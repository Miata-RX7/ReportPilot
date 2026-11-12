"""报告任务的统计周期规则（2026-11-12 回归）。

背景：``task_service.create_task`` 里 ``validate_period_rule`` 的**返回值被丢弃**了
—— 校验了 (granularity, offset)，但从没写进 ReportTask 模型，于是新建任务一律落库
成模型默认值 (month, 0) = 「本期（按触发时间）」。用户在表单里选的「上月 / 上周」
全部丢失（``update_task`` 那边是对的，所以只有新建会丢）。
"""

import pytest

from app.services.task_service import validate_period_rule


class TestValidatePeriodRule:
    def test_normalizes(self) -> None:
        assert validate_period_rule("month", -1) == ("month", -1)
        assert validate_period_rule("WEEK", -1) == ("week", -1)
        assert validate_period_rule("", 0) == ("month", 0)

    def test_rejects_bad_granularity(self) -> None:
        from app.services.task_service import TaskError

        with pytest.raises(TaskError):
            validate_period_rule("day", 0)

    def test_rejects_positive_offset(self) -> None:
        from app.services.task_service import TaskError

        with pytest.raises(TaskError):
            validate_period_rule("month", 1)

    def test_rejects_offset_too_far(self) -> None:
        from app.services.task_service import TaskError

        with pytest.raises(TaskError):
            validate_period_rule("month", -99)


class TestCreateTaskPersistsPeriodRule:
    """新建任务必须把周期规则**落库**（回归：曾被丢弃）。"""

    def test_create_task_sets_period_fields(self) -> None:
        import inspect

        from app.services import task_service

        src = inspect.getsource(task_service.create_task)
        assert "period_granularity=granularity" in src, (
            "create_task 又把周期规则丢掉了 —— 新建任务会存成默认的「本期」"
        )
        assert "period_offset=offset" in src, (
            "create_task 又把周期偏移丢掉了 —— 新建任务会存成默认的「本期」"
        )
