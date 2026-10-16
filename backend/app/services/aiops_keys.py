"""AIOps 定位串编解码与列能力推导。

**为什么需要这一层**：AIOps 的一个「可取数对象」不是单个 id，而是坐标四元组
``(实例, 监控项, 列, 主键行)``。其中主键行是任意字符串（实测出现过
``GigabitEthernet1/0/1``、``Ethernet-Trunk1``、``Vlanif30``、
``CNOOC-(HK-Internet_Center)``），含 ``:`` ``/`` 空格与括号，无法安全地
与数字用分隔符拼接；而 ``calc_engine`` 又强制 ``int(item_id)``。因此：
query_config 里存**稳定定位串**，计算时经 ``normalize_item_keys`` 换成合成整数。

定位串格式（``:`` 分隔，主键行走 base64url）::

    number / text 监控项      n:<instance_id>:<item_id>
    list 监控项的某行某列      l:<instance_id>:<item_id>:<column_id>:<b64url(row_key)>

**稳定性要求**：同一份 query_config 在任何进程、任何时间解析出的定位串必须一致，
否则 ``fetch_trend_data`` 的反查会落空。

实测结论（2026-10-16）：

- ``item/list`` 已返回列的全部元数据，**第 5 级不需要额外请求**
- ``source_history`` 是**列级能力标志**（该列是否可能存历史），**不保证每一行有数据**
- 「该行该列当前值非空」⟺「该行该列有历史」（26 行逐行实测，零误判零漏判）
- 节点间隔 57~63 秒有抖动、**不按整点对齐**，故分桶必须 ``unix // 3600 * 3600``
- 月窗口单次可拉 30 天（42626 点 / 1.26 MB / 2.6 秒），故分桶在adapter 侧做
"""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Literal

# 定位串前缀：字母开头，与纯数字的 Zabbix item_id 天然区分
PREFIX_PLAIN = "n"
PREFIX_LIST = "l"

AiopsDataType = Literal["number", "text", "list"]


@dataclass(frozen=True)
class AIOpsRef:
    """一个可取数对象的完整坐标。"""

    instance_id: int
    item_id: int
    # 以下两个仅 list 型监控项有值
    column_id: int | None = None
    row_key: str | None = None

    @property
    def is_list_column(self) -> bool:
        return self.column_id is not None and self.row_key is not None


def _b64e(raw: str) -> str:
    """主键行 -> base64url（无 padding）。UTF-8 编码，支持中文行键。"""
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _b64d(token: str) -> str:
    pad = "=" * (-len(token) % 4)
    try:
        return base64.urlsafe_b64decode(token + pad).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("AIOps 定位串格式非法：主键行解码失败") from None


def encode_ref(
    instance_id: int | str,
    item_id: int | str,
    column_id: int | str | None = None,
    row_key: str | None = None,
) -> str:
    """组装稳定定位串。

    ``column_id`` 与 ``row_key`` **必须同时给或同时不给**，否则抛 ValueError
    —— 半截坐标会让 ``status`` / ``history`` 的行筛选静默失效，
    返回的是跨行聚合值（实测踩过）。
    """
    if (column_id is None) != (row_key is None):
        raise ValueError("column_id 与 row_key 必须同时提供")
    base = f"{instance_id}:{item_id}"
    if column_id is None:
        return f"{PREFIX_PLAIN}:{base}"
    return f"{PREFIX_LIST}:{base}:{column_id}:{_b64e(row_key)}"


def decode_ref(ref: str) -> AIOpsRef:
    """解析定位串；非法格式抛 ValueError（调用方转 422）。"""
    if not isinstance(ref, str) or not ref:
        raise ValueError("AIOps 定位串为空")
    parts = ref.split(":")
    head = parts[0]
    try:
        if head == PREFIX_PLAIN and len(parts) == 3:
            return AIOpsRef(instance_id=int(parts[1]), item_id=int(parts[2]))
        if head == PREFIX_LIST and len(parts) == 5:
            return AIOpsRef(
                instance_id=int(parts[1]),
                item_id=int(parts[2]),
                column_id=int(parts[3]),
                row_key=_b64d(parts[4]),
            )
    except (ValueError, IndexError):
        raise ValueError(f"AIOps 定位串格式非法: {ref[:48]}") from None
    raise ValueError(f"AIOps 定位串格式非法: {ref[:48]}")


@dataclass(frozen=True)
class AIOpsColumn:
    """list 型监控项的一列（由 item/list 的 source_* 平行数组解出）。"""

    column_id: int
    name: str
    unit: str
    value_type: Literal["number", "text"]
    has_history: bool
    is_primary: bool


def _json_array(raw: Any) -> list[Any]:
    """解析 ``"[1,2,3]"`` 这类**字符串化 JSON 数组**（AIOps 全线都是这个形态）。

    已经是 list 时直接返回；解析失败返回空列表而不是抛错——单列元数据坏掉
    不应让整个监控项列不出来。
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return out if isinstance(out, list) else []
    return []


def parse_columns(item_row: dict[str, Any]) -> list[AIOpsColumn]:
    """把 item/list 的一行监控项解成列列表。

    source / source_name / source_unit / source_value_type / source_history 是
    **平行数组**，下标一一对应；主键列来自 module_extension.primary_ids。
    """
    ids = _json_array(item_row.get("source"))
    names = _json_array(item_row.get("source_name"))
    units = _json_array(item_row.get("source_unit"))
    vtypes = _json_array(item_row.get("source_value_type"))
    hists = _json_array(item_row.get("source_history"))
    primary_ids: set[int] = set()
    try:
        primary_ids = {
            int(x)
            for x in (
                json.loads(item_row.get("module_extension") or "{}").get("primary_ids")
                or []
            )
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        primary_ids = set()

    def at(seq: list[Any], i: int, default: Any = "") -> Any:
        return seq[i] if i < len(seq) else default

    out: list[AIOpsColumn] = []
    for i, raw_id in enumerate(ids):
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        vt = str(at(vtypes, i, "")).strip().lower()
        if vt not in ("number", "text"):
            # 未知类型不给出可选项：宁可少一列，也不要错标类型让用户配出坏指标
            continue
        try:
            has_hist = bool(int(at(hists, i, 0)))
        except (TypeError, ValueError):
            has_hist = False
        out.append(
            AIOpsColumn(
                column_id=cid,
                name=str(at(names, i, "")),
                unit=str(at(units, i, "")),
                value_type=vt,  # type: ignore[arg-type]
                has_history=has_hist,
                is_primary=cid in primary_ids,
            )
        )
    return out


def allowed_metric_types(
    data_type: AiopsDataType,
    column: AIOpsColumn | None,
) -> list[str]:
    """该监控项（或该列）允许的平台 metric_type。

    实测 + 用户确认（2026-10-16）的判定表：

    ========== ================ ============== ==============
    data_type 列 value_type source_history 平台可选
    ========== ================ ============== ==============
    number     -                -             scalar / curve
    text       -                -             text
    list       text             0 或 1        text
    list       number           1             scalar / curve
    list       number           0             scalar
    ========== ================ ============== ==============

    「无历史的 number 列只能选数值、不能选曲线」是用户明确要求：它的当前值
    存在于 status 表，但没有时间序列，画曲线必然是空图。

    **pie 对 AIOps 一律不可用** —— AIOps 没有 Zabbix 那种 up/down 值域语义。
    """
    if data_type == "number":
        return ["scalar", "curve"]
    if data_type == "text":
        return ["text"]
    if data_type == "list":
        if column is None:
            return []
        if column.value_type == "text":
            return ["text"]
        return ["scalar", "curve"] if column.has_history else ["scalar"]
    return []


def supports_curve(data_type: AiopsDataType, column: AIOpsColumn | None) -> bool:
    return "curve" in allowed_metric_types(data_type, column)