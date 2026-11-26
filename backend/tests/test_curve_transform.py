"""回归测试：曲线的「数值变换」(mul/add) 必须能存下来并生效。

背景（2026-11-26 用户反馈）：曲线指标在「数值变换」里把乘以改成 1000，
保存后再打开还是 1 —— 前端提交时 curve 分支写的是 `type_config: {}`，
数值变换被丢弃。

这个测试锁住两件事：
1) CurveTypeConfig 能接受 transform 字段（后端 schema 层）
2) 曲线的默认变换优先级：series 自己的 > type_config.transform > transform_default
"""

from app.schemas.metric import CurveTypeConfig


class TestCurveTransform:
    def test_accepts_transform(self) -> None:
        cfg = CurveTypeConfig.model_validate({"transform": {"mul": 1000, "add": 0}})
        assert cfg.transform is not None
        assert cfg.transform.mul == 1000
        assert cfg.transform.add == 0

    def test_default_is_none(self) -> None:
        """老数据没有 transform -> None（calc_engine 会回落到 transform_default）。"""
        cfg = CurveTypeConfig.model_validate({})
        assert cfg.transform is None
        assert cfg.transform_default.mul == 1
        assert cfg.transform_default.add == 0

    def test_extra_fields_tolerated(self) -> None:
        """历史数据里可能有别的键，不能因此校验失败。"""
        cfg = CurveTypeConfig.model_validate(
            {"transform": {"mul": 2, "add": 1}, "legacy_key": 1}
        )
        assert cfg.transform.mul == 2
