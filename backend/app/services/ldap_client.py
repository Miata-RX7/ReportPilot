"""AD / LDAP 客户端（2026-11-12）。

只用**读**操作：搜索用户、校验用户密码、拉用户列表。不写任何 AD 侧属性。

## 认证模型

平台有两类账号：

- **本地账号**（``users.source = 'local'``）：admin / ops，密码哈希存在本地
- **AD 账号**（``users.source = 'ad'``）：其余用户，密码只由 AD 校验

登录时先看本地：本地账号且密码对 -> 放行；否则走 AD。

## 密码校验怎么做

AD 里同一个人的 DN 不固定（337 在 ``uid=337,ou=livecom,dc=livecom,dc=hk``，
别人可能在别的 OU），所以**不拼 DN**：

1. 用服务账号 bind 到 AD
2. 按 ``(sAMAccountName=<用户名>)`` 搜到条目 -> 拿到该人的真实 DN
3. 用 **该 DN + 用户提交的密码** 再 bind 一次 -> 成功即密码正确

第 2 步已经区分了「用户不存在」；第 3 步失败就是密码错。

## 状态同步

AD 的 ``userAccountControl`` 里 bit 0x2（ACCOUNTDISABLE）置位 = 账号被禁用。
例如现场 337 的值 66048 = 0x10200，bit 0x2 未置位 -> 启用中。
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any

from ldap3 import SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException

logger = logging.getLogger(__name__)

#: userAccountControl 的 ACCOUNTDISABLE 位
ACCOUNTDISABLE = 0x2


class AdError(Exception):
    """AD 不可用 / 配置不完整 / 查询失败。"""


class AdAuthError(Exception):
    """凭据不正确（用户不存在或密码错）。"""


@dataclass
class AdUser:
    """一个 AD 用户（已按配置做字段映射）。"""

    username: str
    display_name: str
    email: str
    dn: str
    disabled: bool = False
    raw: dict[str, Any] | None = None


@dataclass
class AdConfig:
    """AD 连接与映射配置。"""

    server: str
    bind_dn: str
    bind_password: str
    base_dn: str
    user_filter: str = "(objectClass=user)"
    map_username: str = "sAMAccountName"
    map_display_name: str = "displayName"
    map_email: str = "mail"
    timeout: int = 15

    @property
    def configured(self) -> bool:
        return bool(self.server and self.base_dn and self.bind_dn)


def _escape(value: str) -> str:
    """转义 LDAP 过滤特殊字符（RFC 4515）。"""
    out = []
    for ch in value:
        if ch in "()*\\":
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def _server(cfg: AdConfig) -> Server:
    use_ssl = cfg.server.lower().startswith("ldaps://")
    host = cfg.server.split("://", 1)[-1]
    # 端口：URL 里带了就用它，否则 ldap=389 / ldaps=636
    port = None
    if ":" in host:
        host, port_s = host.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            port = None
    if port is None:
        port = 636 if use_ssl else 389
    kwargs: dict[str, Any] = {"host": host, "port": port, "get_info": None}
    if use_ssl:
        kwargs["tls"] = Tls(validate=ssl.CERT_NONE)
    return Server(**kwargs)


def _connect(cfg: AdConfig) -> Connection:
    """用服务账号 bind 到 AD。"""
    try:
        conn = Connection(
            _server(cfg),
            user=cfg.bind_dn,
            password=cfg.bind_password,
            auto_bind=False,
            raise_exceptions=True,
            receive_timeout=cfg.timeout,
        )
        if not conn.bind():
            raise AdError("AD 绑定失败：服务账号或密码不正确")
        return conn
    except LDAPException as exc:
        raise AdError(f"AD 连接失败：{exc}") from exc


def _to_user(entry: dict[str, Any], cfg: AdConfig) -> AdUser | None:
    attrs = entry.get("attributes") or {}

    def first(name: str) -> str:
        v = attrs.get(name)
        if isinstance(v, list):
            v = v[0] if v else ""
        if v is None:
            return ""
        if isinstance(v, bytes):
            v = v.decode("utf-8", "ignore")
        return str(v).strip()

    username = first(cfg.map_username)
    if not username:
        return None
    uac = first("userAccountControl")
    try:
        disabled = bool(int(uac) & ACCOUNTDISABLE) if uac else False
    except ValueError:
        disabled = False
    return AdUser(
        username=username,
        display_name=first(cfg.map_display_name) or username,
        email=first(cfg.map_email),
        dn=entry.get("dn") or "",
        disabled=disabled,
        raw={"userAccountControl": uac},
    )


def _search_attrs(cfg: AdConfig) -> list[str]:
    return [
        cfg.map_username,
        cfg.map_display_name,
        cfg.map_email,
        "userAccountControl",
        "distinguishedName",
    ]


def find_user(cfg: AdConfig, username: str) -> AdUser | None:
    """按登录名（sAMAccountName）查一个用户。查不到返回 None。"""
    if not cfg.configured:
        raise AdError("AD 未配置完整")
    conn = _connect(cfg)
    try:
        flt = f"(&{cfg.user_filter}({cfg.map_username}={_escape(username)}))"
        conn.search(
            cfg.base_dn,
            flt,
            search_scope=SUBTREE,
            attributes=_search_attrs(cfg),
        )
        if not conn.entries:
            return None
        return _to_user(dict(conn.entries[0]), cfg)
    except LDAPException as exc:
        raise AdError(f"AD 查询失败：{exc}") from exc
    finally:
        _safe_unbind(conn)


def list_users(cfg: AdConfig, limit: int = 5000) -> list[AdUser]:
    """拉取 AD 用户列表（用于同步建档 / 状态同步）。"""
    if not cfg.configured:
        raise AdError("AD 未配置完整")
    conn = _connect(cfg)
    out: list[AdUser] = []
    try:
        conn.search(
            cfg.base_dn,
            cfg.user_filter,
            search_scope=SUBTREE,
            attributes=_search_attrs(cfg),
            size_limit=limit,
        )
        for entry in conn.entries:
            u = _to_user(dict(entry), cfg)
            if u is not None:
                out.append(u)
        return out
    except LDAPException as exc:
        raise AdError(f"AD 查询失败：{exc}") from exc
    finally:
        _safe_unbind(conn)


def authenticate(cfg: AdConfig, username: str, password: str) -> AdUser:
    """校验 AD 用户的密码。成功返回用户信息；失败抛 AdAuthError。"""
    if not cfg.configured:
        raise AdError("AD 未配置完整")
    if not password:
        raise AdAuthError("密码不能为空")
    conn = _connect(cfg)
    try:
        flt = f"(&{cfg.user_filter}({cfg.map_username}={_escape(username)}))"
        conn.search(
            cfg.base_dn,
            flt,
            search_scope=SUBTREE,
            attributes=_search_attrs(cfg),
        )
        if not conn.entries:
            raise AdAuthError("用户名或密码错误")
        entry = dict(conn.entries[0])
        user = _to_user(entry, cfg)
        if user is None:
            raise AdAuthError("用户名或密码错误")
        dn = entry.get("dn")
        if not dn:
            raise AdError("AD 条目缺少 DN")
    except LDAPException as exc:
        raise AdError(f"AD 查询失败：{exc}") from exc
    finally:
        _safe_unbind(conn)

    # 用该用户的 DN + 提交的密码重新 bind —— 成功即密码正确
    user_conn = Connection(
        _server(cfg),
        user=dn,
        password=password,
        auto_bind=False,
        raise_exceptions=False,
        receive_timeout=cfg.timeout,
    )
    try:
        if not user_conn.bind():
            raise AdAuthError("用户名或密码错误")
    except LDAPException as exc:
        raise AdAuthError(f"用户名或密码错误（{exc}）") from exc
    finally:
        _safe_unbind(user_conn)
    return user


def _safe_unbind(conn: Connection) -> None:
    try:
        conn.unbind()
    except Exception:
        logger.debug("LDAP unbind 失败（忽略）", exc_info=True)
