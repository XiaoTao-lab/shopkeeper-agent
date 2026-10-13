"""
指标元数据业务实体

用于表达一个业务指标的名称 说明 别名以及它依赖的底层字段
后续写入 Meta MySQL 和 Qdrant 时都会复用这份统一的业务对象

除基础元数据外，本实体还承载「语义层口径」：把指标的标准计算式 主表 时间字段
和默认过滤条件冻结下来，生成 SQL 时直接按冻结口径执行，避免模型自由解释口径
"""

from dataclasses import dataclass, field


@dataclass
class MetricInfo:
    """系统内部统一使用的指标元数据表达"""

    id: str
    name: str
    description: str
    # 指标依赖的底层字段列表，例如 GMV 依赖 fact_order.order_amount
    relevant_columns: list[str]
    alias: list[str]

    # ---- 以下为语义层口径字段（带默认值，兼容历史数据与旧向量集合）----
    # 标准计算口径，形如 SUM(fact_order.order_amount)，生成 SQL 时按此表达式执行
    formula: str = ""
    # 指标主表，形如 fact_order
    base_table: str = ""
    # 默认时间字段，做时间筛选时优先使用，形如 fact_order.date_id
    time_column: str = ""
    # 默认过滤条件，例如排除已取消订单；为空表示无附加过滤
    default_filters: list[str] = field(default_factory=list)
    # 指标单位，形如 元 件 人 笔
    unit: str = ""
