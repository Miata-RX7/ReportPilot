"""效率价值的响应模型（2026-11-20）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EfficiencySummary(BaseModel):
    """汇总数字（单位：分钟）。"""

    #: 任务数
    task_count: int = 0
    #: 执行次数（只算成功的终态）
    run_count: int = 0
    #: 人工侧总耗时（执行次数 × 人工每次）
    total_manual_minutes: int = 0
    #: 平台侧总耗时（一次性配置 + 执行次数 × 每次人工参与）
    total_platform_minutes: int = 0
    #: 累计净节省
    total_saved_minutes: int = 0

    @property
    def total_saved_hours(self) -> float:
        return round(self.total_saved_minutes / 60, 1)


class TaskEfficiencyRead(BaseModel):
    """单个任务的效率账。"""

    task_id: int
    task_name: str
    template_id: int | None = None
    template_name: str | None = None
    owner_id: int | None = None
    owner_name: str | None = None

    #: 数值类型指标数（scalar / text / time / calculated）
    scalar_count: int = 0
    #: 曲线图类型指标数（curve / pie）
    curve_count: int = 0
    #: AI 摘要占位符数
    insight_count: int = 0
    #: 指标总数（不含摘要）
    total_metrics: int = 0
    #: 该任务是否确实上传过 Excel 并生成了 AI 摘要（确认阶段计入的依据）
    has_excel_summary: bool = False

    #: 执行次数（只算成功终态）
    run_count: int = 0
    #: 人工做一份要多久（分钟/次）
    manual_per_run: int = 0
    #: 平台一次性配置投入（分钟）
    platform_setup: int = 0
    #: 平台每次执行仍需人工参与（分钟/次，仅确认阶段）
    platform_per_run: int = 0
    #: 每次执行净节省（分钟）
    saved_per_run: int = 0
    #: 累计净节省（分钟）
    total_saved: int = 0
    #: 盈亏平衡：执行几次才回本；不划算时为 None
    breakeven_runs: int | None = None
    #: 是否是当前用户自己创建的任务
    is_mine: bool = False


class EfficiencyValue(BaseModel):
    """首页「效率价值」区块的完整数据。"""

    #: 全局汇总（所有任务）
    summary: EfficiencySummary = Field(default_factory=EfficiencySummary)
    #: 全部任务明细（每任务一行）
    tasks: list[TaskEfficiencyRead] = Field(default_factory=list)
    #: 当前用户名下任务的明细（「我的任务」卡片）
    mine: list[TaskEfficiencyRead] = Field(default_factory=list)
    #: 当前用户名下任务的汇总
    mine_summary: EfficiencySummary = Field(default_factory=EfficiencySummary)
