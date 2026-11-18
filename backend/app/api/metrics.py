"""指标定义 CRUD 与试算 API。"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import String, bindparam, cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, load_adapter_and_name, require_admin
from app.services import calculated_metrics, ownership
from app.database import get_db
from app.models.datasource import DataSource
from app.models.metric import MetricDefinition
from app.models.template import TemplateVersion, WordTemplate
from app.models.user import User
from app.schemas.metric import (
    ComputeRequest,
    MetricCreate,
    MetricDefinitionRead,
    MetricDetail,
    MetricFact,
    MetricListItem,
    MetricPage,
    MetricUpdate,
    QueryConfigResolved,
    ResolvedGroup,
    ResolvedHost,
    ResolvedItem,
    coerce_unit_config,
)
from app.services.adapters.base import (
    AdapterAPIError,
    AdapterAuthError,
    AdapterConnectionError,
    AdapterPermissionError,
)
from app.services.calc_engine import MetricComputeError, compute_metric

logger = logging.getLogger(__name__)

#: 列表页名称解析的最大并发外部调用数。
#: 上游是运维监控系统，打太狠会被限流或拖慢（Zabbix 的 host.get 在大实例上
#: 本来就要几百毫秒）；4 足以把「两套数据源串行」变成「基本等于最慢那次」。
_RESOLVE_CONCURRENCY = 4

router = APIRouter()


async def _owner_name_map(
    db: AsyncSession, owner_ids: set[int]
) -> dict[int, str]:
    """id -> 用户名。用于「创建人」列（仅 admin 可见）。"""
    if not owner_ids:
        return {}
    rows = await db.execute(
        select(User.id, User.username).where(User.id.in_(owner_ids))
    )
    return {uid: uname for uid, uname in rows.all()}


async def _get_or_404(db: AsyncSession, metric_id: int) -> MetricDefinition:
    metric = await db.get(MetricDefinition, metric_id)
    if metric is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "指标不存在")
    return metric


async def _check_name_conflict(
    db: AsyncSession,
    name: str,
    owner_id: int | None,
    exclude_id: int | None = None,
) -> None:
    """同一创建人内不能重名；**不同创建人可以同名**（2026-11-17）。

    改之前是全局唯一 —— 但指标按创建人隔离可见，于是普通用户撞到别人的
    同名指标时只会看到一句「指标名称已存在」，而那个指标他根本看不到。
    现在唯一性与可见性对齐：普通用户只撞得到自己建过的名字。

    DB 层由 ``uq_metric_owner_name_active`` 部分唯一索引兜底
    （``WHERE is_active = true``，作用域 ``(owner_id, name)``）。
    """
    stmt = select(MetricDefinition.id).where(
        MetricDefinition.name == name,
        MetricDefinition.owner_id == owner_id,
        MetricDefinition.is_active.is_(True),
    )
    if exclude_id is not None:
        stmt = stmt.where(MetricDefinition.id != exclude_id)
    if (await db.scalar(stmt)) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"您已创建过同名指标：{name}（指标名在同一创建人内唯一）",
        )


async def _check_datasource(db: AsyncSession, datasource_id: int) -> None:
    ds = await db.get(DataSource, datasource_id)
    if ds is None or not ds.is_active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "数据源不存在或已停用"
        )


@router.post("", response_model=MetricDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_metric(
    payload: MetricCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MetricDefinition:
    """创建指标。payload 按 metric_type 做 discriminated union 精确校验。

    2026-11-11：创建权限从「仅 admin」放开为「所有登录用户」，指标归属创建人
    （owner_id）。改名的人仍只有 owner 本人和 admin。
    """
    if payload.data_source_id is not None:
        await _check_datasource(db, payload.data_source_id)
    if payload.metric_type == "calculated":
        try:
            await calculated_metrics.validate_calculated_config(
                db,
                query_config=payload.query_config.model_dump(),
                data_source_id=payload.data_source_id,
                owner_id=current_user.id,
            )
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
            ) from None
    await _check_name_conflict(db, payload.name, current_user.id)
    calc_formula = getattr(payload, "calc_formula", None)
    metric = MetricDefinition(
        owner_id=current_user.id,
        name=payload.name,
        data_source_id=payload.data_source_id,
        metric_type=payload.metric_type,
        query_config=payload.query_config.model_dump(),
        type_config=payload.type_config.model_dump(),
        calc_formula=(calc_formula.model_dump() if calc_formula is not None else {}),
        unit=payload.unit,
        display_precision=payload.display_precision,
        tags=payload.tags,
        unit_config=payload.unit_config.to_storage() if payload.unit_config else None,
    )
    db.add(metric)
    await db.commit()
    await db.refresh(metric)
    logger.info(
        "指标已创建 id=%d name=%s type=%s tags=%s",
        metric.id,
        metric.name,
        metric.metric_type,
        metric.tags,
    )
    return metric


def _dialect_name(db: AsyncSession) -> str:
    """取当前方言名（AsyncSession.bind 可能为 None，回退 get_bind）。"""
    bind = db.bind
    if bind is None:
        try:
            bind = db.get_bind()
        except Exception:  # pragma: no cover - 防御
            return ""
    return getattr(getattr(bind, "dialect", None), "name", "")


def _tag_filter(stmt, db: AsyncSession, tag: str, idx: int = 0):
    """按标签精确过滤。

    PostgreSQL 走 JSONB 包含查询（tags @> '["核心"]'::jsonb），由 GIN 索引支撑。
    这里显式指定 bindparam 类型为 JSONB：JSONVariant 是 with_variant 类型，
    直接用 .contains() 会让 asyncpg 收到非法的 json 文本。
    其他方言（如单测 SQLite）没有 JSONB，退化为文本 LIKE；注意 SQLAlchemy 默认
    JSON 序列化会把中文写成 \\uXXXX，故同时匹配转义形式。
    """
    if _dialect_name(db) == "postgresql":
        return stmt.where(
            MetricDefinition.tags.op("@>")(
                bindparam(f"tag_{idx}", value=[tag], type_=JSONB)
            )
        )
    column = cast(MetricDefinition.tags, String)
    escaped = json.dumps(tag, ensure_ascii=True)[1:-1]
    return stmt.where(
        column.like(f'%"{tag}"%') | column.like(f'%"{escaped}"%')
    )


def _resolved_item(key: str, got: dict[str, Any]) -> ResolvedItem:
    """单个取数对象-> ResolvedItem。

    AIOps 的取数对象是定位串（可能较长），未知时截断到 40 字符，避免列表页被刷屏。
    """
    return ResolvedItem(
        id=key,
        name=str(got.get("name") or f"未知取数对象 (id: {key[:40]})"),
        host_id=str(got.get("host_id") or ""),
        host=str(got.get("host") or ""),
    )


#: 名称回填的整体超时（秒）—— 超时则放弃回填，列表照常返回
_RESOLVE_TIMEOUT_SECONDS = 8.0


async def _resolve_many(
    db: AsyncSession, metrics: list[MetricDefinition]
) -> dict[int, QueryConfigResolved]:
    """批量把 query_config 里的 id 解析为名称。

    按数据源聚合，与指标条数无关；任一数据源失败时该数据源下全部降级为不解析。

    **范围与取数对象的解析统一委托 ``adapter.resolve_scoped_items``**：
    Zabbix 的取数对象是纯数字 item_id，AIOps 的是四元组定位串
    （``n:``/``l:`` 前缀，见 services/aiops_keys.py），两者的「存在性校验 +
    名称回填」方式完全不同，写在这里分支会让本函数变成数据源方言的堆积处。
    上级 / 下级字段名同样由 ``adapter.scope_kind`` 决定
    （Zabbix 主机组+主机，AIOps 模型+实例）。
    """
    by_ds: dict[int, list[MetricDefinition]] = {}
    for m in metrics:
        # time 类型不取数（data_source_id 为空），没有需要解析的 id
        if m.data_source_id is None:
            continue
        by_ds.setdefault(m.data_source_id, []).append(m)

    out: dict[int, QueryConfigResolved] = {}

    async def one_datasource(ds_id: int, group_metrics: list[MetricDefinition]) -> None:
        """解析单个数据源下的全部指标，结果写进 out（闭包共享）。

        抛出的异常在外层统一捕获并降级 —— 单个数据源不可用不该让其他
        数据源的指标连名称都显示不出来。
        """
        adapter, _ = await load_adapter_and_name(db, ds_id)
        is_aiops = getattr(adapter, "scope_kind", "host") == "model_instance"
        upper_key = "model_ids" if is_aiops else "host_group_ids"
        lower_key = "instance_ids" if is_aiops else "host_ids"
        upper_label = "模型" if is_aiops else "主机组"
        lower_label = "实例" if is_aiops else "主机"

        # 按「**上级集合**」分桶：同一主机组/模型下的指标合并成**一次**
        # resolve_scoped_items。
        #
        # 早先按「精确下级主机集合」分桶，看着粒度更细、返回更少，实测反而更慢：
        # 指标组合千差万别 -> 桶数 ~= 指标数 -> 每个桶一次 list_hosts + list_items，
        # 而 list_items 的缓存 key 含 host_ids 列表（不同桶不共享）-> 全部回源。
        # 实测 102 个指标（含停用）要 89 秒（2026-11-18）。
        #
        # 改成按上级分桶后，RPC 数与指标数**无关**（同组只打一次），
        # list_items 一次拿回该组全部主机的 item，本地再匹配 keys。
        # 这里只做名称回填、不承担配置校验（真正的取数校验在 calc_engine 里），
        # 放宽一格换取 RPC 数与指标数无关。
        buckets: dict[tuple[str, ...], dict[str, list[str]]] = {}
        per_metric: dict[int, tuple[list[str], list[str], list[str]]] = {}
        for m in group_metrics:
            qc = m.query_config or {}
            gids = [str(x) for x in (qc.get(upper_key) or [])]
            hids = [str(x) for x in (qc.get(lower_key) or [])]
            if not gids or not hids:
                continue
            # 取数对象：scalar/pie/text 用 item_ids，curve 用 series
            keys = [str(x) for x in (qc.get("item_ids") or [])]
            for raw in qc.get("series") or []:
                if isinstance(raw, dict) and raw.get("item_id"):
                    keys.append(str(raw["item_id"]))
            keys = list(dict.fromkeys(keys))  # 去重且保序
            per_metric[m.id] = (gids, hids, keys)
            if not keys:
                continue
            # 桶 key = 上级（主机组/模型）集合 —— 不是精确的主机集合（见上方注释）
            slot = buckets.setdefault(tuple(sorted(gids)), {"upper": [], "keys": [], "lower": []})
            for g in gids:
                if g not in slot["upper"]:
                    slot["upper"].append(g)
            for h in hids:
                if h not in slot["lower"]:
                    slot["lower"].append(h)
            for k in keys:
                if k not in slot["keys"]:
                    slot["keys"].append(k)
        if not buckets:
            return

        # 上级名称与各桶的取数对象**并发**取：它们互不依赖，串行等待是纯粹的浪费。
        # 整体套一个 Semaphore，避免数据源与桶数很多时瞬间把上游打满。
        async def do_bucket(_key: tuple[str, ...], slot: dict[str, list[str]]):
            # 下级取桶内所有指标的主机并集（一次拉回，本地再按 keys 匹配）
            return await adapter.resolve_scoped_items(
                upper_ids=slot["upper"], lower_ids=slot["lower"], keys=slot["keys"]
            )

        sem = asyncio.Semaphore(_RESOLVE_CONCURRENCY)

        async def guarded(coro):
            async with sem:
                return await coro

        # **整体超时保护**（2026-11-18）。
        #
        # 名称回填只是「锦上添花」，不该把列表页拖死。Zabbix 不可达时适配器
        # 会一直等到自己的 connect timeout（数据源里配的 30s）才返回，
        # guarded 只吞异常、并不加速 —— 于是列表页要干等 30s。
        #
        # 这里给解析整体加一个短超时：超时就**放弃名称回填**（名称列留空），
        # 列表照常秒回。取数（calc_engine）不受影响，仍用适配器自己的超时。
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    guarded(adapter.list_host_groups()),
                    *(guarded(do_bucket(hids, slot)) for hids, slot in buckets.items()),
                ),
                timeout=_RESOLVE_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning(
                "名称回填超时或失败（%.0fs），本次不解析名称：%s",
                _RESOLVE_TIMEOUT_SECONDS,
                type(exc).__name__,
            )
            return {}
        groups = results[0]
        gmap = {str(g["group_id"]): g["name"] for g in groups}

        # 定位串 -> 已解析元信息；host_id -> 下级名称（供 hosts 回显）
        resolved_map: dict[str, dict[str, Any]] = {}
        hmap: dict[str, str] = {}
        for slot, resolved in zip(buckets.values(), results[1:], strict=True):
            for key, got in zip(slot["keys"], resolved, strict=False):
                got = got or {}
                resolved_map[key] = got
                if got.get("host_id") and got.get("host"):
                    hmap.setdefault(str(got["host_id"]), str(got["host"]))

        for m in group_metrics:
            found = per_metric.get(m.id)
            if found is None:
                continue
            gids, hids, keys = found
            out[m.id] = QueryConfigResolved(
                host_groups=[
                    ResolvedGroup(
                        id=g, name=gmap.get(g, f"未知{upper_label} (id: {g})")
                    )
                    for g in gids
                ],
                hosts=[
                    ResolvedHost(
                        id=h,
                        name=hmap.get(h) or f"未知{lower_label} (id: {h})",
                    )
                    for h in hids
                ],
                items=[_resolved_item(i, resolved_map.get(i) or {}) for i in keys],
            )

    # 数据源之间并发：它们各自独立（不同 adapter、不同外部系统），
    # 串行等待会让总耗时等于各数据源耗时之和（实测两套数据源串起来 2.28 秒）。
    tasks = [one_datasource(ds_id, ms) for ds_id, ms in by_ds.items()]
    settled = await asyncio.gather(*tasks, return_exceptions=True)
    for ds_id, r in zip(by_ds, settled, strict=True):
        if isinstance(r, BaseException):
            logger.warning(
                "数据源 %d 取数配置名称解析失败，降级为仅 id", ds_id, exc_info=r
            )
    return out


def _to_read(metric: MetricDefinition) -> MetricDefinitionRead:
    return MetricDefinitionRead(
        id=metric.id,
        name=metric.name,
        data_source_id=metric.data_source_id,
        metric_type=metric.metric_type or "scalar",
        query_config=metric.query_config,
        type_config=metric.type_config or {},
        calc_formula=metric.calc_formula,
        unit=metric.unit,
        display_precision=metric.display_precision,
        tags=metric.tags or [],
        is_active=metric.is_active,
        created_at=metric.created_at,
        unit_config=metric.unit_config,
    )


def _stored_unit_config(raw: dict | None) -> dict | None:
    """落库配置喂回严格 schema 前的宽容归一（非法 -> None = 不换算）。"""
    cfg = coerce_unit_config(raw)
    return cfg.to_storage() if cfg is not None else None


def _validate_stored_config(metric: MetricDefinition) -> None:
    """用分类型 schema 复校落库后的 (type, query_config, type_config) 组合。

    更新接口按字段局部提交（可能只改其中一个），落库后统一按对应类型的
    入参模型复校，避免出现 pie 却带 series 之类的错配。
    """
    payload = {
        "name": metric.name,
        "data_source_id": metric.data_source_id,
        "unit": metric.unit,
        "display_precision": metric.display_precision,
        "tags": metric.tags or [],
        "metric_type": metric.metric_type or "scalar",
        "query_config": metric.query_config or {},
        "type_config": metric.type_config or {},
        "calc_formula": metric.calc_formula or {},
        # 存量脏配置归一后再复校，避免它把「只改标签」这类更新连坐拦下
        "unit_config": _stored_unit_config(metric.unit_config),
    }
    try:
        TypeAdapter(MetricCreate).validate_python(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{loc}: {first.get('msg')}" if loc else str(first.get("msg")),
        ) from None


@router.get("", response_model=list[MetricListItem])
async def list_metrics(
    include_inactive: bool = False,
    tag: list[str] = Query(default=[]),
    include_ids: list[int] = Query(
        default=[],
        description="强制包含这些指标（模板绑定页用：已绑定但当前用户无权的指标"
        "也要能显示，否则保存绑定时会被误删）",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MetricListItem]:
    """列表：include_inactive 控制软删除；tag 可重复传，多个标签按 AND 精确匹配。

    **可见性（2026-11-11）**：admin 见全部；普通用户只见**自己创建的**
    （owner_id = 自己）；公共指标（owner_id IS NULL）所有人可见只读。
    include_ids 里的指标**无视可见性**强制返回 —— 模板绑定页要靠它显示
    「别人创建的、已绑定在本模板上的指标」，否则用户看不到就会在保存时误删绑定。

    附带 query_config_resolved（名称解析），供列表直接展示主机/监控项名称。
    """
    stmt = select(MetricDefinition).order_by(MetricDefinition.id)
    if not include_inactive:
        stmt = stmt.where(MetricDefinition.is_active.is_(True))
    for i, t in enumerate(tag):
        cleaned = t.strip()
        if cleaned:
            stmt = _tag_filter(stmt, db, cleaned, i)
    metrics = list((await db.scalars(stmt)).all())
    if include_ids:
        extra = list(
            (
                await db.scalars(
                    select(MetricDefinition).where(
                        MetricDefinition.id.in_(include_ids)
                    )
                )
            ).all()
        )
        known = {m.id for m in metrics}
        metrics.extend(m for m in extra if m.id not in known)
    visible = [m for m in metrics if ownership.metric_visible_to(m, current_user)]
    resolved = await _resolve_many(db, visible)
    owner_names = await _owner_name_map(db, {m.owner_id for m in visible if m.owner_id})
    # 计算型指标的失效依赖（2026-11-17）—— 列表上打 ⚠ 标记用
    from app.services.metric_deps import broken_dependencies_of

    broken = await broken_dependencies_of(db, visible)
    return [
        MetricListItem(
            **_to_read(m).model_dump(),
            query_config_resolved=resolved.get(m.id),
            owner_id=m.owner_id,
            # 只有 admin 能看到创建人 —— 在后端就掐掉，不靠前端隐藏
            owner_name=owner_names.get(m.owner_id) if ownership.is_admin(current_user) else None,
            # 计算型指标：依赖里已失效的那些（2026-11-17）
            broken_deps=broken.get(m.id, []),
        )
        for m in visible
    ]


@router.get("/paged", response_model=MetricPage)
async def list_metrics_paged(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=200),
    include_inactive: bool = False,
    q: str = Query(default="", description="名称模糊搜索（大小写不敏感）"),
    status: str = Query(default="active", description="active / inactive / all"),
    tag: list[str] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MetricPage:
    """**分页**列出指标（列表页用）。

    与 ``GET /api/metrics`` 的区别：这里只查当前页、并**只解析当前页**的名称。
    全量版仍保留给「模板绑定页」等需要一次拿到全部可见指标的调用方。

    放在 ``/{metric_id}`` 之前注册 —— 否则 "paged" 会被当成 metric_id。
    """
    stmt = select(MetricDefinition).order_by(MetricDefinition.id)
    # 状态筛选：active / inactive / all（默认 active）
    if status == "active":
        stmt = stmt.where(MetricDefinition.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(MetricDefinition.is_active.is_(False))
    # 名称模糊搜索
    keyword = q.strip()
    if keyword:
        stmt = stmt.where(MetricDefinition.name.ilike(f"%{keyword}%"))
    for i, t in enumerate(tag):
        cleaned = t.strip()
        if cleaned:
            stmt = _tag_filter(stmt, db, cleaned, i)
    all_rows = list((await db.scalars(stmt)).all())
    visible = [m for m in all_rows if ownership.metric_visible_to(m, current_user)]
    total = len(visible)
    start = (page - 1) * page_size
    page_rows = visible[start : start + page_size]
    if not page_rows:
        return MetricPage(items=[], total=total, page=page, page_size=page_size)

    resolved = await _resolve_many(db, page_rows)
    owner_names = await _owner_name_map(
        db, {m.owner_id for m in page_rows if m.owner_id}
    )
    from app.services.metric_deps import broken_dependencies_of

    broken = await broken_dependencies_of(db, page_rows)
    items = [
        MetricListItem(
            **_to_read(m).model_dump(),
            query_config_resolved=resolved.get(m.id),
            owner_id=m.owner_id,
            owner_name=owner_names.get(m.owner_id) if ownership.is_admin(current_user) else None,
            broken_deps=broken.get(m.id, []),
        )
        for m in page_rows
    ]
    return MetricPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{metric_id}", response_model=MetricDetail)
async def get_metric(
    metric_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MetricDetail:
    """指标详情：附带 query_config 的 id -> name 解析结果（编辑页回填用）。

    数据源不可达时 query_config_resolved 为 null（前端降级显示 id）。
    """
    metric = await _get_or_404(db, metric_id)
    ownership.ensure_metric_visible(metric, current_user)
    resolved = (await _resolve_many(db, [metric])).get(metric.id)
    owner_names = await _owner_name_map(
        db, {metric.owner_id} if metric.owner_id else set()
    )
    from app.services.metric_deps import broken_dependencies_of

    broken = await broken_dependencies_of(db, [metric])
    return MetricDetail(
        **_to_read(metric).model_dump(),
        query_config_resolved=resolved,
        owner_id=metric.owner_id,
        owner_name=owner_names.get(metric.owner_id)
        if ownership.is_admin(current_user)
        else None,
        broken_deps=broken.get(metric.id, []),
    )


@router.put("/{metric_id}", response_model=MetricDefinitionRead)
async def update_metric(
    metric_id: int,
    payload: MetricUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MetricDefinition:
    metric = await _get_or_404(db, metric_id)
    # **先查可见性再查可编辑性**：看不到的返回 404（不泄露「这个 id 存在」），
    # 看得见但不是自己的才返回 403。
    ownership.ensure_metric_visible(metric, current_user)
    ownership.ensure_metric_editable(metric, current_user)
    new_type = payload.metric_type or metric.metric_type
    new_ds = (
        payload.data_source_id
        if payload.data_source_id is not None
        else metric.data_source_id
    )
    new_query = (
        payload.query_config
        if payload.query_config is not None
        else (metric.query_config or {})
    )
    if new_type == "calculated":
        try:
            await calculated_metrics.validate_calculated_config(
                db,
                query_config=new_query,
                data_source_id=new_ds,
                owner_id=metric.owner_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
            ) from None
    if payload.name is not None and payload.name != metric.name:
        await _check_name_conflict(
            db, payload.name, metric.owner_id, exclude_id=metric.id
        )
        metric.name = payload.name
    if payload.data_source_id is not None:
        await _check_datasource(db, payload.data_source_id)
        metric.data_source_id = payload.data_source_id
    if payload.metric_type is not None:
        metric.metric_type = payload.metric_type
    if payload.query_config is not None:
        metric.query_config = payload.query_config
    if payload.type_config is not None:
        metric.type_config = payload.type_config
    if payload.calc_formula is not None:
        metric.calc_formula = payload.calc_formula
    if payload.unit is not None:
        metric.unit = payload.unit
    if payload.display_precision is not None:
        metric.display_precision = payload.display_precision
    if payload.tags is not None:
        metric.tags = payload.tags
    if payload.unit_config is not None:
        # {"mode": "none"} -> 落库 NULL（= 不换算），与「未配置」语义一致
        metric.unit_config = payload.unit_config.to_storage()
    _validate_stored_config(metric)
    await db.commit()
    await db.refresh(metric)
    logger.info("指标已更新 id=%d type=%s", metric.id, metric.metric_type)
    return metric


@router.delete("/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metric(
    metric_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    metric = await _get_or_404(db, metric_id)
    # **先查可见性再查可编辑性**：看不到的返回 404（不泄露「这个 id 存在」），
    # 看得见但不是自己的才返回 403。
    ownership.ensure_metric_visible(metric, current_user)
    ownership.ensure_metric_editable(metric, current_user)

    # **删除指标会连带删除引用它的计算型指标**（2026-11-17）。
    #
    # 计算型指标的算式靠 metric_id 引用别的指标。被引用的指标删了，
    # 计算型指标就再也算不出来 -> 绑了它的模板生成报告必然失败，而且用户
    # 只看到「报告生成失败」，不知道根因是哪个指标被删了。
    #
    # 所以这里**级联删除**这些计算型指标（用户意图是「不再用这个指标了」，
    # 那依赖它的算式也就没有意义了）。删除前前端会先查
    # ``GET /metrics/{id}/referenced-by`` 列出关系让用户确认。
    #
    # 引用只能发生在**同一 owner 内**（validate_calculated_config 有校验），
    # 所以级联删除不会越权删别人的东西。
    from app.services.metric_deps import dependents_of

    victims = await dependents_of(db, metric_id)
    for victim, _var in victims:
        logger.info(
            "指标删除 -> 级联删除计算型指标 metric_id=%d 连带 calc_id=%d name=%s",
            metric_id,
            victim.id,
            victim.name,
        )

    # **删除指标要连带解绑模板**（2026-11-13）。
    # 不解绑的话，模板里会留下指向已停用指标的绑定 -> 报告生成时直接失败
    # （「变量 xxx 绑定的指标不存在或已停用」），而且用户在绑定页根本没法
    # 重新给这个变量选指标（已删除的指标不该出现在候选里）。
    from app.models.template import TemplateBinding

    bound_rows = (
        await db.scalars(
            select(TemplateBinding).where(
                TemplateBinding.binding_type == "metric",
                TemplateBinding.binding_ref_id == metric_id,
            )
        )
    ).all()
    for b in bound_rows:
        await db.delete(b)
    if bound_rows:
        tpl_names = await _template_names_of_bindings(db, bound_rows)
        logger.info(
            "指标删除 -> 解除模板绑定 metric_id=%d 绑定数=%d 涉及模板=%s",
            metric_id,
            len(bound_rows),
            tpl_names,
        )

    # ---- 级联：删掉引用它的计算型指标（连同它们的模板绑定）----
    # 计算型指标可能被模板绑着（实测「上月中断时长」就绑在模板上）——
    # 不解绑的话模板会留下悬空绑定 -> 报告生成报「绑定的指标不存在或已停用」。
    if victims:
        victim_ids = [v.id for v, _ in victims]
        victim_bindings = (
            await db.scalars(
                select(TemplateBinding).where(
                    TemplateBinding.binding_type == "metric",
                    TemplateBinding.binding_ref_id.in_(victim_ids),
                )
            )
        ).all()
        for b in victim_bindings:
            await db.delete(b)
        for victim, _var in victims:
            victim.is_active = False  # 软删除
        logger.info(
            "级联删除计算型指标 %d 个，解除其模板绑定 %d 处",
            len(victims),
            len(victim_bindings),
        )

    metric.is_active = False  # 软删除
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _template_names_of_bindings(
    db: AsyncSession, bindings: list[Any]
) -> list[str]:
    """绑定涉及的模板名（去重）—— 写日志用。

    注意：``template_bindings`` 挂的是 **version_id**，没有 template_id，
    要经 ``template_versions`` 才能拿到模板。
    """
    version_ids = {b.version_id for b in bindings}
    if not version_ids:
        return []
    rows = (
        await db.execute(
            select(WordTemplate.name)
            .join(TemplateVersion, TemplateVersion.template_id == WordTemplate.id)
            .where(TemplateVersion.id.in_(version_ids))
        )
    ).scalars().all()
    return sorted(set(rows))


@router.get("/{metric_id}/referenced-by")
async def list_metric_referenced_by(
    metric_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """**哪些计算型指标引用了这个指标**（删除前给用户看关系）。

    删除这个指标时，这些计算型指标会被**一并删除**（它们的算式会失效），
    所以要让用户先看清关系再决定。
    """
    metric = await _get_or_404(db, metric_id)
    ownership.ensure_metric_visible(metric, current_user)
    from app.services.metric_deps import dependents_of

    deps = await dependents_of(db, metric_id)
    return [
        {
            "calc_id": calc.id,
            "calc_name": calc.name,
            "var_name": var_name,
        }
        for calc, var_name in deps
    ]


@router.get("/{metric_id}/template-bindings")
async def list_metric_template_bindings(
    metric_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """该指标被哪些模板的哪些变量绑定（删除前给用户看清楚要解绑什么）。"""
    metric = await _get_or_404(db, metric_id)
    ownership.ensure_metric_visible(metric, current_user)
    from app.models.template import TemplateBinding

    # template_bindings 挂的是 version_id -> 要经 template_versions 关联模板
    rows = (
        await db.execute(
            select(TemplateBinding, WordTemplate)
            .join(
                TemplateVersion,
                TemplateVersion.id == TemplateBinding.version_id,
            )
            .join(WordTemplate, WordTemplate.id == TemplateVersion.template_id)
            .where(
                TemplateBinding.binding_type == "metric",
                TemplateBinding.binding_ref_id == metric_id,
            )
            .order_by(WordTemplate.id)
        )
    ).all()
    return [
        {
            "template_id": tpl.id,
            "template_name": tpl.name,
            "placeholder": b.placeholder,
            "version_id": b.version_id,
        }
        for b, tpl in rows
    ]


@router.post("/{metric_id}/compute", response_model=MetricFact)
async def compute_metric_endpoint(
    metric_id: int,
    payload: ComputeRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> MetricFact:
    """试算：用指定 period 计算指标，返回 MetricFact，不落库。"""
    metric = await _get_or_404(db, metric_id)
    if not metric.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "指标已停用")
    # time 类型与数据源无关，不加载 Adapter（也不该因数据源不可达而失败）
    if metric.data_source_id is None:
        try:
            return await compute_metric(metric, payload.period, None, "当前时间")
        except MetricComputeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
            ) from None
    adapter, source_name = await load_adapter_and_name(db, metric.data_source_id)
    try:
        return await compute_metric(metric, payload.period, adapter, source_name)
    except MetricComputeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from None
    except AdapterConnectionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源不可达: {exc}") from None
    except AdapterAuthError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源认证失败: {exc}") from None
    except AdapterPermissionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源权限不足: {exc}") from None
    except AdapterAPIError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源接口错误: {exc}") from None
