"""ONLYOFFICE Document Server 集成。

- 生成编辑器配置（document.url / callbackUrl + JWT 签名）
- DS 拉取文件、回调均不带用户 Bearer，改用短期 HMAC 一次性 token
- 回调保存：下载编辑后的 docx -> 落盘为新版本（调用方负责建版本/清理绑定）
"""

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt

from app.config import get_settings
from app.models.template import TemplateVersion, WordTemplate
from app.models.user import User

logger = logging.getLogger(__name__)

TOKEN_NS = "rp-editor:"
CALLBACK_TIMEOUT_SECONDS = 60

# DS 回调 status：
#   2 = 最终保存（用户点保存/关闭文档）-> 落盘为新版本
#   6 = 强制保存（forcesave/autosave 定时触发）-> 仅续保内容，不产生新版本
#   3/7 = 保存出错
# 6 必须与 2 分开：forcesave 会在编辑过程中反复触发，若也建版本，一次编辑
# 会产生大量重复版本（DS 持有的始终是最新文档，6 的内容随后会由 2 覆盖）。
STATUS_SAVE = (2,)
STATUS_FORCE_SAVE = (6,)
STATUS_ERROR = (3, 7)


def _secret() -> str:
    return get_settings().jwt_secret or ""


def sign_token(payload: dict[str, Any], ttl: int | None = None) -> str:
    """短期 HMAC token（base64url(payload).base64url(sig)）。"""
    settings = get_settings()
    ttl = ttl if ttl is not None else settings.editor_token_ttl_seconds
    body = {**payload, "exp": int(time.time()) + ttl}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new((TOKEN_NS + _secret()).encode(), raw, hashlib.sha256).digest()
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"{b64(raw)}.{b64(sig)}"


def verify_token(token: str) -> dict[str, Any] | None:
    """校验并解出 token payload；无效/过期返回 None。"""
    try:
        raw_b64, sig_b64 = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        raw = base64.urlsafe_b64decode(pad(raw_b64))
        sig = base64.urlsafe_b64decode(pad(sig_b64))
    except Exception:
        return None
    expected = hmac.new((TOKEN_NS + _secret()).encode(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        body = json.loads(raw)
    except Exception:
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    return body


def file_url(template_id: int, version_id: int) -> str:
    base = get_settings().public_base_url.rstrip("/")
    token = sign_token({"t": template_id, "v": version_id, "k": "file"})
    return f"{base}/api/templates/{template_id}/file?token={token}"


def callback_url(template_id: int, version_id: int, user_id: int) -> str:
    base = get_settings().public_base_url.rstrip("/")
    token = sign_token({"t": template_id, "v": version_id, "u": user_id, "k": "cb"})
    return f"{base}/api/templates/{template_id}/callback?token={token}"


def build_editor_config(
    template: WordTemplate,
    version: TemplateVersion,
    user: User,
    mode: str = "edit",
) -> dict[str, Any]:
    """构造 DocEditor 初始化配置（含 JWT 签名 token）。

    mode = edit 在线编辑；mode = view 只读查看原始文档。
    """
    settings = get_settings()
    editable = mode == "edit"
    key = f"{template.id}_{version.id}_{mode}_{uuid.uuid4().hex[:12]}"
    config: dict[str, Any] = {
        "document": {
            "fileType": "docx",
            "key": key,
            "title": f"{template.name}.docx",
            "url": file_url(template.id, version.id),
            "permissions": {"edit": editable, "download": True},
        },
        "documentType": "word",
        "type": "desktop",
        "width": "100%",
        "height": "100%",
        "editorConfig": {
            "callbackUrl": callback_url(template.id, version.id, user.id),
            "lang": "zh-CN",
            "mode": "edit" if editable else "view",
            "user": {"id": str(user.id), "name": user.display_name or user.username},
            "customization": {
                "autosave": editable,
                "forcesave": editable,
                "compactHeader": True,
                "hideRightMenu": True,
            },
        },
    }
    if settings.onlyoffice_jwt_secret:
        config["token"] = jwt.encode(
            config, settings.onlyoffice_jwt_secret, algorithm="HS256"
        )
    return config


def verify_callback_jwt(body: dict[str, Any]) -> bool:
    """校验 DS 回调体中的 JWT 签名（未配置密钥时跳过）。"""
    secret = get_settings().onlyoffice_jwt_secret
    if not secret:
        return True
    token = body.get("token")
    if not token:
        return False
    try:
        jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except jwt.PyJWTError:
        logger.warning("ONLYOFFICE 回调 JWT 校验失败")
        return False


def to_internal_url(url: str) -> str:
    """把 DS 生成的文件 URL 改写成后端容器可达的地址（保留 path/query）。

    DS 按自己的视角生成下载 URL（常见 host 是 localhost 或容器本地名），
    后端容器解析不了这些名字，导致保存回调下载失败。配置了
    onlyoffice_internal_url 时替换 scheme+netloc；未配置则原样返回。
    """
    base = get_settings().onlyoffice_internal_url.strip()
    if not base:
        return url
    b = urlsplit(base)
    if not b.scheme or not b.netloc:
        return url
    p = urlsplit(url)
    return urlunsplit((b.scheme, b.netloc, p.path, p.query, p.fragment))


async def download_document(url: str) -> bytes:
    """从 DS 返回的 url 下载编辑后的文档字节。

    先试改写后的内网地址（DS 视角的 host 后端通常连不通），失败再退回原 URL。
    """
    internal = to_internal_url(url)
    candidates = [internal] if internal == url else [internal, url]
    last: Exception | None = None
    for candidate in candidates:
        try:
            async with httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_SECONDS) as client:
                resp = await client.get(candidate)
                resp.raise_for_status()
                return resp.content
        except Exception as exc:  # noqa: BLE001 - 逐个尝试，失败原因记日志
            last = exc
            logger.warning("ONLYOFFICE 下载尝试失败 url=%s err=%s", candidate, exc)
    raise last if last else RuntimeError("download failed")
