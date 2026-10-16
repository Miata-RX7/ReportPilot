"""数据源 CRUD 与连通性测试 API。

安全约束：secret 明文只在本文件的 create/update 入参中短暂出现，
日志、响应、异常消息一律不携带明文或密文。
"""

import logging

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, load_adapter_and_name, require_admin
from app.database import get_db
from app.models.datasource import DataSource
from app.models.user import User
from app.schemas.datasource import (
    ConnectionTestResult,
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    HostGroupRef,
    HostRef,
    ItemRef,
    ItemRowRef,
)
from app.services.adapters import build_adapter
from app.services.adapters.base import (
    AdapterAPIError,
    AdapterAuthError,
    AdapterConnectionError,
    AdapterError,
    AdapterPermissionError,
)
from app.schemas.metric import PieSuitability
from app.services import pie_suitability
from app.services.crypto import decrypt, encrypt, mask_secret

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_or_404(db: AsyncSession, ds_id: int) -> DataSource:
    ds = await db.get(DataSource, ds_id)
    if ds is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "数据源不存在")
    return ds


async def _check_name_conflict(
    db: AsyncSession, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(DataSource.id).where(
        DataSource.name == name, DataSource.is_active.is_(True)
    )
    if exclude_id is not None:
        stmt = stmt.where(DataSource.id != exclude_id)
    if (await db.scalar(stmt)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"数据源名称已存在: {name}")


def _adapter_http_error(exc: AdapterError) -> HTTPException:
    """Adapter 分类异常 -> HTTP 502（对前端统一为"数据源侧问题"）。"""
    if isinstance(exc, AdapterConnectionError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源不可达: {exc}")
    if isinstance(exc, AdapterAuthError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源认证失败: {exc}")
    if isinstance(exc, AdapterPermissionError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源权限不足: {exc}")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"数据源接口错误: {exc}")


@router.post("", response_model=DataSourceRead, status_code=status.HTTP_201_CREATED)
async def create_datasource(
    payload: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> DataSource:
    await _check_name_conflict(db, payload.name)
    ds = DataSource(
        name=payload.name,
        type=payload.type,
        config_json=payload.config.model_dump(),
        secret_encrypted=encrypt(payload.secret),
        secret_hint=mask_secret(payload.secret),
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    logger.info("数据源已创建 id=%d name=%s type=%s", ds.id, ds.name, ds.type)
    return ds


@router.get("", response_model=list[DataSourceRead])
async def list_datasources(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    include_inactive: bool = Query(
        False, description="包含已软删除的数据源（仅排查用）"
    ),
) -> list[DataSource]:
    """列出数据源，**默认只返回启用中的**。

    删除是软删除（``is_active=False``），但列表必须把它藏起来，否则会出现
    「点了删除、提示成功、刷新后那行还在」—— 用户完全无法把它删掉，
    因为界面上也没有「恢复」入口。

    这与重名校验口径一致：``_check_name_conflict`` 只在 ``is_active=True``
    范围内查重，即软删除的行被当作「已不存在」，同名可以直接重建。
    真要查历史再用 ``include_inactive=true``。
    """
    stmt = select(DataSource).order_by(DataSource.id)
    if not include_inactive:
        stmt = stmt.where(DataSource.is_active.is_(True))
    result = await db.scalars(stmt)
    return list(result.all())


@router.get("/{ds_id}", response_model=DataSourceRead)
async def get_datasource(
    ds_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DataSource:
    return await _get_or_404(db, ds_id)


@router.put("/{ds_id}", response_model=DataSourceRead)
async def update_datasource(
    ds_id: int,
    payload: DataSourceUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> DataSource:
    ds = await _get_or_404(db, ds_id)
    if payload.name is not None and payload.name != ds.name:
        await _check_name_conflict(db, payload.name, exclude_id=ds.id)
        ds.name = payload.name
    if payload.config is not None:
        ds.config_json = payload.config.model_dump()
    if payload.is_active is not None:
        ds.is_active = payload.is_active
    if payload.secret:  # None 或 "" -> 保留原密文
        ds.secret_encrypted = encrypt(payload.secret)
        ds.secret_hint = mask_secret(payload.secret)
    await db.commit()
    await db.refresh(ds)
    logger.info("数据源已更新 id=%d", ds.id)
    return ds


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_datasource(
    ds_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Response:
    ds = await _get_or_404(db, ds_id)
    ds.is_active = False  # 软删除
    await db.commit()
    logger.info("数据源已软删除 id=%d", ds.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


MAX_HOSTS_LIMIT = 500
MAX_ITEMS_LIMIT = 2000


def _parse_id_list(raw: str | None, param: str) -> list[str]:
    """解析逗号分隔的 id 列表；为空则 400（两级下拉都必须先选上一级）。"""
    values = [v.strip() for v in (raw or "").split(",")]
    values = [v for v in values if v]
    if not values:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"缺少必需参数 {param}（逗号分隔多个值）"
        )
    return values


def _to_group_refs(rows: list[dict]) -> list[HostGroupRef]:
    return [
        HostGroupRef(
            group_id=str(r["group_id"]),
            name=r["name"],
            instance_num=r.get("instance_num"),
            template_num=r.get("template_num"),
        )
        for r in rows
    ]


def _to_host_refs(rows: list[dict]) -> list[HostRef]:
    return [
        HostRef(
            host_id=str(r["host_id"]),
            name=r["name"],
            groups=_to_group_refs(r.get("groups") or []),
        )
        for r in rows
    ]


def _to_item_refs(rows: list[dict]) -> list[ItemRef]:
    """把 adapter 的监控项映射成ItemRef。

    AIOps 独有的键（``aiops_*``）只在存在时透传，配合端点的
    ``response_model_exclude_none=True``，**Zabbix 的响应体逐字不变**。
    """
    return [
        ItemRef(
            item_id=str(r["item_id"]),
            name=r["name"],
            key=r["key"],
            host_id=str(r["host_id"]),
            host=r.get("host", ""),
            value_type=int(r["value_type"]),
            units=r.get("units", ""),
            aiops_data_type=r.get("aiops_data_type"),
            aiops_columns=r.get("aiops_columns") or None,
            aiops_template_id=r.get("aiops_template_id"),
            aiops_template_name=r.get("aiops_template_name") or None,
            aiops_cycle=r.get("aiops_cycle"),
        )
        for r in rows
    ]


@router.get("/{ds_id}/host-groups", response_model=list[HostGroupRef], response_model_exclude_none=True)
async def list_ds_host_groups(
    ds_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[HostGroupRef]:
    """主机组下拉（级联第一级）。"""
    await _get_or_404(db, ds_id)
    adapter, _ = await load_adapter_and_name(db, ds_id)
    try:
        return _to_group_refs(await adapter.list_host_groups())
    except AdapterError as exc:
        raise _adapter_http_error(exc) from None


@router.get("/{ds_id}/hosts", response_model=list[HostRef], response_model_exclude_none=True)
async def list_ds_hosts(
    ds_id: int,
    group_ids: str | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[HostRef]:
    """主机 / 实例下拉（级联第二级）：group_ids 必传，最多返回 500 条。

    ``q`` 是可选的名称 / IP 子串过滤，**仅 AIOps 生效**（Zabbix adapter 忽略它，
    与升级前行为一致）。注意过滤发生在截断之前 —— 先截 500 再过滤会让搜索
    结果漏掉本该命中的项。
    """
    await _get_or_404(db, ds_id)
    wanted = _parse_id_list(group_ids, "group_ids")
    adapter, _ = await load_adapter_and_name(db, ds_id)
    try:
        rows = await adapter.list_hosts(wanted, q=q or None)
    except AdapterError as exc:
        raise _adapter_http_error(exc) from None
    return _to_host_refs(rows[:MAX_HOSTS_LIMIT])


@router.get("/{ds_id}/items", response_model=list[ItemRef], response_model_exclude_none=True)
async def list_ds_items(
    ds_id: int,
    host_ids: str | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ItemRef]:
    """监控项下拉（级联第三级）：host_ids 必传，最多 2000 条，支持 q 子串过滤。"""
    await _get_or_404(db, ds_id)
    wanted = _parse_id_list(host_ids, "host_ids")
    adapter, _ = await load_adapter_and_name(db, ds_id)
    try:
        rows = await adapter.list_items(wanted)
    except AdapterError as exc:
        raise _adapter_http_error(exc) from None

    rows = rows[:MAX_ITEMS_LIMIT]
    keyword = (q or "").strip().lower()
    if keyword:
        rows = [
            r
            for r in rows
            if keyword in str(r.get("name", "")).lower()
            or keyword in str(r.get("key", "")).lower()
        ]
    return _to_item_refs(rows)


@router.post("/{ds_id}/test", response_model=ConnectionTestResult)
async def test_datasource(
    ds_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ConnectionTestResult:
    ds = await _get_or_404(db, ds_id)
    if not ds.secret_encrypted:
        return ConnectionTestResult(success=False, message="该数据源未配置密钥")
    try:
        secret = decrypt(ds.secret_encrypted)
    except ValueError:
        return ConnectionTestResult(
            success=False, message="密钥解密失败，请重新配置数据源密钥"
        )

    try:
        adapter = build_adapter(ds.type, ds.config_json, secret)
    except ValueError as exc:
        return ConnectionTestResult(success=False, message=str(exc))

    try:
        await adapter.test_connection()
        return ConnectionTestResult(success=True, message="连接成功")
    except AdapterConnectionError as exc:
        return ConnectionTestResult(success=False, message=f"URL 不可达: {exc}")
    except AdapterAuthError as exc:
        return ConnectionTestResult(success=False, message=f"认证失败: {exc}")
    except AdapterPermissionError as exc:
        return ConnectionTestResult(success=False, message=f"权限不足: {exc}")
    except AdapterAPIError as exc:
        return ConnectionTestResult(success=False, message=f"接口错误: {exc}")
    except NotImplementedError as exc:
        return ConnectionTestResult(success=False, message=str(exc))
    except AdapterError as exc:
        return ConnectionTestResult(success=False, message=f"测试失败: {exc}")
    except Exception:
        logger.exception("数据源连通性测试发生未预期错误 id=%d", ds.id)
        return ConnectionTestResult(success=False, message="测试发生内部错误")


class PieSuitabilityRequest(BaseModel):
    """pie 适用性判定入参（与指标取数范围口径一致）。"""

    host_group_ids: list[str] = Field(min_length=1)
    host_ids: list[str] = Field(min_length=1)
    item_id: str = Field(min_length=1)


@router.post("/{ds_id}/analyze-pie-suitability", response_model=PieSuitability)
async def analyze_pie_suitability(
    ds_id: int,
    payload: PieSuitabilityRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PieSuitability:
    """判断某监控项是否适合做 UP/DOWN 占比（pie 指标配置前的引导）。

    7 天窗口 + 5 秒超时；超时返回 suitable="unknown"，不硬失败。
    AIOps 没有Zabbix 那种 up/down 值域语义，直接 422 拒绝。
    """
    ds = await _get_or_404(db, ds_id)
    if ds.type != "zabbix":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "pie 类型仅支持 Zabbix 数据源（AIOps 无 up/down 值域语义）",
        )
    adapter, _ = await load_adapter_and_name(db, ds_id)
    try:
        item_id = int(payload.item_id)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"item_id 必须是数字: {payload.item_id!r}",
        ) from None
    return await pie_suitability.analyze_pie_suitability(adapter, item_id)


@router.get(
    "/{ds_id}/item-rows",
    response_model=list[ItemRowRef],
    response_model_exclude_none=True,
)
async def list_ds_item_rows(
    ds_id: int,
    instance_id: str = Query(..., description="AIOps 实例 id"),
    item_id: str = Query(..., description="AIOps list 型监控项 id"),
    column_id: str = Query(..., description="列 id（用于过滤无数据的行）"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ItemRowRef]:
    """列出 list 型监控项的主键行（AIOps 级联第 4 级）。

    只返回**该列当前值非空**的行：实测「当前值非空」⟺「有历史」，
    过滤掉空行可以避免用户选到「选了没数据」的项。
    """
    ds = await _get_or_404(db, ds_id)
    if ds.type != "aiops":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "该端点仅适用于 AIOps 数据源",
        )
    adapter, _ = await load_adapter_and_name(db, ds_id)
    try:
        rows = await adapter.list_item_rows(
            instance_id=instance_id,
            item_id=item_id,
            column_id=column_id,
        )
    except AdapterError as exc:
        raise _adapter_http_error(exc) from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"参数非法: {exc}"
        ) from None
    return [
        ItemRowRef(
            row_key=str(r["row_key"]),
            current_value=str(r.get("current_value") or ""),
            as_of=int(r.get("as_of") or 0),
        )
        for r in rows
    ]
