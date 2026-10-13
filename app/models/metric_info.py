"""
`metric_info` ORM 模型

定义元数据库中 metric_info 表对应的 ORM 模型
负责保存指标名称 指标说明 指标别名，以及指标和底层字段之间的关联关系
同时保存语义层口径（标准计算式 主表 时间字段 默认过滤 单位），供 SQL 生成阶段冻结使用
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class MetricInfoMySQL(Base):
    """指标元数据表对应的 ORM 模型"""

    __tablename__ = "metric_info"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="指标编码")
    name: Mapped[str | None] = mapped_column(String(128), comment="指标名称")
    description: Mapped[str | None] = mapped_column(Text, comment="指标描述")
    # 指标通常会关联一个或多个底层字段
    # 这里直接以 JSON 数组保存字段标识列表
    relevant_columns: Mapped[dict | list | None] = mapped_column(
        JSON, comment="关联字段"
    )
    alias: Mapped[dict | list | None] = mapped_column(JSON, comment="指标别名")

    # 语义层口径字段
    formula: Mapped[str | None] = mapped_column(
        String(512), comment="标准计算口径，如 SUM(fact_order.order_amount)"
    )
    base_table: Mapped[str | None] = mapped_column(String(64), comment="指标主表")
    time_column: Mapped[str | None] = mapped_column(String(64), comment="默认时间字段")
    default_filters: Mapped[dict | list | None] = mapped_column(
        JSON, comment="默认过滤条件"
    )
    unit: Mapped[str | None] = mapped_column(String(16), comment="指标单位")
