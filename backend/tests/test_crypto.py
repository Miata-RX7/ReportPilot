"""crypto 模块单元测试。"""

import pytest

from app.config import get_settings
from app.services.crypto import (
    decrypt,
    encrypt,
    get_fernet,
    mask_secret,
    reset_cached_fernet,
)


class TestRoundTrip:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        plaintext = "zabbix-api-token-abcdef123456"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_same_plaintext_different_ciphertext(self) -> None:
        # Fernet 含随机 IV，两次密文不同但都能解密
        c1 = encrypt("same-secret")
        c2 = encrypt("same-secret")
        assert c1 != c2
        assert decrypt(c1) == decrypt(c2) == "same-secret"

    def test_encrypt_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            encrypt("")

    def test_decrypt_tampered_raises(self) -> None:
        ciphertext = encrypt("some-secret")
        tampered = ciphertext[:-2] + ("AA" if not ciphertext.endswith("AA") else "BB")
        with pytest.raises(ValueError, match="解密失败"):
            decrypt(tampered)


class TestKeyValidation:
    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        get_settings.cache_clear()
        reset_cached_fernet()
        try:
            with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
                get_fernet()
        finally:
            get_settings.cache_clear()
            reset_cached_fernet()

    def test_invalid_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENCRYPTION_KEY", "not-a-valid-fernet-key")
        get_settings.cache_clear()
        reset_cached_fernet()
        try:
            with pytest.raises(RuntimeError, match="格式非法"):
                get_fernet()
        finally:
            get_settings.cache_clear()
            reset_cached_fernet()


class TestMaskSecret:
    def test_normal_length(self) -> None:
        assert mask_secret("token-abcdef1234") == "****1234"

    def test_short_secret(self) -> None:
        assert mask_secret("abcd") == "****"
        assert mask_secret("ab") == "****"

    def test_empty(self) -> None:
        assert mask_secret("") == ""
