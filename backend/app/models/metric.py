"""指标定义：取数配置 + 聚合计算逻辑，产出"计算变量"。"""

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


class MetricDefinition(TimestampMixin, Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (
        # 软删除友好的部分唯一索引：仅活跃记录之间 name 唯一
        Index(
            "uq_metric_name_active",
            "name",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # time 类型不取数 -> 允许为空；其余类型由 API 层校验必填
    data_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id"), nullable=True
    )
    # 取数配置，由数据源 Adapter 解释（三级全必填：主机组 / 主机 / 监控项）。
    # {"host_group_ids": ["4"], "host_ids": ["12793"],
    #  "item_ids": ["261006"],                     # 监控项直接指定，不再支持 key 模糊匹配
    #  "value_type_override": null}                # 可选，覆盖默认数值型过滤 [0,3]
    query_config: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    # 标签：仅用于列表展示与搜索，不参与取数。PostgreSQL 上建 GIN 索引，
    # 查询用 tags @> '["核心"]'::jsonb 精确匹配。
    tags: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    # 指标类型：scalar(数值) / curve(曲线) / pie(占比) / text(文本) / time(时间)。
    # 决定 query_config 形态与 calc_engine 的计算分支；库级 CHECK 约束保证合法值。
    metric_type: Mapped[str] = mapped_column(
        String(16), default="scalar", nullable=False
    )
    # 分类型配置（按 metric_type 解释），与仅服务 scalar 的 calc_formula 分开：
    #   scalar {"transform": {"add": 0, "mul": 1}}
    #   curve  {"transform_default": {...}, "max_series": 12}
    #   pie    {"pie_config": {"up_values": [...], "down_values": [...], "unknown_mode": "exclude"}}
    #   text   {"text_config": {"mode": "latest"|"fixed", "fixed_value": null}}
    #   time   {"time_config": {"format_modules": ["年","-","月"], "zero_pad": true,
    #                        "offset_value": -1, "offset_unit": "month"}}
    type_config: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    # 聚合计算逻辑（结构化描述，由 calc_engine 执行），**仅 scalar 使用**：
    # {"agg": "avg", "compare": "mom", "baseline_lookback": 1}
    calc_formula: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    unit: Mapped[str] = mapped_column(String(32), default="")
    # 单位换算：纯比例的「展示派生层」，事实层数值永不改写（仅影响展示值与展示单位）。
    # NULL 或 {"mode": "none"} 与升级前行为完全一致；仅 scalar / curve 生效。
    #   {"mode": "auto", "base": 1000, "suffix_unit": "bps", "decimals": null}
    #       -> 262468.9041666667 展示为 262.47 Kbps（K/M/G/T 按本期值量级自选档）
    #   {"mode": "fixed", "divisor": 1000, "display_unit": "Kbps", "decimals": null}
    # 语义实现见 services/unit_scale.py，前端镜像 frontend/src/utils/unit.ts。
    unit_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONVariant, nullable=True
    )
    # monthly / weekly / daily
    period_type: Mapped[str] = mapped_column(String(16), default="monthly")
    # 渲染小数位数：None -> %g；N -> f"{value:.Nf}"
    display_precision: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
