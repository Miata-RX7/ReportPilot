"""受限算式求值器的单测（2026-11-12）。

重点覆盖两类：
1. 正常算术（含括号优先级）
2. **拒绝危险/越界的写法** —— 这是不用 eval() 的全部意义所在
"""

import pytest

from app.services.expr_eval import (
    ExpressionError,
    evaluate_formula,
    parse_expression,
)


class TestArithmetic:
    def test_simple(self) -> None:
        assert evaluate_formula("1 + 2", {}) == 3.0
        assert evaluate_formula("10 - 3", {}) == 7.0
        assert evaluate_formula("6 * 7", {}) == 42.0
        assert evaluate_formula("10 / 4", {}) == 2.5

    def test_parentheses_change_precedence(self) -> None:
        """(a-b)*c 与 a-b*c 是不同的 —— 括号必须生效。"""
        v = {"a": 10.0, "b": 3.0, "c": 2.0}
        assert evaluate_formula("(a - b) * c", v) == 14.0
        assert evaluate_formula("a - b * c", v) == 4.0

    def test_nested_parentheses(self) -> None:
        assert evaluate_formula("((1 + 2) * (3 + 4)) / 7", {}) == 3.0

    def test_real_use_case(self) -> None:
        """(100 - 可用率%) / 100 * 周期分钟数 = 不可用分钟数"""
        v = {"a": 99.5, "minutes": 44640.0}
        assert evaluate_formula("(100 - a) / 100 * minutes", v) == pytest.approx(223.2)

    def test_numeric_constant_allowed(self) -> None:
        """数字常量必须允许（(100 - a) 里的 100）。"""
        assert evaluate_formula("100 - 99.5", {}) == pytest.approx(0.5)

    def test_unary_minus(self) -> None:
        assert evaluate_formula("0 - a", {"a": 5.0}) == -5.0
        assert evaluate_formula("-a + 10", {"a": 5.0}) == 5.0


class TestVariables:
    def test_collects_variables_in_order(self) -> None:
        p = parse_expression("b + a * c")
        assert p.variables == ["b", "a", "c"]

    def test_undefined_variable_raises(self) -> None:
        with pytest.raises(ExpressionError, match="未定义"):
            evaluate_formula("a + b", {"a": 1.0})

    def test_variable_value_none_raises(self) -> None:
        with pytest.raises(ExpressionError, match="没有值"):
            evaluate_formula("a", {"a": None})  # type: ignore[dict-item]


class TestRejects:
    """必须被拒绝的写法（不用 eval() 的意义）。"""

    @pytest.mark.parametrize(
        "formula",
        [
            "__import__(\'os\').system(\'ls\')",
            "a.__class__",
            "(1).__class__",
            "a[0]",
            "lambda x: x",
            "a if b else c",
        ],
    )
    def test_dangerous_forms_rejected(self, formula: str) -> None:
        with pytest.raises(ExpressionError):
            evaluate_formula(formula, {"a": 1.0, "b": 1.0, "c": 1.0})

    def test_power_operator_rejected(self) -> None:
        """`**` 会被当成幂运算 -> DoS，必须拒绝。"""
        with pytest.raises(ExpressionError, match="不支持的运算符"):
            evaluate_formula("2 ** 999999999", {})

    @pytest.mark.parametrize("formula", ["a // b", "a % b", "a & b", "a | b", "a ^ b"])
    def test_other_operators_rejected(self, formula: str) -> None:
        with pytest.raises(ExpressionError):
            evaluate_formula(formula, {"a": 1.0, "b": 1.0})

    def test_division_by_zero_raises(self) -> None:
        """除零必须报错，不能静默返回 inf/nan。"""
        with pytest.raises(ExpressionError, match="除数为 0"):
            evaluate_formula("a / b", {"a": 1.0, "b": 0.0})

    def test_empty_formula_rejected(self) -> None:
        with pytest.raises(ExpressionError, match="不能为空"):
            evaluate_formula("   ", {})

    def test_syntax_error_has_position(self) -> None:
        with pytest.raises(ExpressionError, match="第"):
            evaluate_formula("1 + * 2", {})

    def test_too_long_rejected(self) -> None:
        with pytest.raises(ExpressionError, match="过长"):
            evaluate_formula("1+" * 300 + "1", {})
