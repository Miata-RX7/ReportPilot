"""onlyoffice_service 单元测试：token 签名/校验、编辑器配置、回调 JWT 校验。"""

import jwt
import httpx
import pytest

from app.config import get_settings
from app.models.template import TemplateVersion, WordTemplate
from app.models.user import User
from app.services import onlyoffice_service


def test_sign_verify_roundtrip() -> None:
    token = onlyoffice_service.sign_token({"t": 1, "v": 2, "k": "file"})
    payload = onlyoffice_service.verify_token(token)
    assert payload is not None
    assert payload["t"] == 1 and payload["v"] == 2 and payload["k"] == "file"


def test_verify_tampered_or_garbage() -> None:
    token = onlyoffice_service.sign_token({"t": 1})
    assert onlyoffice_service.verify_token(token + "x") is None


def test_callback_token_lifetime_is_longer_than_file_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回调 token 有效期必须显著长于文件下载 token。

    回归（2026-10-20 线上）：回调 token 沿用 600s，编辑超过 10 分钟后 ONLYOFFICE
    的保存回调全部 403 —— 界面显示「所有更改已保存」，但平台收不到内容、
    不生成新版本。文件下载 token 保持短是对的（防直链盗用），两者要分开。
    """
    settings = get_settings()
    assert settings.editor_callback_token_ttl_seconds > settings.editor_token_ttl_seconds
    assert settings.editor_callback_token_ttl_seconds >= 3600, (
        "回调 token 至少要 1 小时，否则长编辑仍会丢保存"
    )


def test_callback_url_token_outlives_file_url_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """实打实签两个 token 比 exp：回调的必须显著更晚过期。"""
    monkeypatch.setattr(get_settings(), "public_base_url", "http://backend:8000")
    monkeypatch.setattr(get_settings(), "editor_token_ttl_seconds", 600)
    monkeypatch.setattr(get_settings(), "editor_callback_token_ttl_seconds", 86400)
    cb = onlyoffice_service.callback_url(19, 3, 2)
    fl = onlyoffice_service.file_url(19, 3)
    cb_exp = onlyoffice_service.verify_token(cb.split("token=")[1])["exp"]
    fl_exp = onlyoffice_service.verify_token(fl.split("token=")[1])["exp"]
    assert cb_exp - fl_exp >= 3600, "回调 token 应至少比文件 token 多活 1 小时"


def test_is_expired_token_distinguishes_expiry_from_tampering() -> None:
    """403 要能归因成「过期」还是「非法」，否则线上只能靠猜。"""
    live = onlyoffice_service.sign_token({"t": 1, "k": "cb"})
    assert onlyoffice_service.is_expired_token(live) is False

    expired = onlyoffice_service.sign_token({"t": 1, "k": "cb"}, ttl=-10)
    assert onlyoffice_service.verify_token(expired) is None
    assert onlyoffice_service.is_expired_token(expired) is True

    # 签名被篡改：既不是过期，也不是合法 token
    assert onlyoffice_service.is_expired_token(live + "x") is False
    assert onlyoffice_service.is_expired_token("garbage") is False
    assert onlyoffice_service.verify_token("garbage") is None
    assert onlyoffice_service.verify_token("") is None


def test_verify_expired() -> None:
    token = onlyoffice_service.sign_token({"t": 1}, ttl=-5)
    assert onlyoffice_service.verify_token(token) is None


def test_build_editor_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "onlyoffice_url", "http://ds.local")
    monkeypatch.setattr(get_settings(), "public_base_url", "http://backend:8000")
    monkeypatch.setattr(
        get_settings(), "onlyoffice_jwt_secret", "test-ds-secret-0123456789abcdef"
    )
    tpl = WordTemplate(id=5, name="月报模板")
    version = TemplateVersion(
        id=7, template_id=5, version_no=1, file_path="x.docx", source="upload"
    )
    user = User(
        id=3,
        username="u",
        email="u@x.com",
        display_name="U",
        source="local",
        role="user",
        is_active=True,
    )
    cfg = onlyoffice_service.build_editor_config(tpl, version, user)
    assert cfg["document"]["fileType"] == "docx"
    assert "/api/templates/5/file?token=" in cfg["document"]["url"]
    assert "/api/templates/5/callback?token=" in cfg["editorConfig"]["callbackUrl"]
    assert cfg["editorConfig"]["mode"] == "edit"
    assert cfg["documentType"] == "word"
    decoded = jwt.decode(
        cfg["token"], "test-ds-secret-0123456789abcdef", algorithms=["HS256"]
    )
    assert decoded["document"]["fileType"] == "docx"


def test_verify_callback_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "onlyoffice_jwt_secret", "")
    assert onlyoffice_service.verify_callback_jwt({}) is True  # 未配置跳过

    secret = "test-onlyoffice-secret-0123456789abcdef"
    monkeypatch.setattr(get_settings(), "onlyoffice_jwt_secret", secret)
    assert onlyoffice_service.verify_callback_jwt({}) is False
    good = jwt.encode({"status": 2}, secret, algorithm="HS256")
    assert onlyoffice_service.verify_callback_jwt({"token": good}) is True
    bad = jwt.encode({"status": 2}, "wrong-secret", algorithm="HS256")
    assert onlyoffice_service.verify_callback_jwt({"token": bad}) is False


def test_onlyoffice_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "onlyoffice_url", "")
    monkeypatch.setattr(get_settings(), "onlyoffice_jwt_secret", "")
    monkeypatch.setattr(get_settings(), "public_base_url", "")
    assert get_settings().onlyoffice_enabled is False
    monkeypatch.setattr(get_settings(), "onlyoffice_url", "http://ds")
    monkeypatch.setattr(get_settings(), "onlyoffice_jwt_secret", "s")
    monkeypatch.setattr(get_settings(), "public_base_url", "http://b:8000")
    assert get_settings().onlyoffice_enabled is True


class TestInternalUrlRewrite:
    """DS 回调里的下载 URL 是 DS 视角的 host，后端容器连不通 -> 需改写。"""

    def test_rewrite_to_internal_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "onlyoffice_internal_url", "http://onlyoffice")
        assert (
            onlyoffice_service.to_internal_url(
                "http://localhost/cache/files/WS_1/abc.docx?md5=xx&expires=1"
            )
            == "http://onlyoffice/cache/files/WS_1/abc.docx?md5=xx&expires=1"
        )

    def test_rewrite_keeps_path_and_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "onlyoffice_internal_url", "http://onlyoffice:80")
        out = onlyoffice_service.to_internal_url("https://10.0.0.9/cache/f.docx?md5=1")
        assert out.startswith("http://onlyoffice:80/")
        assert out.endswith("?md5=1")

    def test_no_config_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "onlyoffice_internal_url", "")
        raw = "http://localhost/cache/f.docx?md5=1"
        assert onlyoffice_service.to_internal_url(raw) == raw

    def test_invalid_base_falls_back_to_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "onlyoffice_internal_url", "onlyoffice")
        raw = "http://localhost/cache/f.docx"
        assert onlyoffice_service.to_internal_url(raw) == raw

    @pytest.mark.anyio
    async def test_download_prefers_internal_then_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """内网地址失败时退回原始 URL，保证两种部署形态都能存下。"""
        monkeypatch.setattr(get_settings(), "onlyoffice_internal_url", "http://onlyoffice")
        seen: list[str] = []

        class FakeResp:
            def __init__(self, content: bytes) -> None:
                self.content = content

            def raise_for_status(self) -> None:
                return None

        class FakeClient:
            def __init__(self, *a: object, **k: object) -> None:
                pass

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *a: object) -> None:
                return None

            async def get(self, url: str) -> FakeResp:
                seen.append(url)
                if "onlyoffice" in url:
                    raise httpx.ConnectError("refused")
                return FakeResp(b"docx-bytes")

        monkeypatch.setattr(onlyoffice_service.httpx, "AsyncClient", FakeClient)
        data = await onlyoffice_service.download_document("http://localhost/cache/f.docx")
        assert data == b"docx-bytes"
        assert seen == [
            "http://onlyoffice/cache/f.docx",
            "http://localhost/cache/f.docx",
        ]
