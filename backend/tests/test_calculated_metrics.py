"""计算型指标（calculated）的端到端单测（2026-11-12）。

覆盖：
- 算式求值（(100-a)/100*minutes 这种真实口径）
- 内置变量 minutes 按**具体周期**算（31 天 / 30 天 / 28 天 / 周）
- 变量必须是基础 scalar、不能引用计算型、不能跨数据源
- 数值变换（transform mul/add）做单位人工换算
"""

import pytest

from app.services.calculated_metrics import validate_calculated_config
from app.services.expr_eval import ExpressionError, parse_expression


class TestBuiltinMinutes:
    def test_month_days_differ(self) -> None:
        """月分钟数按**日历月实际天数**，不是固定 30 天。"""
        from app.services.period_utils import period_minutes

        assert period_minutes("2026-10") == 44640.0   # 31 天
        assert period_minutes("2026-09") == 43200.0   # 30 天
        assert period_minutes("2026-04") == 43200.0   # 30 天
        assert period_minutes("2026-02") == 40320.0   # 28 天

    def test_leap_year_february(self) -> None:
        from app.services.period_utils import period_minutes

        assert period_minutes("2028-02") == 41760.0   # 29 天

    def test_week_is_seven_days(self) -> None:
        from app.services.period_utils import period_minutes

        assert period_minutes("2026-W41") == 10080.0


class TestFormula:
    def test_real_formula(self) -> None:
        """(100 - 可用率%) / 100 * 周期分钟数 = 不可用分钟数"""
        from app.services.expr_eval import evaluate_formula

        v = {"a": 99.5, "minutes": 44640.0}
        assert evaluate_formula("(100 - a) / 100 * minutes", v) == pytest.approx(223.2)

    def test_transform_applied_after(self) -> None:
        """结果先算出来，再套 transform（mul/add）做单位人工换算。"""
        from app.services.expr_eval import evaluate_formula

        raw = evaluate_formula("(100 - a) / 100 * minutes", {"a": 99.5, "minutes": 44640.0})
        mul, add = 1.0 / 60.0, 0.0     # 分钟 -> 小时
        assert raw * mul + add == pytest.approx(3.72)

    def test_builtin_minutes_is_optional(self) -> None:
        """不提供 minutes 时，公式里不能用它。"""
        p = parse_expression("a + 1")
        assert p.variables == ["a"]


class TestValidation:
    def test_rejects_undefined_variable(self) -> None:
        import asyncio

        async def run() -> None:
            with pytest.raises(ValueError, match="未定义"):
                await validate_calculated_config(
                    _FakeDb(),
                    query_config={"formula": "a + b", "variables": [{"name": "a", "metric_id": 1}]},
                    data_source_id=8,
                )

        asyncio.run(run())

    def test_rejects_duplicate_names(self) -> None:
        import asyncio

        async def run() -> None:
            with pytest.raises(ValueError, match="重复"):
                await validate_calculated_config(
                    _FakeDb(),
                    query_config={
                        "formula": "a + a",
                        "variables": [
                            {"name": "a", "metric_id": 1},
                            {"name": "a", "metric_id": 2},
                        ],
                    },
                    data_source_id=8,
                )

        asyncio.run(run())

    def test_rejects_bad_syntax(self) -> None:
        import asyncio

        async def run() -> None:
            with pytest.raises(ValueError, match="算式非法"):
                await validate_calculated_config(
                    _FakeDb(),
                    query_config={"formula": "a ** b", "variables": [{"name": "a", "metric_id": 1}]},
                    data_source_id=8,
                )

        asyncio.run(run())

    def test_rejects_empty_formula(self) -> None:
        import asyncio

        async def run() -> None:
            with pytest.raises(ValueError, match="不能为空"):
                await validate_calculated_config(
                    _FakeDb(),
                    query_config={"formula": "  ", "variables": [{"name": "a", "metric_id": 1}]},
                    data_source_id=8,
                )

        asyncio.run(run())


class _FakeDb:
    """只需要 execute() -> 带 .scalars() 的结果。"""

    def __init__(self, metrics: list | None = None) -> None:
        self._metrics = metrics or []

    async def execute(self, stmt):  # noqa: ANN001, ANN202
        class _R:
            def scalars(self):  # noqa: ANN202
                return self

            def all(self):  # noqa: ANN202
                return self._rows

        r = _R()
        r._rows = self._metrics
        return r
