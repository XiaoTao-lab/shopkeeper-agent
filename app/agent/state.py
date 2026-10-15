"""
电商问数 Agent 状态定义

State 是 LangGraph 各节点之间传递和更新的共享数据
本章在用户原始问题之外，新增关键词列表和三路召回结果
并把召回到的实体整理成后续提示词更容易消费的表信息和指标信息
SQL 生成闭环会继续写入候选 SQL 以及校验错误信息，用于控制校正或执行分支
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.value_info import ValueInfo


class MetricInfoState(TypedDict):
    """面向 SQL 生成提示词的指标信息"""

    name: str
    description: str
    # 指标依赖的字段 id，用来提示模型不要脱离业务口径随意计算
    relevant_columns: list[str]
    alias: list[str]
    # 语义层口径：标准计算式 主表 时间字段 默认过滤 单位
    formula: str
    base_table: str
    time_column: str
    default_filters: list[str]
    unit: str


class ColumnInfoState(TypedDict):
    """表上下文中的字段信息"""

    name: str
    type: str
    role: str
    # 字段真实样例值，尤其用于辅助 where 条件里的枚举值选择
    examples: list
    description: str
    alias: list[str]


class TableInfoState(TypedDict):
    """SQL 生成阶段真正传给模型的表结构上下文"""

    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class DateInfoState(TypedDict):
    """SQL 生成阶段使用的当前日期上下文"""

    date: str
    weekday: str
    quarter: str


class DBInfoState(TypedDict):
    """SQL 生成阶段使用的数据库环境信息"""

    dialect: str
    version: str


class TimeRangeState(TypedDict):
    """解析出的时间区间（半开区间 [start, end)）"""

    start: str  # 起始日（含），YYYY-MM-DD
    end: str  # 结束日（不含），YYYY-MM-DD
    granularity: str  # day/week/month/quarter/half_year/year
    expr: str  # 命中的原始时间表达
    source: str  # rule / llm


class DataAgentState(TypedDict):
    """一次问数链路中的核心状态"""

    query: str  # 用户输入的查询（多轮场景下已被改写为独立问题）
    # 多轮对话：改写后的独立问题，首轮等于 query，便于排查"模型到底把问题改成了什么"
    rewritten_query: str
    # 本轮开始时间（epoch 毫秒），用于给用户展示"这次问数花了多久"
    started_at: int
    # 多轮对话历史：由 checkpointer 按 thread_id 持久化，add_messages 负责追加而非覆盖
    # 每轮存入"改写后的独立问题"，让后续轮次始终在自洽语境上继续改写
    messages: Annotated[list[AnyMessage], add_messages]
    keywords: list[str]  # 抽取的关键词
    retrieved_column_infos: list[ColumnInfo]  # 检索到的字段信息
    retrieved_metric_infos: list[MetricInfo]  # 检索到的指标信息
    retrieved_value_infos: list[ValueInfo]  # 检索到的取值信息

    table_infos: list[TableInfoState]  # 合并和补齐后的表结构上下文
    metric_infos: list[MetricInfoState]  # 合并后的指标上下文
    metric_caliber_text: str  # 冻结后的指标口径约束文本
    glossary_text: str  # 注入的业务字典文本（仅解释术语，不覆盖口径/权限）
    clarify_question: str  # 澄清反问文案；非空表示本轮因歧义短路（不进入 SQL 生成）
    clarify_options: list[str]  # 澄清时可选的候选指标
    time_range: TimeRangeState | None  # 解析出的时间区间
    time_range_text: str  # 冻结后的时间范围约束文本
    date_info: DateInfoState  # 当前日期 星期和季度信息
    db_info: DBInfoState  # 数据库方言和版本信息

    sql: str  # 生成或校正后的SQL

    error: str  # 校验SQL时出现的错误信息
    # 被安全防线拦截时的来源（intent / sql_guard）；非空表示本轮是"预期拦截"而非真故障。
    # 归因、审计、报表三处共用这个标记，把"安全拦下的"和"真崩掉的"分开统计。
    blocked_by: str

    # —— 权限与审计（B2）——
    user_role: str  # 当前请求的角色名（admin / east_manager / analyst / guest），来自请求头 X-User-Role
    thread_id: str  # 会话 id，供审计留痕与多轮定位
    rls_applied: bool  # 本轮是否真正注入了行级过滤（全量角色为 False）
    rls_region_ids: list[str]  # 实际注入的区域 id 白名单，供审计与提示使用
    masked_columns: list[str]  # 本轮实际脱敏的结果列名
    row_count: int  # 结果行数，供审计留痕
