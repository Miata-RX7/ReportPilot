"""指标 / 报告任务的归属权限（2026-11-11）。

背景：原先指标和报告任务**完全不分用户** —— 所有人都能看到全部，任何登录用户
都能改/删别人的任务。现在改为「每个人只看到自己创建的」。

这些用例锁住三件事：
1. 可见性：admin 全见；普通用户只见自己的
2. 写权限：owner 或 admin
3. **无权查看返回 404 而不是 403**（403 会泄露「这个 id 存在」）
"""

import pytest

from app.services import ownership


class _User:
    def __init__(self, uid: int, role: str = "user"):
        self.id = uid
        self.role = role


class _Metric:
    def __init__(self, owner_id: int | None):
        self.id = 1
        self.owner_id = owner_id


class _Task:
    def __init__(self, created_by: int):
        self.id = 1
        self.created_by = created_by


class TestMetricVisibility:
    def test_admin_sees_all(self) -> None:
        admin = _User(1, "admin")
        assert ownership.metric_visible_to(_Metric(999), admin)
        assert ownership.metric_editable_by(_Metric(999), admin)

    def test_owner_sees_and_edits_own(self) -> None:
        me = _User(7)
        assert ownership.metric_visible_to(_Metric(7), me)
        assert ownership.metric_editable_by(_Metric(7), me)

    def test_others_invisible_and_not_editable(self) -> None:
        me = _User(7)
        assert not ownership.metric_visible_to(_Metric(999), me)
        assert not ownership.metric_editable_by(_Metric(999), me)

    def test_public_metric_visible_but_not_editable(self) -> None:
        """owner_id IS NULL = 公共指标：所有人可见只读。"""
        me = _User(7)
        assert ownership.metric_visible_to(_Metric(None), me)
        assert not ownership.metric_editable_by(_Metric(None), me)

    def test_invisible_raises_404_not_403(self) -> None:
        """403 会泄露「这个 id 存在」，所以用 404。"""
        from fastapi import HTTPException

        me = _User(7)
        with pytest.raises(HTTPException) as exc:
            ownership.ensure_metric_visible(_Metric(999), me)
        assert exc.value.status_code == 404

    def test_not_editable_raises_403(self) -> None:
        from fastapi import HTTPException

        me = _User(7)
        with pytest.raises(HTTPException) as exc:
            ownership.ensure_metric_editable(_Metric(999), me)
        assert exc.value.status_code == 403


class TestTaskVisibility:
    def test_admin_sees_all(self) -> None:
        admin = _User(1, "admin")
        assert ownership.task_visible_to(_Task(999), admin)
        assert ownership.task_editable_by(_Task(999), admin)

    def test_owner_sees_and_edits_own(self) -> None:
        me = _User(7)
        assert ownership.task_visible_to(_Task(7), me)
        assert ownership.task_editable_by(_Task(7), me)

    def test_others_invisible(self) -> None:
        me = _User(7)
        assert not ownership.task_visible_to(_Task(999), me)
        assert not ownership.task_editable_by(_Task(999), me)

    def test_invisible_raises_404(self) -> None:
        from fastapi import HTTPException

        me = _User(7)
        with pytest.raises(HTTPException) as exc:
            ownership.ensure_task_visible(_Task(999), me)
        assert exc.value.status_code == 404


class TestOwnerNamesAdminOnly:
    def test_non_admin_gets_empty_map(self) -> None:
        """「创建人」只有 admin 能拿到 —— 后端就掐掉，不靠前端隐藏。"""
        me = _User(7)
        assert ownership.owner_names_for_admin({1}, {1: "ops"}, me) == {}

    def test_admin_gets_names(self) -> None:
        admin = _User(1, "admin")
        assert ownership.owner_names_for_admin({1}, {1: "ops"}, admin) == {1: "ops"}
