"""「仅确认不发送」与报告预览（2026-11-12）。

背景：确认人下载报告、线下改完自己发给收件人了 -> 系统不该再发、也不该继续催办，
更不能永远卡在 pending_confirmation。
"""

from app.models.report import REPORT_STATUSES
from app.services.report_service import ALLOWED_ACTIONS


class TestConfirmedNoPushState:
    def test_status_exists(self) -> None:
        assert "confirmed_no_push" in REPORT_STATUSES

    def test_is_terminal(self) -> None:
        """终态：没有任何后续动作（不可撤销 —— 有问题重新手动触发）。"""
        assert ALLOWED_ACTIONS["confirmed_no_push"] == set()

    def test_not_in_pending_confirmation_actions(self) -> None:
        """待确认态允许的动作里不应有 confirm_only 之外的推送类动作。"""
        assert "push" not in ALLOWED_ACTIONS["pending_confirmation"]

    def test_confirmed_still_allows_push(self) -> None:
        """普通「确认并发送」路径不受影响。"""
        assert ALLOWED_ACTIONS["confirmed"] == {"push"}


class TestConfirmOnlySource:
    """用源码断言锁住关键行为（这类状态机 bug 单测函数签名测不出来）。"""

    def test_confirm_only_does_not_push(self) -> None:
        import inspect

        from app.services import report_service

        src = inspect.getsource(report_service.confirm_only)
        assert "push_confirmed" not in src, (
            "confirm_only 里不能推送 —— 「仅确认不发送」就是不发送"
        )
        assert "confirmed_no_push" in src, (
            "confirm_only 必须落到 confirmed_no_push 终态"
        )

    def test_confirm_only_consumes_token(self) -> None:
        import inspect

        from app.services import report_service

        src = inspect.getsource(report_service.confirm_only)
        assert "confirmation_token = None" in src, (
            "confirm_only 也要消费一次性 token（链接用掉就不能再确认）"
        )


class TestPdfPreviewFields:
    def test_report_has_preview_columns(self) -> None:
        from app.models.report import Report

        cols = Report.__table__.c
        for name in ("pdf_path", "docx_downloaded_at", "confirmed_no_push_at"):
            assert name in cols, f"reports 缺少 {name} 列"
