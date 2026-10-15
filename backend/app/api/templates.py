"""模板上传、版本、绑定、授权、预览与在线编辑 API。

权限：属主/admin = manage；被授权 edit = 可编辑文档+绑定；被授权 view = 只读。
"""

import logging
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from werkzeug.utils import secure_filename

from app.api.deps import get_current_user, resolve_user
from app.config import get_settings
from app.database import get_db
from app.models.metric import MetricDefinition
from app.models.template import (
    TemplateBinding,
    TemplateGrant,
    TemplateVersion,
    WordTemplate,
)
from app.models.user import User
from app.schemas.template import (
    BindingRead,
    BindingsResult,
    BindingsUpdate,
    EditorConfig,
    GrantRead,
    GrantsUpdate,
    InsightPreviewRequest,
    InsightPreviewResult,
    PreviewRequest,
    TemplateDetail,
    PlaceholderInfo,
    TemplateListItem,
    TransferRequest,
    VersionRead,
)
from app.services import ai_insight, ai_trace, onlyoffice_service, template_service
from app.services.ai_insight import InsightConfig
from app.services.adapters.base import (
    AdapterAPIError,
    AdapterAuthError,
    AdapterConnectionError,
    AdapterPermissionError,
)
from app.services.docx_parser import (
    TemplateSyntaxError,
    parse_template,
    placeholder_alone_map,
)
from app.services.preview_service import (
    PreviewError,
    collect_facts,
    compute_template_facts,
    render_preview,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 图表类型指标：Word 内以图片渲染，要求变量独占段落
CHART_METRIC_TYPES = ("curve", "pie")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DOCX_MAGIC = b"PK\x03\x04"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _sanitize_filename(raw: str) -> str:
    name = secure_filename(raw)
    name = name[:100]
    return name or "template.docx"


async def _read_limited(file: UploadFile) -> bytes:
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf.extend(chunk)
        if len(buf) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "模板文件超过 10MB 限制"
            )
    return bytes(buf)


async def _get_or_404(db: AsyncSession, tpl_id: int) -> WordTemplate:
    tpl = await db.get(WordTemplate, tpl_id)
    if tpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板不存在")
    return tpl


def _version_read(tpl: WordTemplate, version: TemplateVersion) -> VersionRead:
    return VersionRead(
        id=version.id,
        version_no=version.version_no,
        file_name=Path(version.file_path).name,
        placeholder_count=len(version.placeholders_json or []),
        source=version.source,
        is_current=version.id == tpl.current_version_id,
        created_at=version.created_at,
    )


async def _to_detail(
    db: AsyncSession, tpl: WordTemplate, permission: str
) -> TemplateDetail:
    version = await template_service.get_current_version(db, tpl)
    bindings = (
        await template_service.bindings_for_version(db, version.id) if version else []
    )
    bound = {b.placeholder for b in bindings}
    names = list(version.placeholders_json or []) if version else []
    # 独占信息按需从当前版本文件解析（不落库：始终反映文件真实状态）
    alone = placeholder_alone_map(version, names) if version else {}
    owner = await db.get(User, tpl.owner_id) if tpl.owner_id else None
    return TemplateDetail(
        id=tpl.id,
        name=tpl.name,
        is_active=tpl.is_active,
        created_at=tpl.created_at,
        owner_id=tpl.owner_id,
        owner_name=(owner.display_name or owner.username) if owner else None,
        permission=permission,
        current_version=_version_read(tpl, version) if version else None,
        placeholders=[
            PlaceholderInfo(name=n, is_alone_in_paragraph=alone.get(n, False))
            for n in names
        ],
        bindings=[BindingRead.model_validate(b) for b in bindings],
        unbound=[p for p in names if p not in bound],
    )


@router.post("", response_model=TemplateDetail, status_code=status.HTTP_201_CREATED)
async def upload_template(
    name: str = Form(min_length=1, max_length=128),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TemplateDetail:
    raw_filename = file.filename or ""
    if not raw_filename.lower().endswith(".docx"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "仅支持 .docx 模板文件"
        )
    filename = _sanitize_filename(raw_filename)

    data = await _read_limited(file)
    if not data[:4] == DOCX_MAGIC:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "文件不是有效的 docx（magic number 不匹配）"
        )

    conflict = await db.scalar(
        select(WordTemplate.id).where(
            WordTemplate.name == name, WordTemplate.is_active.is_(True)
        )
    )
    if conflict is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"模板名称已存在: {name}")

    storage = Path(get_settings().storage_dir)
    tmp_path = storage / "templates" / f"tmp_{uuid.uuid4().hex}.docx"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(data)
    try:
        try:
            placeholders = parse_template(tmp_path).names
        except TemplateSyntaxError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"message": str(exc), "violations": exc.violations},
            ) from None
        except Exception:
            logger.exception("模板解析失败 name=%s", name)
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "模板文件损坏，无法解析"
            ) from None

        tpl = WordTemplate(name=name, owner_id=current_user.id)
        db.add(tpl)
        await db.flush()
        final_path = tmp_path.parent / f"{tpl.id}_{filename}"
        tmp_path.replace(final_path)
        await template_service.create_version(
            db,
            tpl,
            file_path=str(final_path),
            placeholders=placeholders,
            actor_id=current_user.id,
            source="upload",
        )
        await db.commit()
        await db.refresh(tpl)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    logger.info(
        "模板已上传 id=%d name=%s placeholders=%d owner=%d",
        tpl.id,
        tpl.name,
        len(placeholders),
        tpl.owner_id or 0,
    )
    return await _to_detail(db, tpl, "manage")


@router.get("", response_model=list[TemplateListItem])
async def list_templates(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TemplateListItem]:
    pairs = await template_service.list_accessible(
        db, current_user, include_inactive=include_inactive
    )
    bound_counts = await template_service.count_bindings_by_version(db)
    items: list[TemplateListItem] = []
    for tpl, permission in pairs:
        version = await template_service.get_current_version(db, tpl)
        owner = await db.get(User, tpl.owner_id) if tpl.owner_id else None
        items.append(
            TemplateListItem(
                id=tpl.id,
                name=tpl.name,
                file_name=Path(version.file_path).name if version else "",
                placeholder_count=len(version.placeholders_json or []) if version else 0,
                bound_count=bound_counts.get(version.id, 0) if version else 0,
                is_active=tpl.is_active,
                created_at=tpl.created_at,
                owner_id=tpl.owner_id,
                owner_name=(owner.display_name or owner.username) if owner else None,
                current_version_no=version.version_no if version else None,
                permission=permission,
            )
        )
    return items


@router.get("/{tpl_id}", response_model=TemplateDetail)
async def get_template(
    tpl_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TemplateDetail:
    tpl = await _get_or_404(db, tpl_id)
    permission = await template_service.require_permission(
        db, tpl, current_user, "view"
    )
    return await _to_detail(db, tpl, permission)


@router.get("/{tpl_id}/versions", response_model=list[VersionRead])
async def list_template_versions(
    tpl_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[VersionRead]:
    tpl = await _get_or_404(db, tpl_id)
    await template_service.require_permission(db, tpl, current_user, "view")
    versions = await template_service.list_versions(db, tpl)
    return [_version_read(tpl, v) for v in versions]


@router.post("/{tpl_id}/versions/{version_id}/activate", response_model=VersionRead)
async def activate_version(
    tpl_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VersionRead:
    """回滚：把指定历史版本设为当前版本。"""
    tpl = await _get_or_404(db, tpl_id)
    await template_service.require_permission(db, tpl, current_user, "edit")
    version = await template_service.get_version_or_404(db, tpl, version_id)
    await template_service.set_current_version(db, tpl, version)
    await db.commit()
    await db.refresh(tpl)
    return _version_read(tpl, version)


@router.put("/{tpl_id}/bindings", response_model=BindingsResult)
async def put_bindings(
    tpl_id: int,
    payload: BindingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BindingsResult:
    """全量替换**当前版本**的绑定；传空数组 = 清空。"""
    tpl = await _get_or_404(db, tpl_id)
    await template_service.require_permission(db, tpl, current_user, "edit")
    version = await template_service.get_current_version(db, tpl)
    if version is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板无可用版本")
    placeholder_set = set(version.placeholders_json or [])
    # 图表类型指标必须独占段落 -> 解析当前版本文件拿独占信息
    alone = placeholder_alone_map(version, list(placeholder_set))

    seen: set[str] = set()
    new_bindings: list[TemplateBinding] = []
    for item in payload.bindings:
        if item.placeholder not in placeholder_set:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"变量不存在于模板: {item.placeholder}",
            )
        if item.placeholder in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"同一变量重复绑定: {item.placeholder}",
            )
        seen.add(item.placeholder)
        if item.binding_type == "metric":
            metric = await db.get(MetricDefinition, item.binding_ref_id)
            if metric is None or not metric.is_active:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"变量 {item.placeholder} 绑定的指标不存在或已停用",
                )
            # 阶段 3A：图表类型（curve/pie）可绑定，但 docxtpl 插图要求该
            # 变量独占一个段落，否则 140mm 宽的行内图片会挤在文字中间。
            # 实测 docxtpl 0.20.2 混排也能插图，因此这不是"能不能渲染"的
            # 问题，而是版式不可控 -> 在绑定时快速失败并给出可操作提示。
            if (metric.metric_type or "scalar") in CHART_METRIC_TYPES:
                if not alone.get(item.placeholder, False):
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        f"变量 {item.placeholder} 绑定的指标 {metric.name} 是"
                        f"{metric.metric_type} 图表类型，要求该变量在模板中独占一段"
                        f"（不能与其它文字混排），请修改模板中 {{{{ {item.placeholder} }}}}"
                        "所在段落只保留该变量",
                    )
        new_bindings.append(
            TemplateBinding(
                placeholder=item.placeholder,
                binding_type=item.binding_type,
                binding_ref_id=item.binding_ref_id,
                binding_key=item.binding_key,
                ai_config_json=item.ai_config_for_db(),
            )
        )

    cleared = await template_service.replace_version_bindings(
        db, version.id, new_bindings
    )
    await db.commit()
    for b in new_bindings:
        await db.refresh(b)
    logger.info(
        "模板绑定已更新 template_id=%d version=%d cleared=%d new=%d",
        tpl.id,
        version.version_no,
        cleared,
        len(new_bindings),
    )
    return BindingsResult(
        bindings=[BindingRead.model_validate(b) for b in new_bindings],
        cleared_count=cleared,
    )


@router.get("/{tpl_id}/file")
async def get_template_file(
    tpl_id: int,
    token: str | None = None,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> FileResponse:
    """返回原始 docx。ONLYOFFICE 用签名 token；前端预览用 Bearer。"""
    tpl = await _get_or_404(db, tpl_id)
    payload = onlyoffice_service.verify_token(token) if token else None
    version: TemplateVersion | None = None
    if payload and payload.get("k") == "file" and payload.get("t") == tpl_id:
        version = await db.get(TemplateVersion, int(payload.get("v", 0)))
    else:
        user = await resolve_user(db, authorization)
        await template_service.require_permission(db, tpl, user, "view")
        version = await template_service.get_current_version(db, tpl)

    if version is None or not Path(version.file_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板文件不存在")
    return FileResponse(
        version.file_path,
        media_type=DOCX_MIME,
        filename=f"{tpl.name}.docx",
    )


@router.post("/{tpl_id}/editor", response_model=EditorConfig)
async def open_editor(
    tpl_id: int,
    mode: str = Query(default="edit"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EditorConfig:
    """返回 ONLYOFFICE 配置（mode=edit 编辑 / mode=view 只读查看；未配置 DS 时 enabled=false）。"""
    if mode not in ("edit", "view"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "mode 仅支持 edit/view")
    tpl = await _get_or_404(db, tpl_id)
    if not tpl.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板已停用")
    await template_service.require_permission(
        db, tpl, current_user, "edit" if mode == "edit" else "view"
    )
    settings = get_settings()
    if not settings.onlyoffice_enabled:
        return EditorConfig(enabled=False, docserver_url="", config={})
    version = await template_service.get_current_version(db, tpl)
    if version is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板无可用版本")
    config = onlyoffice_service.build_editor_config(tpl, version, current_user, mode)
    return EditorConfig(
        enabled=True, docserver_url=settings.onlyoffice_url, config=config
    )


@router.post("/{tpl_id}/callback")
async def onlyoffice_callback(
    tpl_id: int,
    request: Request,
    token: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """ONLYOFFICE 保存回调：落盘为新版本，重扫占位符并清理失效绑定。"""
    tpl = await _get_or_404(db, tpl_id)
    payload = onlyoffice_service.verify_token(token)
    if not payload or payload.get("k") != "cb" or payload.get("t") != tpl_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "回调 token 无效")
    body = await request.json()
    if not onlyoffice_service.verify_callback_jwt(body):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "回调签名校验失败")

    st = int(body.get("status", 0))
    if st in onlyoffice_service.STATUS_FORCE_SAVE:
        # 强制保存（autosave/forcesave）：DS 侧已持有最新文档，仅确认即可，
        # 不落盘、不建版本，避免一次编辑产生大量重复版本。
        return {"error": 0}
    if st not in onlyoffice_service.STATUS_SAVE:
        if st in onlyoffice_service.STATUS_ERROR:
            logger.warning("ONLYOFFICE 保存出错 template_id=%d body=%s", tpl_id, body)
        return {"error": 0}

    url = body.get("url")
    if not url:
        return {"error": 1}
    try:
        data = await onlyoffice_service.download_document(url)
    except Exception:
        logger.exception("ONLYOFFICE 文档下载失败 template_id=%d", tpl_id)
        return {"error": 1}

    # 内容与当前版本完全一致 -> 不产生新版本（DS 可能在关闭时重复回调）
    current = await template_service.get_current_version(db, tpl)
    if current is not None and current.file_path:
        cur_path = Path(current.file_path)
        if cur_path.exists() and cur_path.read_bytes() == data:
            logger.info(
                "ONLYOFFICE 内容无变化，跳过新版本 template_id=%d version_no=%s",
                tpl_id,
                current.version_no,
            )
            return {"error": 0}

    storage = Path(get_settings().storage_dir) / "templates"
    storage.mkdir(parents=True, exist_ok=True)
    new_path = storage / f"{tpl_id}_edit_{uuid.uuid4().hex}.docx"
    new_path.write_bytes(data)
    try:
        placeholders = parse_template(new_path).names
    except TemplateSyntaxError as exc:
        new_path.unlink(missing_ok=True)
        logger.warning("ONLYOFFICE 编辑结果语法非法 template_id=%d %s", tpl_id, exc)
        return {"error": 1}
    except Exception:
        new_path.unlink(missing_ok=True)
        logger.exception("ONLYOFFICE 编辑结果解析失败 template_id=%d", tpl_id)
        return {"error": 1}

    actor_id = int(payload.get("u", 0)) or None
    await template_service.create_version(
        db,
        tpl,
        file_path=str(new_path),
        placeholders=placeholders,
        actor_id=actor_id,
        source="edit",
    )
    await db.commit()
    logger.info("ONLYOFFICE 保存为新版本 template_id=%d placeholders=%d", tpl_id, len(placeholders))
    return {"error": 0}


@router.get("/{tpl_id}/grants", response_model=list[GrantRead])
async def list_grants(
    tpl_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[GrantRead]:
    tpl = await _get_or_404(db, tpl_id)
    await template_service.require_permission(db, tpl, current_user, "manage")
    rows = (
        await db.execute(
            select(TemplateGrant, User)
            .join(User, TemplateGrant.user_id == User.id)
            .where(TemplateGrant.template_id == tpl.id)
        )
    ).all()
    return [
        GrantRead(
            user_id=g.user_id,
            username=u.username,
            display_name=u.display_name or u.username,
            permission=g.permission,
        )
        for g, u in rows
    ]


@router.put("/{tpl_id}/grants", response_model=list[GrantRead])
async def put_grants(
    tpl_id: int,
    payload: GrantsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[GrantRead]:
    """全量替换授权列表（属主与 admin 不改动自身的 manage 权限）。"""
    tpl = await _get_or_404(db, tpl_id)
    await template_service.require_permission(db, tpl, current_user, "manage")

    seen: set[int] = set()
    for item in payload.grants:
        if item.user_id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "同一用户重复授权"
            )
        seen.add(item.user_id)
        if item.user_id == tpl.owner_id:
            continue  # 属主天然 manage，忽略
        user = await db.get(User, item.user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"用户不存在或已停用: {item.user_id}"
            )

    existing = list(
        (
            await db.scalars(
                select(TemplateGrant).where(TemplateGrant.template_id == tpl.id)
            )
        ).all()
    )
    for old in existing:
        await db.delete(old)
    await db.flush()
    for item in payload.grants:
        if item.user_id == tpl.owner_id:
            continue
        db.add(
            TemplateGrant(
                template_id=tpl.id, user_id=item.user_id, permission=item.permission
            )
        )
    await db.commit()
    return await list_grants(tpl_id, db, current_user)


@router.post("/{tpl_id}/transfer", response_model=TemplateDetail)
async def transfer_owner(
    tpl_id: int,
    payload: TransferRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TemplateDetail:
    """转移属主（owner/admin 可操作）。"""
    tpl = await _get_or_404(db, tpl_id)
    await template_service.require_permission(db, tpl, current_user, "manage")
    new_owner = await db.get(User, payload.owner_id)
    if new_owner is None or not new_owner.is_active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "目标用户不存在或已停用"
        )
    tpl.owner_id = new_owner.id
    # 移除新属主可能存在的授权（属主天然 manage）
    grant = await db.scalar(
        select(TemplateGrant).where(
            TemplateGrant.template_id == tpl.id,
            TemplateGrant.user_id == new_owner.id,
        )
    )
    if grant is not None:
        await db.delete(grant)
    await db.commit()
    await db.refresh(tpl)
    logger.info("模板属主转移 template_id=%d -> user=%d", tpl.id, new_owner.id)
    return await _to_detail(db, tpl, "manage")


@router.delete("/{tpl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    tpl_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    tpl = await _get_or_404(db, tpl_id)
    await template_service.require_permission(db, tpl, current_user, "manage")
    tpl.is_active = False  # 软删除，磁盘文件保留（Phase 2 再做清理任务）
    await db.commit()
    logger.info("模板已软删除 id=%d", tpl.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{tpl_id}/preview")
async def preview_template(
    tpl_id: int,
    payload: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    tpl = await _get_or_404(db, tpl_id)
    if not tpl.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板已停用")
    await template_service.require_permission(db, tpl, current_user, "view")
    version = await template_service.get_current_version(db, tpl)
    if version is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板无可用版本")
    if not Path(version.file_path).exists():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "模板文件缺失，请重新上传"
        )
    try:
        output = await render_preview(db, version, payload.period)
    except PreviewError as exc:
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
    return FileResponse(
        output,
        media_type=DOCX_MIME,
        filename=f"preview_{tpl.name}_{payload.period}.docx",
    )


@router.post("/{tpl_id}/insight-preview", response_model=InsightPreviewResult)
async def preview_insight(
    tpl_id: int,
    payload: InsightPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InsightPreviewResult:
    """试跑某段 AI 摘要：用当前周期真实 facts 立即出稿，不生成 docx、不落库。

    提示词/参考范文由页面传入，保存前就能看到效果——这是"提示词好不好用"的
    关键反馈回路。取数走与 preview 相同的路径与异常映射；AI 自身失败（未配置/
    网络/超时）内部降级为占位文本，用 `degraded` 标识而非报错。
    """
    tpl = await _get_or_404(db, tpl_id)
    if not tpl.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板已停用")
    await template_service.require_permission(db, tpl, current_user, "edit")
    version = await template_service.get_current_version(db, tpl)
    if version is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板无可用版本")

    try:
        _bindings, facts_by_ph, _metrics = await compute_template_facts(
            db, version, payload.period
        )
    except PreviewError as exc:
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

    facts_list = collect_facts(facts_by_ph)
    if not facts_list:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "当前版本没有任何已绑定的指标，无法生成摘要",
        )
    config = InsightConfig.from_mapping(payload.ai_config.to_db())
    # 试跑不落报告，但同样留痕：便于回溯「当初这段提示词配的是什么、AI 回了什么」
    result = await ai_insight.generate_summary(
        facts_list,
        payload.period,
        config,
        trace=ai_trace.AiCallContext(
            kind="preview",
            template_id=tpl.id,
            slot=payload.placeholder,
            period=payload.period,
            actor_id=current_user.id,
        ),
    )

    unverified = result["unverified_numbers"]
    leaks = result["sample_leak"]
    logger.info(
        "AI 摘要试跑 template_id=%d placeholder=%s metrics=%d degraded=%s"
        " unverified=%d sample_leak=%d",
        tpl.id,
        payload.placeholder,
        len(facts_list),
        result["degraded"],
        len(unverified),
        len(leaks),
    )
    return InsightPreviewResult(
        text=result["text"],
        unverified_numbers=unverified,
        sample_leak=leaks,
        degraded=result["degraded"],
        retried=result["retried"],
        model=result["model"],
        metric_count=len(facts_list),
    )
