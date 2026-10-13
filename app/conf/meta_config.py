"""
元数据知识构建配置

定义 meta_config.yaml 在程序中的结构，这份配置不描述底层基础设施
而是描述一次元数据知识构建要处理哪些表 哪些字段 和哪些指标，脚本入口会先读取 YAML
再把内容转换成这里定义的 dataclass 对象，后续服务层就可以按统一结构完成元数据同步
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ColumnConfig:
    """
    单个字段的同步配置
    描述字段的业务角色 说明 别名以及是否同步字段值
    """

    name: str
    role: str
    description: str
    alias: list[str]
    sync: bool


@dataclass
class TableConfig:
    """
    单张表及其字段列表的同步配置
    一张表下面会继续声明需要纳入知识库的字段列表
    """

    name: str
    role: str
    description: str
    columns: list[ColumnConfig]


@dataclass
class MetricConfig:
    """
    单个指标的同步配置
    用来描述指标和底层字段之间的关联关系，并承载语义层口径
    formula/base_table/time_column/default_filters/unit 提供默认值，兼容旧配置
    """

    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]
    # 标准计算口径，如 SUM(fact_order.order_amount)
    formula: str = ""
    # 指标主表，如 fact_order
    base_table: str = ""
    # 默认时间字段，如 fact_order.date_id
    time_column: str = ""
    # 默认过滤条件；为 None 表示无附加过滤
    default_filters: Optional[list[str]] = None
    # 指标单位，如 元 件 人 笔
    unit: str = ""


@dataclass
class MetaConfig:
    """
    元数据知识构建总配置
    tables 和 metrics 都允许为空 便于分阶段只构建其中一部分
    """

    tables: Optional[list[TableConfig]] = None
    metrics: Optional[list[MetricConfig]] = None
