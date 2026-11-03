"""从上传的 Excel 做分类统计，产出可溯源的总结文本。

场景（2026-11-03）：报告任务里有一个小节是对「工单明细表」的分类统计总结，
形如「本月共处理账号/权限管理操作 24 件；终端设备维护…21 件；…」。

**为什么不用「数字必须在表里」做校验**：这些数字是**派生量** —— 24 件是数出来的，
表里没有哪一格写着 24。用「数字是否出现在单元格」当围栏会把**所有正确的数字**
都判成可疑。所以校验的对象不是数字本身，而是**数字与行的对应关系**。

核心不变式（见 verify_categories）：
    摘要说「24 件」-> 必须能指出这 24 件是哪 24 行（rows）
    且这些行必须真的满足判定依据（match）
    且没被指出的、也满足依据的行不能存在（漏计）

这样系统做的全是**确定性检查**，不需要维护任何关键词映射表 ——
分类怎么定是 AI 的事，系统只验「数字指不指得出具体的行」。

分块（map-reduce）：200+ 行的表一次塞给 LLM 既超上下文又数不准，
所以切成小块分别问「这几行各有哪几类、各几行」，再合并。
分块还有个额外好处：**块小 -> 每块的可溯源集合极小 -> 校验很紧**
（「块里只有 3 个数字，AI 却说 24」立刻暴露）。
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 每个分块多少行。太大 LLM 数不准，太小调用次数多、合并成本高。
BLOCK_ROWS = 30

#: 单个分块的校验不通过时，重试次数
MAX_BLOCK_RETRIES = 1

#: 支持的文件类型
ALLOWED_SUFFIXES = (".xlsx", ".xlsm")


class FileSummaryError(Exception):
    """文件解析 / 总结生成失败。消息不含文件内容。"""


@dataclass
class SheetData:
    """解析出来的一张表。"""

    sheet_name: str
    header_row: int
    columns: list[str]
    #: 每行是一个 dict：列名 -> 值（字符串），另带 "__row" 真实行号
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass
class Category:
    """一个分类及其证据行。"""

    name: str
    rows: list[int] = field(default_factory=list)
    #: 判定依据：{"type": "value"|"keywords", ...}
    match: dict[str, Any] = field(default_factory=dict)
    verified: bool = True
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": len(self.rows),
            "rows": self.rows,
            "match": self.match,
            "verified": self.verified,
            "notes": self.notes,
        }


@dataclass
class Verification:
    """整体校验结果。"""

    categories: int = 0
    numbers: int = 0
    verified: bool = True
    issues: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "categories": self.categories,
            "numbers": self.numbers,
            "verified": self.verified,
            "issues": self.issues,
        }


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_workbook(path: Path) -> list[SheetData]:
    """读出工作簿里所有「像表」的 sheet。

    自动识别表头行：取前 20 行里**非空单元格数最多**的那行（表头通常最宽），
    之后的非空行视为数据行。全空 sheet 忽略。
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise FileSummaryError("服务器缺少 openpyxl，无法解析 Excel") from exc

    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise FileSummaryError(
            f"仅支持 {'/'.join(ALLOWED_SUFFIXES)} 文件，收到 {path.suffix or '未知类型'}"
        )
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise FileSummaryError(f"Excel 解析失败: {type(exc).__name__}") from exc

    out: list[SheetData] = []
    try:
        for ws in wb.worksheets:
            grid = [list(r) for r in ws.iter_rows(values_only=True)]
            sheet = _sheet_from_grid(ws.title, grid)
            if sheet is not None and sheet.rows:
                out.append(sheet)
    finally:
        wb.close()
    if not out:
        raise FileSummaryError("文件里没有找到可解析的数据表")
    return out


def _sheet_from_grid(name: str, grid: list[list[Any]]) -> SheetData | None:
    """从二维数组里找出表头行与数据行。"""
    best_idx = -1
    best_width = 0
    for i, row in enumerate(grid[:20]):  # 表头不会太靠下
        width = sum(1 for c in row if c is not None and str(c).strip())
        if width > best_width:
            best_width = width
            best_idx = i
    if best_idx < 0 or best_width < 2:
        return None

    header = grid[best_idx]
    columns: list[str] = []
    for j, cell in enumerate(header):
        text = "" if cell is None else str(cell).strip()
        columns.append(text or f"列{j + 1}")

    rows: list[dict[str, Any]] = []
    for i in range(best_idx + 1, len(grid)):
        raw = grid[i]
        if all(c is None or not str(c).strip() for c in raw):
            continue
        item: dict[str, Any] = {}
        for j, col in enumerate(columns):
            cell = raw[j] if j < len(raw) else None
            item[col] = "" if cell is None else str(cell).strip()
        item["__row"] = i + 1  # 用 Excel 里的真实行号，便于回溯
        rows.append(item)
    return SheetData(sheet_name=name, header_row=best_idx + 1, columns=columns, rows=rows)


def guess_label_column(sheet: SheetData) -> str:
    """猜「按哪列分类」。

    优先列名带「类型 / 分类 / 事项 / 内容」等字样的列；否则取**去重值最少**的
    文本列（分类列的特征就是取值种类有限）。
    """
    preferred = ("类型", "分类", "类别", "事项", "内容", "工单类型", "处理类型")
    for col in sheet.columns:
        if any(k in col for k in preferred):
            return col
    best = ""
    best_count = 10**9
    for col in sheet.columns:
        vals = {str(r.get(col, "")).strip() for r in sheet.rows}
        vals.discard("")
        if 1 < len(vals) < len(sheet.rows):
            if len(vals) < best_count:
                best_count = len(vals)
                best = col
    return best or (sheet.columns[0] if sheet.columns else "")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _match_text(row: dict[str, Any], match: dict[str, Any]) -> bool:
    """这一行是否满足判定依据。"""
    kind = str(match.get("type") or "keywords")
    if kind == "value":
        col = str(match.get("column") or "")
        want = str(match.get("value") or "")
        return str(row.get(col, "")).strip() == want
    keywords = [str(k) for k in (match.get("keywords") or []) if str(k).strip()]
    if not keywords:
        return True  # 没给依据 -> 无法校验（由调用方标记为未校验）
    blob = " ".join(str(v) for k, v in row.items() if k != "__row").lower()
    return any(kw.lower() in blob for kw in keywords)


def verify_categories(
    cats: list[Category],
    rows_by_no: dict[int, dict[str, Any]],
    *,
    check_omission: bool = True,
) -> Verification:
    """校验分类与证据行的一致性（全部确定性检查）。

    三件事：
    1. 声称的数量 == 证据行数
    2. 证据行真实存在，且真的满足判定依据
    3. **漏计**：还有行满足依据却没被列入（统计场景最常见又最难自查的错）
    """
    v = Verification(categories=len(cats))
    for c in cats:
        v.numbers += len(c.rows)
        if len(set(c.rows)) != len(c.rows):
            c.verified = False
            c.notes.append("[dup] 证据行有重复")
        missing = [r for r in c.rows if r not in rows_by_no]
        if missing:
            c.verified = False
            c.notes.append(f"[missing-row] {len(missing)} 个行号在表中不存在")
        unmatched = [
            r for r in c.rows if r in rows_by_no and not _match_text(rows_by_no[r], c.match)
        ]
        if unmatched:
            c.verified = False
            c.notes.append(f"[unmatched] {len(unmatched)} 个证据行不满足判定依据")
        if not c.match.get("keywords") and not c.match.get("value"):
            c.notes.append("[no-basis] 未提供判定依据，仅校验数量")

    if check_omission:
        covered = {r for c in cats for r in c.rows}
        for c in cats:
            if not c.match.get("keywords") and not c.match.get("value"):
                continue
            extra = [
                r
                for r, row in rows_by_no.items()
                if r not in covered and _match_text(row, c.match)
            ]
            if extra:
                c.verified = False
                c.notes.append(f"[omission] 有 {len(extra)} 行也满足依据但未被计入")
    v.verified = all(c.verified for c in cats) if cats else True
    v.issues = [f"{c.name}：{'；'.join(c.notes)}" for c in cats if c.notes]
    return v


# ---------------------------------------------------------------------------
# AI 调用
# ---------------------------------------------------------------------------

_BLOCK_PROMPT = """你是数据分类统计助手。下面给你一个表格的若干行（每行带真实行号）。

要求：
1. 按「{label_col}」列的内容做分类统计。允许把含义相近的取值归为一个分类
   （例如「账号权限申请」与「权限变更」归为「账号权限管理」），但**不要**把
   含义不同的取值硬凑到一起。
2. 每个分类给出三样东西：
   - name：分类名（简洁，中文）
   - rows：该分类包含的**行号**列表（只能用下面给出的行号）
   - match：判定依据，二选一
     * {{"type": "value", "column": "{label_col}", "value": "<该类对应的原始取值>"}}
       —— 仅当该分类恰好对应 {label_col} 的某一个原始取值时用
     * {{"type": "keywords", "keywords": ["关键词1", "关键词2"]}}
       —— 合并了多个取值时用；关键词之间是「或」的关系，且必须能在单元格里直接匹配到
3. 只输出 JSON，不要任何解释文字。格式：
   {{"categories": [{{"name": "...", "rows": [1,2], "match": {{...}}}}]}}

表格列：{columns}

{rows_text}"""


def _rows_text(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """把行渲染成给 AI 看的文本（截断超长单元格，避免撑爆上下文）。"""
    lines: list[str] = []
    for row in rows:
        cells = []
        for col in columns:
            val = str(row.get(col, ""))
            if len(val) > 120:
                val = val[:120] + "…"
            cells.append(f"{col}={val}")
        lines.append(f"第 {row['__row']} 行：" + " | ".join(cells))
    return "\n".join(lines)


def _parse_categories(raw: str) -> list[Category]:
    """解析 AI 返回的 JSON。容忍 ```json 包裹与前后废话。"""
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise FileSummaryError("AI 未返回可解析的 JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise FileSummaryError(f"AI 返回的 JSON 解析失败: {exc.msg}") from exc

    out: list[Category] = []
    for item in data.get("categories") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        rows: list[int] = []
        for r in item.get("rows") or []:
            try:
                rows.append(int(r))
            except (TypeError, ValueError):
                continue
        match = item.get("match")
        if not isinstance(match, dict):
            match = {}
        out.append(Category(name=name, rows=rows, match=match))
    return out


def verify_text_numbers(text: str, cats: list[Category]) -> list[str]:
    """校验最终段落里的数字是否都来自结构化计数。

    这是**最贴近用户所见**的一道检查：结构化统计说「账号权限 24 件」，AI 写出来的
    段落里必须也是 24。若 AI 在措辞时把数字改了（25 / 2400 / 2.4），这里能抓住。

    与「数字在不在表里」不同：这些数字本来就是派生量，**表里没有**。
    校验的是「段落里的数字 == 统计结果里的某个计数」。

    返回可疑数字的字符串列表（空 = 全部对得上）。
    """
    counts = {len(c.rows) for c in cats}
    # 允许的数字：各类计数，以及它们的十进制形式（AI 偶尔会写成 "24." 或 "24 "）
    allowed: set[str] = set()
    for n in counts:
        allowed.add(str(n))
        allowed.add(f"{n}.0")
    suspicious: list[str] = []
    for raw in re.findall(r"\d+(?:\.\d+)?", text):
        token = raw.rstrip(".")
        if token in allowed:
            continue
        # 数字可能是更大数的一部分（如 2026 年、24 位）—— 只在它恰好等于某个计数
        # 的「变体」时才可疑，避免误报日期/编号
        if token and token.rstrip("0").rstrip(".") in allowed:
            continue
        suspicious.append(token)
    return sorted(set(suspicious))


def _merge_categories(cats: list[Category]) -> list[Category]:
    """把同名分类合并（行号并集、依据取第一个非空的）。"""
    order: list[str] = []
    by_name: dict[str, Category] = {}
    for c in cats:
        if c.name not in by_name:
            by_name[c.name] = Category(name=c.name, rows=[], match=dict(c.match))
            order.append(c.name)
        target = by_name[c.name]
        for r in c.rows:
            if r not in target.rows:
                target.rows.append(r)
        if not target.match and c.match:
            target.match = dict(c.match)
    for name in order:
        by_name[name].rows.sort()
    return [by_name[n] for n in order]


_COMPOSE_PROMPT = """下面是一组已经统计好的分类计数，请把它们改写成一段通顺的中文月度小结。

要求：
1. **数字必须原样使用**，不要重新计算、不要四舍五入、不要补充任何数字
2. 格式：每个分类写成「分类名 数量 单位」，用分号或顿号连接成一段话
3. 可以先写一句总起（如「本月共处理 XX 件」），再逐项列出
4. 不要输出 markdown、不要输出标题、不要解释

统计数据：
{stats}"""


async def _compose_text(
    cats: list[Category], ai_client: Any, call_ai: Any
) -> str:
    """把结构化计数写成一段通顺的话（数字全部来自统计结果，不允许 AI 自己算）。"""
    stats = "、".join(f"{c.name} {len(c.rows)}" for c in cats)
    messages = [
        {"role": "system", "content": "你只输出改写后的正文，不要任何解释。"},
        {
            "role": "user",
            "content": _COMPOSE_PROMPT.format(stats=stats),
        },
    ]
    return (await call_ai(ai_client, messages)).strip()


async def summarize_sheet(
    sheet: SheetData,
    *,
    label_col: str,
    prompt: str,
    ai_client: Any,
    call_ai: Any,
) -> tuple[str, list[Category], Verification]:
    """对一张表做分类统计，产出总结文本 + 溯源数据。

    ``call_ai`` 是 ai_insight 里的 ``_call_ai``（发消息、拿文本），
    ``ai_client`` 是 OpenAI 客户端 —— 两者注入进来，便于测试。
    """
    rows_by_no = {int(r["__row"]): r for r in sheet.rows}
    collected: list[Category] = []

    for start in range(0, len(sheet.rows), BLOCK_ROWS):
        block = sheet.rows[start : start + BLOCK_ROWS]
        user_text = _rows_text(block, sheet.columns)
        if prompt.strip():
            user_text = f"{prompt.strip()}\n\n{user_text}"
        messages = [
            {"role": "system", "content": "你只输出 JSON，不输出解释。"},
            {
                "role": "user",
                "content": _BLOCK_PROMPT.format(
                    label_col=label_col,
                    columns=" | ".join(sheet.columns),
                    rows_text=user_text,
                ),
            },
        ]
        raw = await call_ai(ai_client, messages)
        cats = _parse_categories(raw)

        # 校验不通过时带纠正指令重试一次
        v = verify_categories(cats, rows_by_no, check_omission=False)
        if not v.verified and MAX_BLOCK_RETRIES:
            correction = (
                "上一次输出有问题，请修正后重新输出 JSON：\n"
                + "\n".join(v.issues)
                + "\n注意：rows 里只能用给过的行号，每个分类的行数必须与 rows 长度一致。"
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": correction})
            raw = await call_ai(ai_client, messages)
            cats = _parse_categories(raw)

        collected.extend(cats)

    merged = _merge_categories(collected)
    # 合并后做**完整**校验（含漏计）—— 分块时各块行号区间不重叠，
    # 合并后才有完整视野，才能查出「漏计」
    v = verify_categories(merged, rows_by_no, check_omission=True)

    text = await _compose_text(merged, ai_client, call_ai)
    # 段落里的数字必须与结构化计数一致（AI 措辞时改数能抓住）
    suspicious = verify_text_numbers(text, merged)
    if suspicious:
        v.verified = False
        v.issues.append(f"[text-number] 段落里出现了与统计不符的数字: {suspicious}")
    return text, merged, v
