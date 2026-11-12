"""报告 DOCX -> PDF 转换（用于确认页预览，2026-11-12）。

两条路，优先第一条：

1. **LibreOffice headless**（``soffice --headless --convert-to pdf``）
   —— 装在 backend 镜像里（``libreoffice-writer``）。这是实际生效的那条。
2. **ONLYOFFICE /converter** —— 线上那个 DocumentServer 是**精简版**
   （``documentserver/core/`` 是空的、没有 x2t 转换器），``/converter`` 会返回
   ``error -7``。留着作为「将来换成完整版 DS 就能用」的备路。

为什么在**报告生成时**转一次并缓存，而不是确认页实时转：
- 转换要几十秒，每次打开确认页都转一遍没法用
- PDF 是静态快照，正好用于「预览的就是最终发出的那份」

转换失败**不阻塞报告生成** —— 转不动就只留「下载 Word」，报告照常生成/推送。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

#: LibreOffice 转换超时（秒）。首次启动 profile 偏慢，给足
SOFFICE_TIMEOUT = 180.0
#: ONLYOFFICE converter 超时
CONVERT_TIMEOUT = 120.0


def _soffice_bin() -> str | None:
    """找 soffice 可执行文件。"""
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    # 常见的非 PATH 安装位置
    for p in ("/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice"):
        if Path(p).exists():
            return p
    return None


async def _convert_with_soffice(docx_path: Path, out_dir: Path) -> Path | None:
    """LibreOffice headless 转换。"""
    bin_path = _soffice_bin()
    if bin_path is None:
        logger.info("未找到 LibreOffice，跳过 soffice 转换")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    # LibreOffice 需要一个可写的 HOME（profile 放这里）
    with tempfile.TemporaryDirectory(prefix="lo_profile_") as profile:
        env = dict(os.environ)
        env["HOME"] = profile
        env.setdefault("SAL_USE_VCLPLUGIN", "svp")
        cmd = [
            bin_path,
            "--headless",
            "--norestore",
            "--invisible",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(docx_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=SOFFICE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("LibreOffice 转换超时：%s", docx_path)
            return None
    pdf_path = out_dir / f"{docx_path.stem}.pdf"
    if not pdf_path.exists():
        logger.warning(
            "LibreOffice 未产出 PDF：%s (%s)",
            docx_path,
            err.decode(errors="ignore")[:200] if err else "",
        )
        return None
    logger.info("PDF 转换完成(LibreOffice) %s -> %s", docx_path.name, pdf_path)
    return pdf_path


def _internal_url() -> str:
    settings = get_settings()
    url = settings.onlyoffice_internal_url or settings.onlyoffice_url
    return str(url or "").rstrip("/")


async def _convert_with_onlyoffice(docx_path: Path, out_dir: Path) -> Path | None:
    """ONLYOFFICE /converter 转换（线上是精简版 DS，实际会失败 —— 保留作备路）。"""
    base = _internal_url()
    if not base:
        return None
    try:
        payload: dict[str, Any] = {
            "async": False,
            "filetype": "docx",
            "key": uuid.uuid4().hex,
            "outputtype": "pdf",
            "title": docx_path.stem,
            "document": {"base64": _b64(docx_path), "fileType": "docx", "title": docx_path.stem},
        }
        secret = get_settings().onlyoffice_jwt_secret
        if secret:
            import jwt

            payload["token"] = jwt.encode(payload, secret, algorithm="HS256")
        async with httpx.AsyncClient(timeout=CONVERT_TIMEOUT) as client:
            resp = await client.post(f"{base}/converter", json=payload)
            resp.raise_for_status()
            data = resp.json()
        if data.get("error"):
            logger.info("ONLYOFFICE converter 返回错误：%s", data.get("error"))
            return None
        file_url = data.get("fileUrl")
        if not file_url:
            return None
        async with httpx.AsyncClient(timeout=CONVERT_TIMEOUT) as client:
            pdf_resp = await client.get(file_url)
            pdf_resp.raise_for_status()
            content = pdf_resp.content
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{docx_path.stem}.pdf"
        out_path.write_bytes(content)
        return out_path
    except Exception:
        logger.exception("ONLYOFFICE 转换失败")
        return None


def _b64(path: Path) -> str:
    import base64

    return base64.b64encode(path.read_bytes()).decode()


async def convert_docx_to_pdf(docx_path: Path, out_dir: Path) -> Path | None:
    """DOCX -> PDF。成功返回 PDF 路径，失败返回 None（**不抛异常**）。"""
    if not docx_path.exists():
        logger.warning("DOCX 不存在，跳过转换：%s", docx_path)
        return None
    result = await _convert_with_soffice(docx_path, out_dir)
    if result is not None:
        return result
    logger.info("LibreOffice 不可用，回退 ONLYOFFICE converter")
    return await _convert_with_onlyoffice(docx_path, out_dir)
