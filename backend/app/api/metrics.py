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
from app.database import get_db
from app.models.datasource import DataSource
from app.models.metric import MetricDefinition
from app.models.user import User
from app.schemas.metric import (
    ComputeRequest,
    MetricCreate,
    MetricDefinitionRead,
    MetricDetail,
    MetricFact,
    MetricListItem,
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


async def _get_or_404(db: AsyncSession, metric_id: int) -> MetricDefinition:
    metric = await db.get(MetricDefinition, metric_id)
    if metric is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "指标不存在")
    return metric


async def _check_name_conflict(
    db: AsyncSession, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(MetricDefinition.id).where(
        MetricDefinition.name == name, MetricDefinition.is_active.is_(True)
    )
    if exclude_id is not None:
        stmt = stmt.where(MetricDefinition.id != exclude_id)
    if (await db.scalar(stmt)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"指标名称已存在: {name}")


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
    _: User = Depends(require_admin),
) -> MetricDefinition:
    """创建指标。payload 按 metric_type 做 discriminated union 精确校验。"""
    if payload.data_source_id is not None:
        await _check_datasource(db, payload.data_source_id)
    await _check_name_conflict(db, payload.name)
    calc_formula = getattr(payload, "calc_formula", None)
    metric = MetricDefinition(
        name=payload.name,
        data_source_id=payload.data_source_id,
        metric_type=payload.metric_type,
        query_config=payload.query_config.model_dump(),
        type_config=payload.type_config.model_dump(),
        calc_formula=(calc_formula.model_dump() if calc_formula is not None else {}),
        unit=payload.unit,
        period_type=payload.period_type,
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

        # 按「下级集合」分桶：同一批主机/实例的指标合并成**一次** resolve_scoped_items。
        # 若放在「每个指标一次」里，N 个指标就是 N×(list_hosts + list_items) 次 RPC，
        # 列表页会明显变慢（曾实测 4.4kB 响应要 2.7 秒）。
        # 上级取桶内所有指标的并集：这里只做名称回填、不承担配置校验
        # （真正的取数校验在 calc_engine 里），放宽一格换取 RPC 数与指标数无关。
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
            slot = buckets.setdefault(tuple(hids), {"upper": [], "keys": []})
            for g in gids:
                if g not in slot["upper"]:
                    slot["upper"].append(g)
            for k in keys:
                if k not in slot["keys"]:
                    slot["keys"].append(k)
        if not buckets:
            return

        # 上级名称与各桶的取数对象**并发**取：它们互不依赖，串行等待是纯粹的浪费。
        # 整体套一个 Semaphore，避免数据源与桶数很多时瞬间把上游打满。
        async def do_bucket(hids: tuple[str, ...], slot: dict[str, list[str]]):
            return await adapter.resolve_scoped_items(
                upper_ids=slot["upper"], lower_ids=list(hids), keys=slot["keys"]
            )

        sem = asyncio.Semaphore(_RESOLVE_CONCURRENCY)

        async def guarded(coro):
            async with sem:
                return await coro

        results = await asyncio.gather(
            guarded(adapter.list_host_groups()),
            *(guarded(do_bucket(hids, slot)) for hids, slot in buckets.items()),
        )
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
        period_type=metric.period_type,
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
        "period_type": metric.period_type,
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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[MetricListItem]:
    """列表：include_inactive 控制软删除；tag 可重复传，多个标签按 AND 精确匹配。

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
    resolved = await _resolve_many(db, metrics)
    return [
        MetricListItem(
            **_to_read(m).model_dump(),
            query_config_resolved=resolved.get(m.id),
        )
        for m in metrics
    ]


@router.get("/{metric_id}", response_model=MetricDetail)
async def get_metric(
    metric_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> MetricDetail:
    """指标详情：附带 query_config 的 id -> name 解析结果（编辑页回填用）。

    数据源不可达时 query_config_resolved 为 null（前端降级显示 id）。
    """
    metric = await _get_or_404(db, metric_id)
    resolved = (await _resolve_many(db, [metric])).get(metric.id)
    return MetricDetail(
        **_to_read(metric).model_dump(), query_config_resolved=resolved
    )


@router.put("/{metric_id}", response_model=MetricDefinitionRead)
async def update_metric(
    metric_id: int,
    payload: MetricUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> MetricDefinition:
    metric = await _get_or_404(db, metric_id)
    if payload.name is not None and payload.name != metric.name:
        await _check_name_conflict(db, payload.name, exclude_id=metric.id)
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
    if payload.period_type is not None:
        metric.period_type = payload.period_type
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
    _: User = Depends(require_admin),
) -> Response:
    metric = await _get_or_404(db, metric_id)
    metric.is_active = False  # 软删除
    await db.commit()
    logger.info("指标已软删除 id=%d", metric.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
