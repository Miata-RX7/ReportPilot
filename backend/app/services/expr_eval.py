"""受限算术表达式求值（计算型指标用，2026-11-12）。

计算型指标的算式只允许：**数字 / 变量 / + - * / / 括号**。

**为什么不用 eval()**：
1. ``eval`` 能执行任意代码（``__import__`` / ``open`` ...），哪怕公式是admin 写的
   也不该在服务端跑未受限的代码
2. ``**`` 会被当成幂运算 -> ``2**999999999`` 直接把 CPU 打满
3. 裸eval 出来的错误信息没有位置，用户看不懂

所以这里用 ``ast.parse`` 解析后**白名单遍历**：只认下面这些节点，其余一律报错，
并且错误信息精确到字符位置。

运算符语义与 Python 一致（``/`` 是真除法），与用户写公式的直觉一致。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


class ExpressionError(ValueError):
    """算式非法（语法错误 / 不支持的写法 / 变量未定义）。"""


#: 允许的 AST 节点类型
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Constant,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.USub,
    ast.UAdd,
)

#: 允许的二元运算符
_ALLOWED_BINOPS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}

#: 允许的一元运算符
_ALLOWED_UNARYOPS: dict[type[ast.unaryop], str] = {
    ast.USub: "-",
    ast.UAdd: "+",
}

#: 数字常量的上限（防止 1e999 这类把内存打爆）
_MAX_NUMBER = 1e15


@dataclass
class ParsedExpression:
    """解析后的算式（可重复求值）。"""

    source: str
    tree: ast.Expression
    #: 公式里用到的变量名（按出现顺序去重）
    variables: list[str]


def parse_expression(formula: str) -> ParsedExpression:
    """解析算式并做静态校验。

    校验内容：语法合法、只含白名单节点、变量名合法（ASCII 标识符）。
    **不检查变量是否已定义** —— 那是业务层的校验（定义表与公式要对得上）。
    """
    src = (formula or "").strip()
    if not src:
        raise ExpressionError("算式不能为空")
    if len(src) > 500:
        raise ExpressionError("算式过长（上限 500 字符）")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        pos = exc.offset if exc.offset is not None else 0
        raise ExpressionError(
            f"第 {pos} 字符附近语法错误：{exc.msg}"
        ) from None

    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"第 {getattr(node, 'col_offset', 0) + 1} 字符附近："
                f"不支持的写法 {type(node).__name__}"
                "（只允许数字、变量、+ - * / 和括号）"
            )
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExpressionError(
                    f"第 {node.col_offset + 1} 字符附近：只允许数字常量"
                )
            if abs(node.value) > _MAX_NUMBER:
                raise ExpressionError("数字常量过大")
        if isinstance(node, ast.Name):
            if node.id not in names:
                names.append(node.id)
        if isinstance(node, ast.BinOp):
            op = type(node.op)
            if op not in _ALLOWED_BINOPS:
                raise ExpressionError(
                    f"第 {node.col_offset + 1} 字符附近："
                    f"不支持的运算符 '{_OPERATOR_TEXT.get(op, op.__name__)}'"
                    "（只允许 + - * /）"
                )
        if isinstance(node, ast.UnaryOp):
            op = type(node.op)
            if op not in _ALLOWED_UNARYOPS:
                raise ExpressionError(
                    f"第 {node.col_offset + 1} 字符附近：不支持的一元运算符"
                )

    return ParsedExpression(source=src, tree=tree, variables=names)


#: 运算符的可读名字（报错用）
_OPERATOR_TEXT: dict[type[ast.operator], str] = {
    ast.Pow: "**",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.LShift: "<<",
    ast.RShift: ">>",
}


def evaluate(parsed: ParsedExpression, variables: dict[str, float]) -> float:
    """对已解析的算式求值。

    ``variables`` 是变量名 -> 数值 的映射。变量不在映射里 -> 报错（未定义）。
    除以 0 -> 报错（不返回 inf/nan，静默的错误值比报错更糟）。
    """
    return _eval_node(parsed.tree.body, variables, parsed.source)


def _eval_node(node: ast.AST, variables: dict[str, float], source: str) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ExpressionError(f"变量 '{node.id}' 未定义")
        value = variables[node.id]
        if value is None:
            raise ExpressionError(f"变量 '{node.id}' 没有值")
        return float(value)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, variables, source)
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, variables, source)
        right = _eval_node(node.right, variables, source)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ExpressionError("除数为 0")
            return left / right
        raise ExpressionError("不支持的运算符")

    raise ExpressionError(f"不支持的写法 {type(node).__name__}")


def evaluate_formula(formula: str, variables: dict[str, float]) -> float:
    """便捷入口：解析 + 求值。"""
    return evaluate(parse_expression(formula), variables)
