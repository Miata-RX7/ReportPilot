"""确认页上传 Excel -> 分类统计总结 -> 快照 -> 报告渲染（2026-11-03）。

覆盖三件事：
1. **校验的是「数字指不指得出具体的行」**，而不是「数字在不在表里」——
   这些数字是派生量（24 件是数出来的），用「数字是否出现在单元格」当围栏
   会把所有正确数字都判成可疑。
2. 漏计能被查出来（这是统计场景最常见又最难自查的错误）。
3. 「总结_」占位不参与模板绑定，渲染时由确认快照注入。
"""

from pathlib import Path

import pytest

from app.services import file_summary
from app.services.docx_renderer import (
    RenderError,
    check_context,
    is_summary_placeholder,
)
from app.services.file_summary import (
    Category,
    SheetData,
    _merge_categories,
    _parse_categories,
    guess_label_column,
    verify_categories,
)


def _sheet() -> SheetData:
    rows = [
        {"__row": 2, "处理类型": "账号权限", "处理内容": "账号权限申请"},
        {"__row": 3, "处理类型": "终端维护", "处理内容": "终端重装"},
        {"__row": 4, "处理类型": "账号权限", "处理内容": "权限变更"},
        {"__row": 5, "处理类型": "现场巡检", "处理内容": "机房巡检"},
        {"__row": 6, "处理类型": "终端维护", "处理内容": "补丁升级"},
    ]
    return SheetData(
        sheet_name="Sheet1",
        header_row=1,
        columns=["处理类型", "处理内容"],
        rows=rows,
    )


class TestVerifyCategories:
    def test_count_must_match_rows(self) -> None:
        """声称 3 件却只列了 2 行 -> 不通过。"""
        sheet = _sheet()
        rows = {int(r["__row"]): r for r in sheet.rows}
        cats = [
            Category(
                name="账号权限管理",
                rows=[2],
                match={"type": "keywords", "keywords": ["账号", "权限"]},
            )
        ]
        v = verify_categories(cats, rows)
        # 第 4 行（权限变更）也含「权限」却没被计入 -> 漏计
        assert not v.verified
        assert any("[total]" in i for i in v.issues)

    def test_missing_row_number_is_detected(self) -> None:
        """证据行号在表里不存在 -> 不通过。"""
        sheet = _sheet()
        rows = {int(r["__row"]): r for r in sheet.rows}
        cats = [
            Category(
                name="X",
                rows=[2, 999],
                match={"type": "keywords", "keywords": ["账号"]},
            )
        ]
        v = verify_categories(cats, rows)
        assert not v.verified
        assert any("[missing-row]" in i for i in v.issues)

    def test_row_not_matching_criteria_is_tolerated(self) -> None:
        """证据行不满足判定依据 -> **不再**判不通过（用户要求：数量对上就行）。

        这类事项本身语义模糊，人为分都未必准，用机器卡「这行该不该属于这一类」
        只会制造大量误报。
        """
        sheet = _sheet()
        rows = {int(r["__row"]): r for r in sheet.rows}
        cats = [
            Category(
                name="账号权限管理",
                rows=[2, 3, 4, 5, 6],  # 第 3 行是「终端重装」，不含账号/权限
                match={"type": "keywords", "keywords": ["账号", "权限"]},
            )
        ]
        v = verify_categories(cats, rows)
        assert v.verified, v.issues
        assert not any("[unmatched]" in i for i in v.issues)
    def test_correct_classification_passes(self) -> None:
        """正确的分类应当通过（且不报漏计）。"""
        sheet = _sheet()
        rows = {int(r["__row"]): r for r in sheet.rows}
        cats = [
            Category(
                name="账号权限管理",
                rows=[2, 4],
                match={"type": "keywords", "keywords": ["账号", "权限"]},
            ),
            Category(
                name="终端维护",
                rows=[3, 6],
                match={"type": "keywords", "keywords": ["终端", "补丁"]},
            ),
            Category(
                name="现场巡检",
                rows=[5],
                match={"type": "keywords", "keywords": ["巡检"]},
            ),
        ]
        v = verify_categories(cats, rows)
        assert v.verified, v.issues
        assert v.numbers == 5

    def test_value_match_is_exact(self) -> None:
        """type=value 时按列值精确匹配。"""
        sheet = _sheet()
        rows = {int(r["__row"]): r for r in sheet.rows}
        cats = [
            Category(
                name="终端维护",
                rows=[2, 3, 4, 5, 6],  # 覆盖全部行：新口径校验的是总账
                match={"type": "value", "column": "处理类型", "value": "终端维护"},
            )
        ]
        v = verify_categories(cats, rows)
        assert v.verified, v.issues

    def test_no_match_basis_is_fine(self) -> None:
        """没给依据也不再报「未提供判定依据」—— 只看数量。"""
        sheet = _sheet()
        rows = {int(r["__row"]): r for r in sheet.rows}
        cats = [Category(name="随便", rows=[2, 3, 4, 5, 6])]
        v = verify_categories(cats, rows)
        assert v.verified, v.issues
        assert not any("[no-basis]" in n for c in cats for n in c.notes)
class TestVerifyTextNumbers:
    """段落里的数字必须与结构化计数一致（AI 措辞时改数能抓住）。"""

    def _cats(self):
        from app.services.file_summary import Category

        return [
            Category(name="账号权限管理", rows=list(range(2, 26))),
            Category(name="终端维护", rows=list(range(30, 51))),
        ]

    def test_matching_numbers_pass(self) -> None:
        from app.services.file_summary import verify_text_numbers

        text = "本月共处理账号权限管理 24 件；终端设备维护 21 件。"
        assert verify_text_numbers(text, self._cats()) == []

    def test_wrong_number_is_flagged(self) -> None:
        from app.services.file_summary import verify_text_numbers

        text = "本月共处理账号权限管理 25 件。"
        bad = verify_text_numbers(text, self._cats())
        assert "25" in bad

    def test_trailing_dot_tolerated(self) -> None:
        from app.services.file_summary import verify_text_numbers

        text = "共 24. 件"
        assert verify_text_numbers(text, self._cats()) == []

    def test_unrelated_number_flagged(self) -> None:
        """统计里没有的数字（AI 凭空加的）要能抓住。"""
        from app.services.file_summary import verify_text_numbers

        text = "共处理 24 件，其中 7 件为紧急。"
        assert "7" in verify_text_numbers(text, self._cats())


class TestMergeCategories:
    def test_same_name_merges_rows(self) -> None:
        a = Category(name="账号权限", rows=[2, 3], match={"keywords": ["账号"]})
        b = Category(name="账号权限", rows=[4], match={})
        merged = _merge_categories([a, b])
        assert len(merged) == 1
        assert merged[0].rows == [2, 3, 4]

    def test_order_is_preserved(self) -> None:
        cats = [
            Category(name="B", rows=[1]),
            Category(name="A", rows=[2]),
        ]
        assert [c.name for c in _merge_categories(cats)] == ["B", "A"]


class TestParseCategories:
    def test_plain_json(self) -> None:
        cats = _parse_categories(
            '{"categories": [{"name": "X", "rows": [1,2], "match": {"keywords": ["a"]}}]}'
        )
        assert len(cats) == 1
        assert cats[0].name == "X"
        assert cats[0].rows == [1, 2]

    def test_markdown_fenced(self) -> None:
        cats = _parse_categories(
            '```json\n{"categories": [{"name": "X", "rows": [1]}]}\n```'
        )
        assert len(cats) == 1

    def test_garbage_raises(self) -> None:
        with pytest.raises(file_summary.FileSummaryError):
            _parse_categories("没有 JSON 的一段话")

    def test_non_numeric_rows_dropped(self) -> None:
        cats = _parse_categories(
            '{"categories": [{"name": "X", "rows": [1, "abc", null]}]}'
        )
        assert cats[0].rows == [1]


class TestSheetFromGrid:
    def test_detects_header_and_rows(self) -> None:
        grid = [
            ["说明文字", None, None],
            ["工单号", "处理类型", "处理内容"],
            ["INC-1", "账号权限", "账号权限申请"],
            [None, None, None],
            ["INC-2", "终端维护", "终端重装"],
        ]
        sheet = file_summary._sheet_from_grid("S", grid)
        assert sheet is not None
        assert sheet.columns == ["工单号", "处理类型", "处理内容"]
        assert sheet.row_count == 2
        # 行号用 Excel 真实行号，便于回溯
        assert [r["__row"] for r in sheet.rows] == [3, 5]

    def test_empty_sheet_ignored(self) -> None:
        assert file_summary._sheet_from_grid("S", [[None, None], [None, None]]) is None

    def test_guess_label_column_prefers_type(self) -> None:
        sheet = _sheet()
        assert guess_label_column(sheet) == "处理类型"

    def test_guess_label_column_falls_back_to_low_cardinality(self) -> None:
        sheet = SheetData(
            sheet_name="S",
            header_row=1,
            columns=["编号", "类别"],
            rows=[
                {"__row": 2, "编号": "A", "类别": "x"},
                {"__row": 3, "编号": "B", "类别": "x"},
                {"__row": 4, "编号": "C", "类别": "y"},
            ],
        )
        assert guess_label_column(sheet) == "类别"


class TestSummaryPlaceholder:
    """「总结_」占位不参与模板绑定，渲染时由确认快照注入。

    注意：**docx_renderer 必须在模块级导入**。若放在测试体内首次导入，
    它会连带导入 docxtpl 并改变全局的 asyncio 事件循环状态 —— 本文件按字母序
    排在 test_metrics_api.py 之前，于是污染后面的用例，出现
    ``sniffio.AsyncLibraryNotFoundError`` 这类随机失败（实测踩过）。
    """

    def test_summary_placeholder_detection(self) -> None:
        assert is_summary_placeholder("summary_workorder")
        assert not is_summary_placeholder("CPU使用率")
        assert not is_summary_placeholder("insight_1")

    def test_check_context_ignores_missing_summary_placeholder(self) -> None:
        """「总结_」占位缺失不算错误（它由确认快照运行时注入）。"""
        check_context({"CPU使用率"}, {"CPU使用率": "1"})
        check_context({"CPU使用率", "summary_workorder"}, {"CPU使用率": "1"})
        with pytest.raises(RenderError):
            check_context({"CPU使用率"}, {})
