"""报告 DOCX -> PDF 转换（用于确认页预览，2026-11-12）。

用 ONLYOFFICE 的 ``/converter`` API（DocumentServer 自带转换器）。
平台已有 ONLYOFFICE 集成（模板在线编辑），这里只复用同一个服务地址。

为什么在**报告生成时**转一次并缓存，而不是确认页实时转：
- 转换要几秒，每次打开确认页都转一遍会卡
- PDF 是静态快照，正好用于「预览的就是最终发出的那份」

转换失败**不阻塞报告生成** —— 转不动就只留「下载 Word」，报告照常生成/推送。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

CONVERT_TIMEOUT = 120.0


def _internal_url() -> str:
    settings = get_settings()
    url = settings.onlyoffice_internal_url or settings.onlyoffice_url
    return str(url or "").rstrip("/")


def _secret() -> str:
    return get_settings().onlyoffice_jwt_secret or ""


async def convert_docx_to_pdf(docx_path: Path, out_dir: Path) -> Path | None:
    """DOCX -> PDF。成功返回 PDF 路径，失败返回 None（**不抛异常**）。"""
    if not docx_path.exists():
        logger.warning("DOCX 不存在，跳过转换：%s", docx_path)
        return None
    base = _internal_url()
    if not base:
        logger.warning("未配置 ONLYOFFICE 地址，跳过 PDF 转换")
        return None
    try:
        payload: dict[str, Any] = {
            "async": False,
            "filetype": "docx",
            "key": uuid.uuid4().hex,
            "outputtype": "pdf",
            "title": docx_path.stem,
        }
        secret = _secret()
        if secret:
            import jwt

            payload["token"] = jwt.encode(payload, secret, algorithm="HS256")
        async with httpx.AsyncClient(timeout=CONVERT_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/converter",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        file_url = data.get("fileUrl")
        if not file_url:
            logger.warning("converter 未返回 fileUrl：%s", str(data)[:200])
            return None
        async with httpx.AsyncClient(timeout=CONVERT_TIMEOUT) as client:
            pdf_resp = await client.get(file_url)
            pdf_resp.raise_for_status()
            content = pdf_resp.content
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{docx_path.stem}.pdf"
        out_path.write_bytes(content)
        logger.info(
            "PDF 转换完成 %s -> %s (%d bytes)", docx_path.name, out_path, len(content)
        )
        return out_path
    except Exception:
        logger.exception("PDF 转换失败（不影响报告生成）：%s", docx_path)
        return None
