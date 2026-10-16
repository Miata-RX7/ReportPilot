"""AIOps 定位串编解码与列能力判定（纯逻辑，无网络）。

这些规则来自 2026-10-16 的接口实测，**每一条都有踩过的坑对应**，
改动前请先读tests/test_aiops_keys.py 里的注释。
"""

import pytest

from app.services.aiops_keys import (
    AIOpsColumn,
    allowed_metric_types,
    decode_ref,
    encode_ref,
    parse_columns,
)


class TestRefCodec:
    """定位串编解码。"""

    def test_plain_ref_roundtrip(self) -> None:
        ref = encode_ref(715, 1694)
        assert ref == "n:715:1694"
        got = decode_ref(ref)
        assert (got.instance_id, got.item_id) == (715, 1694)
        assert got.column_id is None and got.row_key is None
        assert got.is_list_column is False

    def test_list_ref_roundtrip(self) -> None:
        ref = encode_ref(715, 1696, 11504, "GigabitEthernet1/0/1")
        got = decode_ref(ref)
        assert got.instance_id == 715
        assert got.item_id == 1696
        assert got.column_id == 11504
        assert got.row_key == "GigabitEthernet1/0/1"
        assert got.is_list_column is True

    @pytest.mark.parametrize(
        "row_key",
        [
            "GigabitEthernet1/0/1",  # 含 /
            "Ethernet-Trunk1",  # 含 -
            "Vlanif30",
            "Console9/0/0",
            "CNOOC-(HK-Internet_Center)",  # 含括号与连字符
            "端口 1",  # 含空格
            "a:b:c",  # 含冒号 —— 分隔符歧义用例
            "中文行键",
        ],
    )
    def test_row_key_survives_awkward_characters(self, row_key: str) -> None:
        """主键行含 / : 空格括号中文都能往返 —— 这是 base64url 存在的全部理由。"""
        assert decode_ref(encode_ref(1, 2, 3, row_key)).row_key == row_key

    def test_column_and_row_must_come_together(self) -> None:
        """半截坐标会让 history 的行筛选静默失效（返回跨行聚合值），必须拦。"""
        with pytest.raises(ValueError, match="必须同时提供"):
            encode_ref(1, 2, column_id=3)
        with pytest.raises(ValueError, match="必须同时提供"):
            encode_ref(1, 2, row_key="Port1")

    @pytest.mark.parametrize(
        "bad",
        ["", "x:1:2", "l:1:2", "l:1:2:3", "n:1", "1:2", "l:a:b:c:d"],
    )
    def test_invalid_ref_raises(self, bad: str) -> None:
        with pytest.raises(ValueError, match="定位串"):
            decode_ref(bad)

    def test_ref_is_stable_across_calls(self) -> None:
        """反查依赖稳定性：同一坐标反复编码必须逐字相同。"""
        a = encode_ref(715, 1696, 11504, "Port1")
        b = encode_ref("715", "1696", "11504", "Port1")
        assert a == b


class TestParseColumns:
    """item/list 的平行数组 -> 列列表。"""

    def _item(self) -> dict:
        # 真实样本（实例 14009的端口表，item 9669）的字段形态
        return {
            "source": "[11498, 11499, 11500, 11503, 11504]",
            "source_name": '["端口名称","端口别名","状态","带宽","入流量"]',
            "source_unit": '["","","","auto1000","auto1000 bps"]',
            "source_value_type": '["text","text","text","number","number"]',
            "source_history": "[0, 0, 1, 0, 1]",
            "module_extension": '{"primary_ids":[11498]}',
        }

    def test_parses_all_columns(self) -> None:
        cols = parse_columns(self._item())
        assert [c.column_id for c in cols] == [11498, 11499, 11500, 11503, 11504]
        assert cols[0].name == "端口名称"
        assert cols[4].unit == "auto1000 bps"

    def test_primary_column_flagged(self) -> None:
        cols = parse_columns(self._item())
        assert [c.is_primary for c in cols] == [True, False, False, False, False]

    def test_value_type_and_history_preserved(self) -> None:
        cols = {c.column_id: c for c in parse_columns(self._item())}
        assert cols[11500].value_type == "text" and cols[11500].has_history is True
        # 实测存在这种组合：数值列但没有历史（带宽是配置值不是采集值）
        assert cols[11503].value_type == "number" and cols[11503].has_history is False
        assert cols[11504].value_type == "number" and cols[11504].has_history is True

    def test_unknown_value_type_dropped(self) -> None:
        """类型认不出来的列宁可不给出，也不要错标类型让用户配出坏指标。"""
        item = self._item() | {"source_value_type": '["text","","text","number","number"]'}
        assert [c.column_id for c in parse_columns(item)] == [
            11498,
            11500,
            11503,
            11504,
        ]

    def test_broken_json_does_not_raise(self) -> None:
        """单列元数据坏掉不该让整个监控项列不出来。"""
        assert parse_columns({"source": "not-json"}) == []
        assert parse_columns({}) == []

    def test_accepts_native_list(self) -> None:
        """已是 list 时直接用（防御不同版本的接口形态）。"""
        item = self._item() | {"source": [1, 2], "source_name": ["a", "b"]}
        assert [c.column_id for c in parse_columns(item)] == [1, 2]


class TestAllowedMetricTypes:
    """列 -> 平台 metric_type 的判定表（用户 2026-10-16 确认）。"""

    @staticmethod
    def _col(value_type: str, has_history: bool) -> AIOpsColumn:
        return AIOpsColumn(
            column_id=1,
            name="c",
            unit="",
            value_type=value_type,  # type: ignore[arg-type]
            has_history=has_history,
            is_primary=False,
        )

    def test_plain_number_item(self) -> None:
        assert allowed_metric_types("number", None) == ["scalar", "curve"]

    def test_plain_text_item(self) -> None:
        assert allowed_metric_types("text", None) == ["text"]

    def test_list_text_column_is_text_only(self) -> None:
        """text 列即使有历史也归text（实测 状态列就是 text+history=1）。"""
        for has_hist in (True, False):
            col = self._col("text", has_hist)
            assert allowed_metric_types("list", col) == ["text"]

    def test_list_number_column_with_history_allows_curve(self) -> None:
        assert allowed_metric_types("list", self._col("number", True)) == [
            "scalar",
            "curve",
        ]

    def test_list_number_column_without_history_is_scalar_only(self) -> None:
        """用户明确要求：无历史的数值列「只能选数值不能选曲线」——
        画曲线必然是空图（实测该列 history 接口返回 0 个点）。"""
        assert allowed_metric_types("list", self._col("number", False)) == ["scalar"]

    def test_list_without_column_is_empty(self) -> None:
        assert allowed_metric_types("list", None) == []

    def test_pie_never_allowed(self) -> None:
        """AIOps 没有 up/down 值域语义，pie 一律不可用。"""
        for dt in ("number", "text", "list"):
            col = self._col("number", True) if dt == "list" else None
            assert "pie" not in allowed_metric_types(dt, col)  # type: ignore[arg-type]