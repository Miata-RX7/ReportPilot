"""敏感信息加解密（Fernet 对称加密）。

安全约束：
- 密钥仅来自环境变量 ENCRYPTION_KEY，缺失/非法时抛 RuntimeError（启动 fail-fast）。
- secret_encrypted 存 Fernet.encrypt() 的完整输出（Base64 token 字符串）。
- 本模块任何异常消息、日志均不携带明文或密文内容。
"""

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    """构建（并缓存）Fernet 实例。密钥缺失或非法时抛 RuntimeError。"""
    global _fernet
    if _fernet is None:
        key = get_settings().encryption_key
        if not key:
            raise RuntimeError(
                "ENCRYPTION_KEY 未配置。请用以下命令生成并写入环境变量："
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        try:
            _fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "ENCRYPTION_KEY 格式非法，需为 Fernet.generate_key() 的输出"
            ) from exc
    return _fernet


def reset_cached_fernet() -> None:
    """清除缓存的 Fernet 实例（测试 / 密钥轮换用）。"""
    global _fernet
    _fernet = None


def encrypt(plaintext: str) -> str:
    """明文 -> Fernet 完整 token（Base64 字符串）。空明文抛 ValueError。"""
    if not plaintext:
        raise ValueError("secret 不能为空")
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token_encrypted: str) -> str:
    """密文 -> 明文。解密失败抛 ValueError（不携带密文内容）。"""
    try:
        return get_fernet().decrypt(token_encrypted.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("密文解密失败（密钥不匹配或密文已损坏）") from exc


def mask_secret(plaintext: str) -> str:
    """生成脱敏提示：长度<=4 返回 "****"，否则 "****" + 末4位。

    仅用于写入 secret_hint 时调用一次；响应序列化直接读 secret_hint，
    不得对密文或明文再次调用本函数。
    """
    if not plaintext:
        return ""
    if len(plaintext) <= 4:
        return "****"
    return f"****{plaintext[-4:]}"
