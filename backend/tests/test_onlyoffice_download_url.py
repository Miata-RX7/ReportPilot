"""ONLYOFFICE 文档下载地址的子路径前缀剥离（2026-11-25）。

背景：子路径部署下 DS 生成的下载 url 带 /onlyoffice 前缀，直接拼到内部地址
后面会得到 http://onlyoffice/onlyoffice/cache/... -> 404，保存回调失败。
"""

from unittest.mock import patch

import app.services.onlyoffice_service as svc


class _Settings:
    onlyoffice_internal_url = "http://onlyoffice"
    onlyoffice_url = "https://report.livecom.ltd/onlyoffice"


class TestToInternalUrl:
    def _convert(self, url: str) -> str:
        with patch.object(svc, "get_settings", lambda: _Settings()):
            return svc.to_internal_url(url)

    def test_strips_public_path_prefix(self) -> None:
        """带 /onlyoffice 前缀的 url -> 内部地址不能重复前缀。"""
        got = self._convert(
            "https://report.livecom.ltd/onlyoffice/cache/files/data/"
            "20_49_edit_x_4108/output.docx/output.docx?md5=abc&expires=1"
        )
        assert got.startswith("http://onlyoffice/cache/files/data/"), got
        assert "/onlyoffice/onlyoffice/" not in got, got
        # query 必须保留（md5/expires 是 DS 的下载校验）
        assert "md5=abc" in got and "expires=1" in got, got

    def test_keeps_url_without_prefix(self) -> None:
        """本来就没有前缀的 url 不受影响。"""
        got = self._convert(
            "http://onlyoffice/cache/files/data/x/output.docx/output.docx?md5=1"
        )
        assert got.startswith("http://onlyoffice/cache/files/data/"), got

    def test_no_internal_url_returns_original(self) -> None:
        """没配内部地址时原样返回。"""
        with patch.object(svc, "get_settings", lambda: type(
            "S", (), {"onlyoffice_internal_url": "", "onlyoffice_url": ""}
        )()):
            url = "https://report.livecom.ltd/onlyoffice/cache/x/output.docx"
            assert svc.to_internal_url(url) == url
