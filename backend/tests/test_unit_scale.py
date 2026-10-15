"""unit_scale 单测：单位换算「展示派生层」的语义锁定。

覆盖：
- UnitScale.parse 的宽容解析（脏配置一律退化为「不换算」，绝不打断报告生成）
- 自动选档（1000 / 1024、0 与负值、超大值封顶 E 档、浮点误差陷阱）
- 固定除数、单位拼接（后缀优先 / 回退指标原始单位）、展示精度优先级
- ResolvedScale.describe 的 formula 文案
- UnitScaleConfig 的严格校验与 to_storage 落库形态（含 divisor=1000 的回归）
"""

import pytest
from pydantic import ValidationError

from app.schemas.metric import UnitScaleConfig, coerce_unit_config
from app.services.unit_scale import (
    DEFAULT_DISPLAY_DECIMALS,
    UnitScale,
    auto_order,
    display_unit_of,
    scale_value,
)


class TestParse:
    def test_non_dict_is_disabled(self) -> None:
        assert UnitScale.parse(None).enabled is False
        assert UnitScale.parse("auto").enabled is False
        assert UnitScale.parse([1, 2]).enabled is False

    def test_unknown_mode_is_disabled(self) -> None:
        assert UnitScale.parse({"mode": "wat"}).enabled is False
        assert UnitScale.parse({}).enabled is False

    def test_auto_defaults_and_strip(self) -> None:
        s = UnitScale.parse({"mode": "auto", "suffix_unit": "  bps  "})
        assert s.enabled is True
        assert s.mode == "auto"
        assert s.base == 1000
        assert s.suffix_unit == "bps"

    def test_auto_bad_base_falls_back_to_1000(self) -> None:
        assert UnitScale.parse({"mode": "auto", "base": 512}).base == 1000

    def test_fixed_ok(self) -> None:
        s = UnitScale.parse({"mode": "fixed", "divisor": 1000, "display_unit": "Kbps"})
        assert s.enabled is True
        assert s.divisor == 1000.0
        assert s.display_unit == "Kbps"

    @pytest.mark.parametrize("bad", [0, -1, "abc", None, float("inf")])
    def test_fixed_bad_divisor_is_disabled(self, bad: object) -> None:
        assert UnitScale.parse({"mode": "fixed", "divisor": bad}).enabled is False

    def test_decimals_clamped(self) -> None:
        assert UnitScale.parse({"mode": "auto", "decimals": 99}).decimals == 10
        assert UnitScale.parse({"mode": "auto", "decimals": -3}).decimals == 0
        assert UnitScale.parse({"mode": "auto", "decimals": "abc"}).decimals is None


class TestAutoOrder:
    @pytest.mark.parametrize(
        ("magnitude", "base", "expected"),
        [
            (0.0, 1000, 0),
            (999.0, 1000, 0),
            (1000.0, 1000, 1),
            (262468.9041666667, 1000, 1),
            (999_999.0, 1000, 1),
            (1_000_000.0, 1000, 2),
            # 浮点陷阱：math.floor(math.log(1e9, 1000)) 会算成 2
            (1e9, 1000, 3),
            (1e12, 1000, 4),
            (-1000.0, 1000, 1),  # 取绝对值
            (1e21, 1000, 6),  # 封顶 E 档
            (1e30, 1000, 6),
            (2000.0, 1024, 1),
            (1023.0, 1024, 0),
        ],
    )
    def test_order(self, magnitude: float, base: int, expected: int) -> None:
        assert auto_order(magnitude, base) == expected

    def test_non_finite(self) -> None:
        assert auto_order(float("inf"), 1000) == 0
        assert auto_order(float("nan"), 1000) == 0


class TestResolve:
    def test_disabled_returns_none(self) -> None:
        assert UnitScale().resolve(magnitude=1) is None
        assert UnitScale.parse({"mode": "none"}).resolve(magnitude=1) is None

    def test_auto_bps_to_kbps(self) -> None:
        """262468.9041666667 bps -> ÷1000 -> 262.47 Kbps（默认 2 位小数）。"""
        cfg = UnitScaleConfig(mode="auto", base=1000, suffix_unit="bps")
        scale = UnitScale.parse(cfg.model_dump()).resolve(magnitude=262468.9041666667)
        assert scale is not None
        assert scale.factor == 1000
        assert scale.unit == "Kbps"
        assert scale.precision == DEFAULT_DISPLAY_DECIMALS
        assert scale.apply(262468.9041666667) == pytest.approx(262.4689041667)

    def test_auto_suffix_falls_back_to_metric_unit(self) -> None:
        scale = UnitScale.parse({"mode": "auto"}).resolve(
            magnitude=2_000_000, base_unit="bps"
        )
        assert scale is not None
        assert scale.unit == "Mbps"

    def test_auto_base_1024_uses_binary_prefix(self) -> None:
        scale = UnitScale.parse({"mode": "auto", "base": 1024, "suffix_unit": "B"}).resolve(
            magnitude=2048
        )
        assert scale is not None
        assert scale.factor == 1024
        assert scale.unit == "KiB"

    def test_auto_zero_keeps_original_unit(self) -> None:
        scale = UnitScale.parse({"mode": "auto", "suffix_unit": "bps"}).resolve(magnitude=0)
        assert scale is not None
        assert scale.factor == 1
        assert scale.unit == "bps"

    def test_fixed_divisor(self) -> None:
        scale = UnitScale.parse(
            {"mode": "fixed", "divisor": 1000, "display_unit": "Kbps"}
        ).resolve(magnitude=1)
        assert scale is not None
        assert scale.factor == 1000
        assert scale.unit == "Kbps"

    def test_fixed_falls_back_to_metric_unit(self) -> None:
        scale = UnitScale.parse({"mode": "fixed", "divisor": 60}).resolve(
            magnitude=1, base_unit="s"
        )
        assert scale is not None
        assert scale.unit == "s"

    def test_precision_priority(self) -> None:
        """换算配置 decimals > 指标 display_precision > 默认 2 位。"""
        auto = {"mode": "auto"}
        assert UnitScale.parse(auto).resolve(magnitude=1, precision=4).precision == 4
        assert UnitScale.parse(auto).resolve(magnitude=1).precision == 2
        assert (
            UnitScale.parse({**auto, "decimals": 3}).resolve(magnitude=1, precision=4).precision
            == 3
        )

    def test_apply_none_passthrough(self) -> None:
        scale = UnitScale.parse({"mode": "fixed", "divisor": 10}).resolve(magnitude=1)
        assert scale is not None
        assert scale.apply(None) is None

    def test_describe(self) -> None:
        assert (
            UnitScale.parse({"mode": "fixed", "divisor": 1000, "display_unit": "Kbps"})
            .resolve(magnitude=1)
            .describe()
            == "÷1000 → Kbps"
        )
        # 无单位时只给除数
        assert UnitScale.parse({"mode": "fixed", "divisor": 60}).resolve(magnitude=1).describe() == "÷60"


class TestHelpers:
    def test_scale_value_none_scale_passthrough(self) -> None:
        assert scale_value(5.0, None) == 5.0
        assert scale_value(None, None) is None

    def test_scale_value_applies(self) -> None:
        scale = UnitScale.parse({"mode": "fixed", "divisor": 1000}).resolve(magnitude=1)
        assert scale_value(262468.9, scale) == pytest.approx(262.4689)

    def test_display_unit_of_fallback(self) -> None:
        assert display_unit_of(None, "bps") == "bps"
        scale = UnitScale.parse({"mode": "fixed", "divisor": 1000}).resolve(
            magnitude=1, base_unit="bps"
        )
        assert display_unit_of(scale, "bps") == "bps"  # fixed 无 display_unit -> 回退


class TestUnitScaleConfigSchema:
    def test_defaults(self) -> None:
        cfg = UnitScaleConfig()
        assert cfg.mode == "none"
        assert cfg.base == 1000
        assert cfg.divisor == 1000.0
        assert cfg.decimals is None

    def test_invalid_base_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnitScaleConfig(mode="auto", base=512)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [0, -1, 1e13])
    def test_invalid_divisor_rejected(self, bad: float) -> None:
        with pytest.raises(ValidationError):
            UnitScaleConfig(mode="fixed", divisor=bad)

    def test_decimals_range(self) -> None:
        with pytest.raises(ValidationError):
            UnitScaleConfig(decimals=11)
        with pytest.raises(ValidationError):
            UnitScaleConfig(decimals=-1)

    def test_suffix_stripped(self) -> None:
        assert UnitScaleConfig(mode="auto", suffix_unit="  bps ").suffix_unit == "bps"

    def test_to_storage_none_mode_is_null(self) -> None:
        assert UnitScaleConfig().to_storage() is None
        assert UnitScaleConfig(mode="none").to_storage() is None

    def test_to_storage_auto_omits_empties(self) -> None:
        cfg = UnitScaleConfig(mode="auto", base=1000, suffix_unit="")
        assert cfg.to_storage() == {"mode": "auto", "base": 1000}

    def test_to_storage_auto_1024_with_decimals(self) -> None:
        cfg = UnitScaleConfig(mode="auto", base=1024, suffix_unit="B", decimals=3)
        assert cfg.to_storage() == {
            "mode": "auto",
            "base": 1024,
            "suffix_unit": "B",
            "decimals": 3,
        }

    def test_to_storage_fixed_keeps_divisor_1000(self) -> None:
        """回归：divisor=1000 是最常见配置，不能被当默认值丢掉（否则退化为不换算）。"""
        cfg = UnitScaleConfig(mode="fixed", divisor=1000, display_unit="Kbps")
        stored = cfg.to_storage()
        assert stored == {"mode": "fixed", "divisor": 1000.0, "display_unit": "Kbps"}
        # 落库 -> 读回必须仍是「固定除数 1000」
        scale = UnitScale.parse(stored).resolve(magnitude=1)
        assert scale is not None
        assert scale.factor == 1000
        assert scale.unit == "Kbps"


class TestCoerceUnitConfig:
    def test_none(self) -> None:
        assert coerce_unit_config(None) is None

    def test_valid_dict(self) -> None:
        cfg = coerce_unit_config({"mode": "auto", "base": 1024})
        assert isinstance(cfg, UnitScaleConfig)
        assert cfg.base == 1024

    def test_dirty_dict_becomes_none(self) -> None:
        assert coerce_unit_config({"mode": "auto", "base": 512}) is None
        assert coerce_unit_config({"mode": "fixed", "divisor": -1}) is None

    def test_passthrough_instance(self) -> None:
        cfg = UnitScaleConfig(mode="auto")
        assert coerce_unit_config(cfg) is cfg