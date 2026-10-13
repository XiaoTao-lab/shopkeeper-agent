"""
功能回归用例脚本

一次性跑完「语义层（指标口径）+ 时间维度解析 + 意图层 + SQL 沙箱 + 结果兜底 + 澄清反问 + 结论生成 + 权限（RLS/脱敏）+ 图表规划 + 可观测（链路追踪）+ 召回（并发检索/去重）」的成套用例，
逐条打印 期望/实际 与通过情况，末尾给出汇总。
默认只跑离线用例（口径编译 + 时间解析 + 规则层 + 沙箱 + 空结果提示 + 澄清 + 结论 + 权限 + 图表 + 追踪 + 召回）；加 --llm 时追加需要模型的意图用例。

用法：
    # 离线全量（语义层 + 时间解析 + 规则层 + 沙箱 + 空结果提示）
    uv run python -m app.scripts.run_cases

    # 追加 LLM 意图用例（需 .env 里 API Key 有效）
    uv run python -m app.scripts.run_cases --llm
"""

import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from app.agent.llm import get_llm, llm, llm_fast
from app.agent.nodes.resolve_metric import compile_metric_caliber
from app.conf.app_config import app_config
from app.core.chart_planner import plan_chart
from app.core.clarify_guard import detect_ambiguity
from app.core.concurrency import gather_limited
from app.core.intent_guard import check_intent
from app.core.mask import mask_rows
from app.core.recall import dedup_items, embed_and_search, search_by_keywords
from app.core.result_guard import build_empty_notice
from app.core.result_summary import build_result_summary
from app.core.rls_guard import inject_row_filter
from app.core.sql_guard import guard_sql
from app.core.time_parser import parse_time_range_by_rule
from app.core.tracing import (
    display_width,
    extract_usage,
    format_ms,
    new_trace,
    pad_display,
    percentile,
    resolve_run_status,
    should_sample,
)


@dataclass
class Case:
    """一条测试用例"""

    cid: str
    group: str
    payload: str
    expected: bool  # 意图层：是否写意图；沙箱：是否放行
    note: str = ""


@dataclass
class MetricCase:
    """一条语义层（指标口径）测试用例"""

    cid: str
    metric_infos: list[dict]
    must_contain: list[str]
    must_not_contain: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class TimeCase:
    """一条时间维度解析测试用例"""

    cid: str
    query: str
    today: str
    start: str | None  # None 表示无时间表达
    end: str | None = None
    note: str = ""


@dataclass
class EmptyCase:
    """一条空结果兜底测试用例"""

    cid: str
    result: list[dict]
    time_range: dict | None
    expect_notice: bool  # True=应给出空结果提示；False=有数据不应提示
    must_contain: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class ClarifyCase:
    """一条澄清反问测试用例"""

    cid: str
    query: str
    metrics: list[dict]
    expect_clarify: bool  # True=应判定为歧义并反问；False=不应打扰
    must_contain: list[str] = field(default_factory=list)  # 反问文案/候选里应包含的片段
    note: str = ""


@dataclass
class SummaryCase:
    """一条结论生成（一句话总结）测试用例"""

    cid: str
    result: list[dict]
    expect_none: bool  # True=无有效数据应返回 None；False=应给出结论文本
    must_contain: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class RlsCase:
    """一条行级权限注入测试用例

    expect_applied: 是否应生效（全量角色 / 不涉及区域表 → False）
    expect_modified: SQL 是否被改写（幂等二次注入 → False）
    must_contain: 处理后 SQL 必须包含的片段
    outer_where_excludes: 该片段**不得**出现在最外层 WHERE——
        用于校验子查询场景下条件确实下沉到了「表所在层」，而非误注最外层
    """

    cid: str
    sql: str
    region_ids: list[str]
    expect_applied: bool
    expect_modified: bool
    must_contain: list[str] = field(default_factory=list)
    note: str = ""
    outer_where_excludes: str = ""


@dataclass
class MaskCase:
    """一条列级脱敏测试用例

    expects: [(列名, 期望脱敏后的值)]，断言首行对应列的取值
    """

    cid: str
    rows: list[dict]
    mask_columns: list[str]
    expects: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""


@dataclass
class ChartCase:
    """一条图表规划测试用例

    expect_type: 期望图型（"bar" / "line"）；None 表示"判定不该出图"
    expect_order: 校验 x 轴顺序时给出（默认不校验）
    expect_measures: 校验实际入图的度量列时给出（默认不校验）
    metric_infos: 指标元信息，用来构造"单位一致 / 不一致"的场景
    """

    cid: str
    result: list[dict]
    expect_type: str | None
    expect_order: list[str] | None = None
    expect_measures: list[str] | None = None
    metric_infos: list[dict] | None = None
    note: str = ""


@dataclass
class TraceCase:
    """一条可观测（链路追踪）测试用例

    ``kind`` 决定跑哪个纯函数，全部离线可跑：

    - ``status``    ：``resolve_run_status(payload)``
    - ``format``    ：``format_ms(payload)``
    - ``sample``    ：``should_sample(payload)``
    - ``usage``     ：``extract_usage(SimpleNamespace(**payload))``
    - ``percentile``：``percentile(payload[0], payload[1])``（保留 4 位小数比较）
    - ``span``      ：把 payload 当节点耗时列表记账，取 ``span_ms``
    - ``width``     ：``display_width(payload)``
    - ``pad``       ：``pad_display(payload[0], payload[1])``
    """

    cid: str
    kind: str
    payload: object
    expect: object
    note: str = ""


def _metric_state(raw: dict) -> dict:
    """把精简的指标描述补齐成口径编译所需的状态结构"""
    return {
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
        "relevant_columns": [],
        "alias": [],
        "formula": raw.get("formula", ""),
        "base_table": raw.get("base_table", ""),
        "time_column": raw.get("time_column", ""),
        "default_filters": raw.get("default_filters", []),
        "unit": raw.get("unit", ""),
    }


# 意图层用例：expected = 是否应判定为「写意图」（True 表示应拦截）
INTENT_CASES: list[Case] = [
    # —— 规则命中：高置信写动词，离线即可拦 ——
    Case("I-R01", "意图·规则", "把华东地区的GMV改为1", True, "命中「改为」"),
    Case("I-R02", "意图·规则", "删除所有测试订单", True, "命中「删除」"),
    Case("I-R03", "意图·规则", "清空 fact_order 表", True, "命中「清空」"),
    Case("I-R04", "意图·规则", "把商品A的价格修改为9.9", True, "命中「修改」"),
    Case("I-R05", "意图·规则", "将华北地区销售额设置为0", True, "命中「设置为」"),
    Case("I-R06", "意图·规则", "移除所有失效客户", True, "命中「移除」"),
    Case("I-R07", "意图·规则", "把联系方式替换成空字符串", True, "命中「替换」"),
    Case("I-R08", "意图·规则", "drop table dim_customer", True, "命中英文 drop"),
    Case("I-R09", "意图·规则", "update fact_order set order_amount=0", True, "update"),
    Case(
        "I-R10", "意图·规则", "insert into dim_region values(1,'华东')", True, "insert"
    ),
    Case("I-R11", "意图·规则", "truncate table fact_order", True, "truncate"),
    Case("I-R12", "意图·规则", "delete from fact_order where id=1", True, "delete"),
    # —— 歧义词放行：规则刻意不收，离线应放行 ——
    Case("I-P01", "意图·放行", "统计华北地区的销售总额", False, "正常问数"),
    Case("I-P02", "意图·放行", "查询最近更新的订单", False, "「更新」是修饰词"),
    Case("I-P03", "意图·放行", "统计新增用户数量", False, "「新增」是统计口径"),
    Case("I-P04", "意图·放行", "对比各地区的GMV", False, "正常问数"),
    Case("I-P05", "意图·放行", "列出销量前十的商品", False, "正常问数"),
    Case("I-P06", "意图·放行", "各渠道的退款率是多少", False, "正常问数"),
]

# 需要 LLM 的意图用例：expected = 是否应判定为「写意图」
INTENT_LLM_CASES: list[Case] = [
    Case("I-L01", "意图·LLM", "帮我把滞销商品下架", True, "隐含写意图"),
    Case("I-L02", "意图·LLM", "这批测试订单作废掉", True, "隐含写意图"),
    Case("I-L03", "意图·LLM", "把过期会员标记为失效", True, "隐含写意图"),
    Case("I-L04", "意图·LLM", "统计新增用户数量", False, "回看：不应误伤查询"),
]

# 语义层用例：断言口径文本必须包含 / 不得包含的片段
SEMANTIC_CASES: list[MetricCase] = [
    MetricCase(
        "M-01",
        [
            {
                "name": "GMV",
                "formula": "SUM(fact_order.order_amount)",
                "base_table": "fact_order",
                "unit": "元",
            }
        ],
        ["SUM(fact_order.order_amount)", "GMV", "主表：fact_order", "单位：元"],
        [],
        "单指标口径冻结",
    ),
    MetricCase(
        "M-02",
        [
            {"name": "GMV", "formula": "SUM(fact_order.order_amount)"},
            {"name": "AOV", "formula": "AVG(fact_order.order_amount)"},
        ],
        ["SUM(fact_order.order_amount)", "AVG(fact_order.order_amount)"],
        [],
        "双指标各自成段",
    ),
    MetricCase(
        "M-03",
        [{"name": "描述型指标", "formula": ""}],
        [],
        ["【指标口径约束】"],
        "无标准计算式 → 不下发口径块",
    ),
    MetricCase(
        "M-04",
        [
            {"name": "GMV", "formula": "SUM(fact_order.order_amount)"},
            {"name": "神秘指标", "formula": ""},
        ],
        ["GMV"],
        ["神秘指标"],
        "混合时只保留有口径的指标",
    ),
    MetricCase(
        "M-05",
        [
            {
                "name": "GMV",
                "formula": "SUM(fact_order.order_amount)",
                "base_table": "fact_order",
            },
            {
                "name": "客户数",
                "formula": "COUNT(dim_customer.customer_id)",
                "base_table": "dim_customer",
            },
        ],
        ["跨越多个主表", "dim_customer", "fact_order"],
        [],
        "跨主表给出关联提示",
    ),
    MetricCase(
        "M-06",
        [
            {
                "name": "GMV",
                "formula": "SUM(fact_order.order_amount)",
                "base_table": "fact_order",
            },
            {
                "name": "AOV",
                "formula": "AVG(fact_order.order_amount)",
                "base_table": "fact_order",
            },
        ],
        ["SUM(fact_order.order_amount)"],
        ["跨越多个主表"],
        "同主表不误报跨表提示",
    ),
    MetricCase(
        "M-07",
        [
            {
                "name": "GMV",
                "formula": "SUM(fact_order.order_amount)",
                "default_filters": ["fact_order.order_id IS NOT NULL"],
            }
        ],
        ["fact_order.order_id IS NOT NULL"],
        [],
        "默认过滤条件被拼接",
    ),
    MetricCase(
        "M-08",
        [],
        [],
        ["【指标口径约束】"],
        "空指标列表 → 口径块为空",
    ),
    MetricCase(
        "M-09",
        [{"name": "GMV", "formula": "SUM(fact_order.order_amount)", "unit": ""}],
        ["未标注"],
        [],
        "单位缺省时标注「未标注」",
    ),
    MetricCase(
        "M-10",
        [
            {
                "name": "订单量",
                "formula": "COUNT(DISTINCT fact_order.order_id)",
                "base_table": "fact_order",
                "unit": "笔",
            }
        ],
        ["订单量", "COUNT(DISTINCT fact_order.order_id)", "单位：笔"],
        [],
        "中文指标名 + 去重计数口径",
    ),
]


# 时间维度解析用例：参考日期固定为 2026-09-23，断言半开区间
TIME_CASES: list[TimeCase] = [
    TimeCase("T-01", "上个月的GMV", "2026-09-23", "2026-08-01", "2026-09-01", "相对月"),
    TimeCase(
        "T-02", "本季度订单量", "2026-09-23", "2026-07-01", "2026-10-01", "相对季度"
    ),
    TimeCase(
        "T-03", "最近7天的订单", "2026-09-23", "2026-09-17", "2026-09-24", "含今天顺推"
    ),
    TimeCase("T-04", "本周GMV", "2026-09-23", "2026-09-21", "2026-09-28", "周一对齐"),
    TimeCase("T-05", "上季度", "2026-01-15", "2025-10-01", "2026-01-01", "跨年：季度"),
    TimeCase("T-06", "上个月", "2026-01-15", "2025-12-01", "2026-01-01", "跨年：月份"),
    TimeCase(
        "T-07", "2025年第三季度", "2026-09-23", "2025-07-01", "2025-10-01", "绝对季度"
    ),
    TimeCase("T-08", "各地区的订单量", "2026-09-23", None, None, "无时间表达→不约束"),
]


# 空结果兜底用例：expect_notice = 是否应给出"暂无数据"提示
EMPTY_CASES: list[EmptyCase] = [
    EmptyCase("K-01", [], None, True, ["没有查询到"], "0 行 → 通用提示"),
    EmptyCase("K-02", [{"销售额": None}], None, True, ["没有查询到"], "聚合落在空集"),
    EmptyCase("K-03", [{"GMV": 279159.5}], None, False, [], "有数据 → 不提示"),
    EmptyCase("K-04", [{"订单量": 0}], None, False, [], "0 是有效值 → 不提示"),
    EmptyCase(
        "K-05",
        [{"销售额": None}],
        {
            "start": "2026-04-01",
            "end": "2026-07-01",
            "granularity": "quarter",
            "expr": "上一季度",
            "source": "rule",
        },
        True,
        ["上一季度", "2026-04-01 至 2026-06-30"],
        "带时间区间：含原始表达 + 闭区间末日",
    ),
    EmptyCase("K-06", [{"a": None, "b": 1}], None, False, [], "部分非空 → 不提示"),
]


# 澄清用例使用的精简指标集（与 conf 里的口径一致）
_CLARIFY_METRICS: list[dict] = [
    {
        "name": "GMV",
        "description": "成交金额总和",
        "alias": ["成交总额", "订单总额", "销售额", "营收", "交易总额"],
    },
    {
        "name": "AOV",
        "description": "每笔订单平均金额",
        "alias": ["客单价", "平均订单金额", "平均单价"],
    },
    {
        "name": "订单量",
        "description": "成交订单总数量",
        "alias": ["订单数", "订单总数"],
    },
]

# 澄清反问用例：expect_clarify = 是否应判定为歧义
CLARIFY_CASES: list[ClarifyCase] = [
    ClarifyCase(
        "C-01", "统计华东地区的GMV", _CLARIFY_METRICS, False, [], "命中指标名 → 不打扰"
    ),
    ClarifyCase(
        "C-02",
        "统计华东地区的销售额",
        _CLARIFY_METRICS,
        False,
        [],
        "命中别名 → 不打扰",
    ),
    ClarifyCase(
        "C-03", "华东地区的排名", _CLARIFY_METRICS, False, [], "纯维度问题 → 不打扰"
    ),
    ClarifyCase(
        "C-04",
        "统计华东地区的金额",
        _CLARIFY_METRICS,
        True,
        ["金额", "GMV", "AOV"],
        "泛化词 + 多候选 → 追问并给出候选",
    ),
    ClarifyCase(
        "C-05",
        "统计华东地区的金额",
        [_CLARIFY_METRICS[0]],
        False,
        [],
        "单候选 → 无歧义可问",
    ),
    ClarifyCase(
        "C-06", "查一下 gmv", _CLARIFY_METRICS, False, [], "大小写不敏感：gmv 命中 GMV"
    ),
    ClarifyCase("C-07", "统计华东地区的金额", [], False, [], "无候选 → 不打扰"),
    ClarifyCase(
        "C-08",
        "看一下华东地区的业务数据",
        _CLARIFY_METRICS,
        True,
        ["数据", "GMV"],
        "泛化词「数据」→ 追问",
    ),
]


# 结论生成用例：expect_none = 是否应返回 None（无有效数据）
SUMMARY_CASES: list[SummaryCase] = [
    SummaryCase(
        "Z-01",
        [{"GMV": 107373.0}],
        False,
        ["GMV", "107,373"],
        "单行单指标：直出数值",
    ),
    SummaryCase(
        "Z-02",
        [{"地区": "华东", "GMV": 107373.0, "订单量": 115}],
        False,
        ["地区 华东", "GMV 107,373", "订单量 115"],
        "单行多指标：逐字段列出",
    ),
    SummaryCase(
        "Z-03",
        [{"销售额": None}],
        False,
        ["销售额 -"],
        "单值空：用 - 占位",
    ),
    SummaryCase(
        "Z-04",
        [
            {"地区": "华东", "GMV": 107373.0},
            {"地区": "华北", "GMV": 60000.0},
            {"地区": "华南", "GMV": 88000.0},
        ],
        False,
        ["最高", "华东", "最低", "华北", "共 3 组"],
        "多行维度+度量：点出极值与组数",
    ),
    SummaryCase(
        "Z-05",
        [{"地区": "华东", "GMV": 100}, {"地区": "华北", "GMV": 100}],
        False,
        ["均为 100"],
        "多行取值相同：给均一描述",
    ),
    SummaryCase(
        "Z-06",
        [{"地区": "华东"}, {"地区": "华北"}],
        False,
        ["共 2 行结果"],
        "无度量列：退化为行数",
    ),
    SummaryCase(
        "Z-07",
        [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
        False,
        ["最高 3", "最低 1", "共 2 行"],
        "全数值列：只报极值与行数",
    ),
    SummaryCase("Z-08", [], True, [], "空结果：返回 None"),
    SummaryCase("Z-09", [{}], True, [], "空字典行：返回 None"),
]


# 行级权限注入用例：华东经理的区域白名单
_PERM_REGIONS: list[str] = ["R002", "R005"]

RLS_CASES: list[RlsCase] = [
    RlsCase(
        "P-R01",
        "SELECT region_name, SUM(order_amount) FROM fact_order GROUP BY region_name",
        [],
        False,
        False,
        [],
        "全量角色 → 不注入",
    ),
    RlsCase(
        "P-R02",
        "SELECT region_name, SUM(order_amount) FROM fact_order GROUP BY region_name",
        _PERM_REGIONS,
        True,
        True,
        ["region_id", "R002", "R005"],
        "事实表无别名 → 注入 IN 白名单",
    ),
    RlsCase(
        "P-R03",
        (
            "SELECT r.region_name, SUM(f.order_amount) AS total "
            "FROM fact_order f JOIN dim_region r ON f.region_id = r.region_id "
            "GROUP BY r.region_name"
        ),
        _PERM_REGIONS,
        True,
        True,
        ["f.region_id"],
        "带别名 JOIN → 过滤落在别名上",
    ),
    RlsCase(
        "P-R04",
        "SELECT SUM(order_amount) FROM fact_order WHERE region_id = 'R004'",
        _PERM_REGIONS,
        True,
        True,
        ["R004", "R002", "R005"],
        "用户自带异区条件 → 仍叠加拦截",
    ),
    RlsCase(
        "P-R05",
        "SELECT region_name FROM dim_region",
        _PERM_REGIONS,
        True,
        True,
        ["region_id"],
        "仅维表 → 退到 dim_region 注入",
    ),
    RlsCase(
        "P-R06",
        "SELECT product_name FROM dim_product",
        _PERM_REGIONS,
        False,
        False,
        [],
        "不涉及区域表 → 跳过",
    ),
    RlsCase(
        "P-R07",
        (
            "SELECT SUM(order_amount) FROM fact_order "
            "WHERE region_id IN ('R002','R005')"
        ),
        _PERM_REGIONS,
        True,
        False,
        [],
        "已含相同条件 → 幂等不重复",
    ),
    RlsCase(
        "P-R08",
        (
            "SELECT dr.region_name, t.total FROM "
            "(SELECT region_id, SUM(order_amount) AS total FROM fact_order "
            "WHERE date_id >= 20250101 GROUP BY region_id) AS t "
            "JOIN dim_region AS dr ON t.region_id = dr.region_id"
        ),
        _PERM_REGIONS,
        True,
        True,
        ["region_id", "R002", "R005", "date_id"],
        "子查询派生表 → 条件下沉到子查询（不注最外层）",
        "fact_order.region_id",
    ),
    RlsCase(
        "P-R09",
        (
            "SELECT t2.total FROM (SELECT t1.total FROM "
            "(SELECT SUM(order_amount) AS total FROM fact_order) AS t1) AS t2"
        ),
        _PERM_REGIONS,
        True,
        True,
        ["region_id"],
        "两层嵌套子查询 → 条件落在事实表最近层",
        "fact_order.region_id",
    ),
]

# 列级脱敏用例：analyst / guest 对姓名列脱敏
_MASK_COLS: list[str] = ["customer_name"]

MASK_CASES: list[MaskCase] = [
    MaskCase(
        "P-M01",
        [{"customer_name": "李伟", "GMV": 100}],
        _MASK_COLS,
        [("customer_name", "李*"), ("GMV", 100)],
        "姓名脱敏，其余列不动",
    ),
    MaskCase(
        "P-M02",
        [{"phone": "13800001234"}],
        ["phone"],
        [("phone", "138****1234")],
        "手机号保留前 3 后 4",
    ),
    MaskCase(
        "P-M03",
        [{"email": "alice@example.com"}],
        ["email"],
        [("email", "a***@example.com")],
        "邮箱保留首字符与域名",
    ),
    MaskCase(
        "P-M04",
        [{"customer_name": "李伟", "GMV": 100}],
        [],
        [("customer_name", "李伟")],
        "无脱敏配置 → 原样",
    ),
    MaskCase(
        "P-M05",
        [{"customer_name": "李伟"}, {"customer_name": "张敏"}],
        _MASK_COLS,
        [("customer_name", "李*")],
        "多行均脱敏（断言首行）",
    ),
    MaskCase(
        "P-M06",
        [{"customer_name": None}],
        _MASK_COLS,
        [("customer_name", None)],
        "None 值跳过",
    ),
    MaskCase(
        "P-M07",
        [{"客户名称": "李伟", "region_name": "华东"}],
        _MASK_COLS,
        [("客户名称", "李*"), ("region_name", "华东")],
        "配置物理名客户名 → 命中中文别名，且不误伤 region_name",
    ),
]

# 图表规划用例：expect_type = "bar" / "line"，None 表示"判定不该出图"
CHART_CASES: list[ChartCase] = [
    ChartCase(
        "V-01",
        [{"地区": "华东", "GMV": 107373.0}, {"地区": "华南", "GMV": 88211.0}],
        "bar",
        expect_order=["华东", "华南"],
        expect_measures=["GMV"],
        note="1 维度 1 度量 → 柱状看对比",
    ),
    ChartCase(
        "V-02",
        [
            {"月份": "10月", "GMV": 3.0},
            {"月份": "2月", "GMV": 1.0},
            {"月份": "1月", "GMV": 2.0},
        ],
        "line",
        expect_order=["1月", "2月", "10月"],
        note="时间维度 → 折线；乱序输入按自然序纠正",
    ),
    ChartCase(
        "V-03",
        [{"地区": "华东", "GMV": 1.0}],
        None,
        note="只有一个数 → 不出图",
    ),
    ChartCase("V-04", [{"GMV": 1.0}, {"GMV": 2.0}], None, note="无维度列 → 不出图"),
    ChartCase("V-05", [{"地区": "华东"}, {"地区": "华南"}], None, note="无度量列 → 不出图"),
    ChartCase(
        "V-06",
        [{"地区": "华东", "GMV": 1.0}, {"地区": "华东", "GMV": 2.0}],
        None,
        note="维度只剩一个取值 → 退化成单值，不出图",
    ),
    ChartCase(
        "V-07",
        [{"品类": "A", "销量": 1, "销售额": 2}, {"品类": "B", "销量": 3, "销售额": 4}],
        "bar",
        expect_order=["A", "B"],
        expect_measures=["销量"],
        note="多度量但单位未知 → 退回单度量，避免同轴不可比",
    ),
    ChartCase(
        "V-08",
        [{"月份": "1月", "销量": 1, "销售额": 2}, {"月份": "2月", "销量": 3, "销售额": 4}],
        "bar",
        expect_order=["1月", "2月"],
        note="时间 + 多度量 → 降级柱状（折线只画单度量）",
    ),
    ChartCase(
        "V-09",
        [{"品类": f"P{i}", "GMV": float(i)} for i in range(31)],
        None,
        note="31 行超柱状上限 → 不出图",
    ),
    ChartCase(
        "V-10",
        [{"月份": f"{i}月", "GMV": float(i)} for i in range(1, 41)],
        "line",
        expect_order=[f"{i}月" for i in range(1, 41)],
        note="40 个时间点 → 折线（上限放宽到 60）",
    ),
    ChartCase(
        "V-11",
        [{"月份": 1, "GMV": 100.0}, {"月份": 2, "GMV": 200.0}],
        "line",
        expect_order=["1", "2"],
        expect_measures=["GMV"],
        note="整数月份（MONTH(...) AS 月份）→ 仍认成时间轴，画折线",
    ),
    ChartCase(
        "V-12",
        [{"会员等级": 1, "GMV": 1.0}, {"会员等级": 2, "GMV": 2.0}],
        None,
        note="整数非时间列 → 分不清轴与度量，保守不出图",
    ),
    ChartCase(
        "V-13",
        [{"品类": "A", "GMV": 1.0, "退款额": 2.0}, {"品类": "B", "GMV": 3.0, "退款额": 4.0}],
        "bar",
        expect_measures=["GMV", "退款额"],
        metric_infos=[{"name": "GMV", "unit": "元"}, {"name": "退款额", "unit": "元"}],
        note="同单位多度量 → 保留多系列柱状",
    ),
    ChartCase(
        "V-14",
        [{"品类": "A", "销量": 10, "销售额": 62190.0}, {"品类": "B", "销量": 139, "销售额": 1115.0}],
        "bar",
        expect_measures=["销量"],
        metric_infos=[{"name": "销量", "unit": "件"}, {"name": "销售额", "unit": "元"}],
        note="单位不同（件/元）→ 退回单度量，避免小数量级被压成一线",
    ),
]

# 可观测（链路追踪）用例：覆盖状态归因 / 耗时格式化 / 采样 / token 提取 / 百分位 / span 汇总
TRACE_CASES: list[TraceCase] = [
    TraceCase("T-01", "status", {}, "success", "无错误无澄清 → 成功"),
    TraceCase(
        "T-02", "status", {"error": "boom"}, "error", "有 error 且无拦截标记 → 真故障"
    ),
    TraceCase(
        "T-03", "status", {"clarify_question": "要看哪个指标？"}, "clarify", "澄清反问"
    ),
    TraceCase(
        "T-04",
        "status",
        {"clarify_question": "?", "error": "x"},
        "clarify",
        "澄清优先于失败：澄清是正常业务短路",
    ),
    TraceCase(
        "T-20",
        "status",
        {"error": "命中写意图", "blocked_by": "intent"},
        "blocked",
        "被意图防线拦截 → blocked，不污染故障率",
    ),
    TraceCase(
        "T-21",
        "status",
        {"error": "只读校验未通过", "blocked_by": "sql_guard"},
        "blocked",
        "被 SQL 沙箱拦截 → blocked",
    ),
    TraceCase(
        "T-22",
        "status",
        {"clarify_question": "选哪个指标？", "blocked_by": "intent"},
        "clarify",
        "澄清优先级高于拦截",
    ),
    TraceCase("T-05", "format", 820, "820 ms", "小于 1 秒用 ms"),
    TraceCase("T-06", "format", 6240, "6.24 s", "超过 1 秒换算成秒"),
    TraceCase("T-07", "format", None, "-", "None 用占位符"),
    TraceCase(
        "T-08", "percentile", ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50), 5.5, "P50 线性插值"
    ),
    TraceCase("T-09", "percentile", ([], 95), 0.0, "空样本返回 0"),
    TraceCase(
        "T-10",
        "usage",
        {"llm_output": {"token_usage": {"prompt_tokens": 120, "completion_tokens": 30}}},
        (120, 30),
        "优先取 llm_output.token_usage",
    ),
    TraceCase(
        "T-11",
        "usage",
        {
            "llm_output": None,
            "generations": [
                [
                    SimpleNamespace(
                        message=SimpleNamespace(
                            usage_metadata={"input_tokens": 55, "output_tokens": 22}
                        )
                    )
                ]
            ],
        },
        (55, 22),
        "退回消息上的 usage_metadata",
    ),
    TraceCase(
        "T-12",
        "usage",
        {"llm_output": {}},
        (0, 0),
        "取不到用量返回 0，不影响主链路",
    ),
    TraceCase("T-13", "sample", 1.0, True, "比例 >=1 恒采集"),
    TraceCase("T-14", "sample", 0.0, False, "比例 <=0 恒不采集"),
    TraceCase("T-15", "span", [10, 20, 30], 60, "span_ms 为各节点耗时之和"),
    TraceCase("T-16", "width", "华东", 4, "中文按 2 个显示列计算"),
    TraceCase("T-17", "width", "华东ab", 6, "中英混排宽度"),
    TraceCase("T-18", "pad", ("华东", 6), "华东  ", "按显示宽度补空格对齐"),
    TraceCase("T-19", "pad", ("7", 4), "7   ", "数字左对齐"),
]


# 沙箱用例：expected = 是否放行
SANDBOX_CASES: list[Case] = [
    # —— 放行 ——
    Case("S-P01", "沙箱·放行", "SELECT * FROM fact_order", True, "注入 LIMIT 1000"),
    Case(
        "S-P02",
        "沙箱·放行",
        "SELECT region_name, SUM(order_amount) FROM fact_order GROUP BY region_name",
        True,
        "聚合报表，注入 LIMIT",
    ),
    Case(
        "S-P03",
        "沙箱·放行",
        "WITH t AS (SELECT * FROM fact_order) SELECT * FROM t",
        True,
        "CTE 放行",
    ),
    Case(
        "S-P04",
        "沙箱·放行",
        "SELECT * FROM fact_order LIMIT 10",
        True,
        "保留原 LIMIT，不重复注入",
    ),
    Case(
        "S-P05",
        "沙箱·放行",
        "SELECT product_name FROM dim_product ORDER BY price DESC",
        True,
        "注入 LIMIT",
    ),
    # —— 拦截 ——
    Case("S-B01", "沙箱·拦截", "DELETE FROM fact_order", False, "写操作 Delete"),
    Case("S-B02", "沙箱·拦截", "UPDATE fact_order SET order_amount=0", False, "Update"),
    Case("S-B03", "沙箱·拦截", "DROP TABLE dim_customer", False, "Drop"),
    Case("S-B04", "沙箱·拦截", "TRUNCATE TABLE fact_order", False, "Truncate"),
    Case(
        "S-B05",
        "沙箱·拦截",
        "INSERT INTO dim_region VALUES(1,'华东')",
        False,
        "Insert",
    ),
    Case(
        "S-B06",
        "沙箱·拦截",
        "ALTER TABLE fact_order ADD COLUMN x INT",
        False,
        "Alter",
    ),
    Case(
        "S-B07",
        "沙箱·拦截",
        "SELECT * FROM fact_order; DROP TABLE dim_customer",
        False,
        "多语句注入",
    ),
    Case(
        "S-B08",
        "沙箱·拦截",
        "SELECT LOAD_FILE('/etc/passwd')",
        False,
        "危险函数",
    ),
    Case("S-B09", "沙箱·拦截", "SELECT SLEEP(10)", False, "危险函数"),
    Case("S-B10", "沙箱·拦截", "", False, "空 SQL"),
]


async def _run_intent(cases: list[Case], llm) -> list[tuple[Case, bool, str]]:
    """执行意图层用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        result = await check_intent(case.payload, llm)
        passed = result.is_write == case.expected
        detail = f"判定={'写意图' if result.is_write else '查询'}，来源={result.source}，原因={result.reason}"
        results.append((case, passed, detail))
    return results


def _run_sandbox(cases: list[Case]) -> list[tuple[Case, bool, str]]:
    """执行沙箱用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        result = guard_sql(case.payload)
        passed = result.passed == case.expected
        if result.passed:
            detail = "放行（已加固）" if result.modified else "放行"
        else:
            detail = f"拦截：{result.reason}"
        results.append((case, passed, detail))
    return results


def _run_semantic(cases: list[MetricCase]) -> list[tuple[MetricCase, bool, str]]:
    """执行语义层用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        metric_infos = [_metric_state(m) for m in case.metric_infos]
        text = compile_metric_caliber(metric_infos)
        missing = [s for s in case.must_contain if s not in text]
        forbidden = [s for s in case.must_not_contain if s in text]
        passed = not missing and not forbidden
        if passed:
            detail = f"口径块 {len(text)} 字符，断言全部命中"
        else:
            parts = []
            if missing:
                parts.append(f"缺少 {missing}")
            if forbidden:
                parts.append(f"不应出现 {forbidden}")
            detail = "；".join(parts)
        results.append((case, passed, detail))
    return results


def _run_time(cases: list[TimeCase]) -> list[tuple[TimeCase, bool, str]]:
    """执行时间解析用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        result = parse_time_range_by_rule(case.query, date.fromisoformat(case.today))
        if case.start is None:
            passed = result is None
            detail = (
                "未识别到时间表达"
                if result is None
                else f"误判为 {result.start}~{result.end}"
            )
        elif result is None:
            passed = False
            detail = "未识别到时间表达"
        else:
            passed = (result.start, result.end) == (case.start, case.end)
            detail = f"[{result.start}, {result.end}) 粒度={result.granularity}"
        results.append((case, passed, detail))
    return results


def _run_empty(cases: list[EmptyCase]) -> list[tuple[EmptyCase, bool, str]]:
    """执行空结果兜底用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        notice = build_empty_notice(case.result, case.time_range)
        if case.expect_notice:
            missing = [s for s in case.must_contain if not notice or s not in notice]
            passed = notice is not None and not missing
            detail = f"提示：{notice}" if notice else "未给出提示"
            if missing:
                detail += f"；缺少 {missing}"
        else:
            passed = notice is None
            detail = "有数据，未提示" if notice is None else f"误报提示：{notice}"
        results.append((case, passed, detail))
    return results


def _run_clarify(cases: list[ClarifyCase]) -> list[tuple[ClarifyCase, bool, str]]:
    """执行澄清反问用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        clarify = detect_ambiguity(case.query, case.metrics)
        if case.expect_clarify:
            text = (
                f"{clarify.question} {' '.join(o.name for o in clarify.options)}"
                if clarify
                else ""
            )
            missing = [s for s in case.must_contain if s not in text]
            passed = clarify is not None and not missing
            if clarify is None:
                detail = "未追问（应为歧义）"
            else:
                detail = (
                    f"反问：{clarify.question} 候选={[o.name for o in clarify.options]}"
                )
                if missing:
                    detail += f"；缺少 {missing}"
        else:
            passed = clarify is None
            detail = "未追问" if clarify is None else f"误追问：{clarify.question}"
        results.append((case, passed, detail))
    return results


def _run_summary(cases: list[SummaryCase]) -> list[tuple[SummaryCase, bool, str]]:
    """执行结论生成用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        text = build_result_summary(case.result)
        if case.expect_none:
            passed = text is None
            detail = "返回 None" if text is None else f"误给出结论：{text}"
        else:
            missing = [s for s in case.must_contain if not text or s not in text]
            passed = text is not None and not missing
            detail = f"结论：{text}" if text else "未给出结论"
            if missing:
                detail += f"；缺少 {missing}"
        results.append((case, passed, detail))
    return results


def _outer_where_text(sql: str) -> str:
    """取最外层 SELECT 的 WHERE 文本（用于校验条件未被误注到最外层）"""
    import sqlglot

    statement = sqlglot.parse_one(sql, read="mysql")
    where = statement.args.get("where")
    return where.sql(dialect="mysql") if where is not None else ""


def _run_rls(cases: list[RlsCase]) -> list[tuple[RlsCase, bool, str]]:
    """执行行级权限注入用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        r = inject_row_filter(case.sql, case.region_ids)
        missing = [s for s in case.must_contain if s not in r.sql]
        passed = (
            r.applied == case.expect_applied
            and r.modified == case.expect_modified
            and not missing
        )
        detail = f"applied={r.applied}, modified={r.modified}"
        if missing:
            detail += f"；SQL 缺少片段 {missing}"

        # 负向断言：该片段不得出现在最外层 WHERE（子查询场景条件须下沉到表所在层）
        if case.outer_where_excludes:
            outer_where = _outer_where_text(r.sql)
            if case.outer_where_excludes in outer_where:
                passed = False
                detail += f"；最外层 WHERE 不应含 {case.outer_where_excludes!r}"

        results.append((case, passed, detail))
    return results


def _run_mask(cases: list[MaskCase]) -> list[tuple[MaskCase, bool, str]]:
    """执行列级脱敏用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        r = mask_rows(case.rows, case.mask_columns)
        bad = []
        for column, expected in case.expects:
            actual = r.rows[0].get(column) if r.rows else None
            if actual != expected:
                bad.append(f"{column} 期望={expected!r} 实际={actual!r}")
        passed = not bad
        detail = f"命中列={r.masked_columns}"
        if bad:
            detail += f"；{bad}"
        results.append((case, passed, detail))
    return results


def _run_chart(cases: list[ChartCase]) -> list[tuple[ChartCase, bool, str]]:
    """执行图表规划用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        chart = plan_chart(case.result, case.metric_infos)
        actual_type = chart["type"] if chart else None
        passed = actual_type == case.expect_type
        detail = f"实际={actual_type or '不出图'}"
        if chart:
            detail += f"；度量={chart['measures']}"
            if case.expect_measures is not None:
                measures_ok = chart["measures"] == case.expect_measures
                passed = passed and measures_ok
                if not measures_ok:
                    detail += f"（期望 {case.expect_measures}）"
            if case.expect_order is not None:
                order_ok = chart["order"] == case.expect_order
                passed = passed and order_ok
                detail += f"；轴序={chart['order']}"
                if not order_ok:
                    detail += f"（期望 {case.expect_order}）"
        results.append((case, passed, detail))
    return results


def _eval_trace_case(case: TraceCase) -> object:
    """按用例类型执行对应的纯函数，返回实际值"""
    if case.kind == "status":
        return resolve_run_status(case.payload)
    if case.kind == "format":
        return format_ms(case.payload)
    if case.kind == "sample":
        return should_sample(case.payload)
    if case.kind == "usage":
        return extract_usage(SimpleNamespace(**case.payload))
    if case.kind == "percentile":
        values, p = case.payload
        return round(percentile(values, p), 4)
    if case.kind == "span":
        trace = new_trace(sample_rate=1.0)
        for index, elapsed in enumerate(case.payload):
            trace.add_span(f"node_{index}", "success", elapsed)
        return trace.span_ms
    if case.kind == "width":
        return display_width(case.payload)
    if case.kind == "pad":
        text_value, width = case.payload
        return pad_display(text_value, width)
    raise ValueError(f"未知用例类型：{case.kind}")


def _run_trace(cases: list[TraceCase]) -> list[tuple[TraceCase, bool, str]]:
    """执行可观测用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        try:
            actual = _eval_trace_case(case)
        except Exception as e:  # noqa: BLE001 - 单条用例出错要打印出来，不能中断整组
            actual = f"抛异常 {type(e).__name__}"
        results.append((case, actual == case.expect, f"实际={actual}"))
    return results


def _print_time_group(
    title: str, results: list[tuple[TimeCase, bool, str]]
) -> tuple[int, int]:
    """打印时间解析用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        expect = f"[{case.start}, {case.end})" if case.start else "无约束"
        print(
            f"[{flag}] {case.cid}  参考日={case.today}  期望={expect}  {case.query}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_group(
    title: str,
    results: list[tuple[Case, bool, str]],
    true_label: str,
    false_label: str,
) -> tuple[int, int]:
    """打印一组用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        expect = true_label if case.expected else false_label
        print(
            f"[{flag}] {case.cid}  期望={expect}  {case.payload or '(空)'}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_semantic_group(
    title: str, results: list[tuple[MetricCase, bool, str]]
) -> tuple[int, int]:
    """打印语义层用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        names = [m.get("name", "") for m in case.metric_infos]
        print(
            f"[{flag}] {case.cid}  输入指标={names or '(空)'}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_empty_group(
    title: str, results: list[tuple[EmptyCase, bool, str]]
) -> tuple[int, int]:
    """打印空结果兜底用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        expect = "提示空结果" if case.expect_notice else "不提示"
        print(
            f"[{flag}] {case.cid}  期望={expect}  结果={case.result}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_clarify_group(
    title: str, results: list[tuple[ClarifyCase, bool, str]]
) -> tuple[int, int]:
    """打印澄清反问用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        expect = "应追问" if case.expect_clarify else "不打扰"
        print(
            f"[{flag}] {case.cid}  期望={expect}  候选={len(case.metrics)}个  {case.query}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_summary_group(
    title: str, results: list[tuple[SummaryCase, bool, str]]
) -> tuple[int, int]:
    """打印结论生成用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        expect = "无结论" if case.expect_none else "给结论"
        print(
            f"[{flag}] {case.cid}  期望={expect}  {case.result}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_rls_group(
    title: str, results: list[tuple[RlsCase, bool, str]]
) -> tuple[int, int]:
    """打印行级权限注入用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        regions = case.region_ids or "全量"
        print(
            f"[{flag}] {case.cid}  区域={regions}  {case.sql}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_mask_group(
    title: str, results: list[tuple[MaskCase, bool, str]]
) -> tuple[int, int]:
    """打印列级脱敏用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        print(
            f"[{flag}] {case.cid}  脱敏列={case.mask_columns or '无'}  {case.rows}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_chart_group(
    title: str, results: list[tuple[ChartCase, bool, str]]
) -> tuple[int, int]:
    """打印图表规划用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        print(
            f"[{flag}] {case.cid}  期望={case.expect_type or '不出图'}  行数={len(case.result)}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def _print_trace_group(
    title: str, results: list[tuple[TraceCase, bool, str]]
) -> tuple[int, int]:
    """打印可观测用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        print(
            f"[{flag}] {case.cid}  {case.kind}  期望={case.expect}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    use_llm = "--llm" in sys.argv[1:]

    llm = None
    if use_llm:
        from app.agent.llm import llm as real_llm

        llm = real_llm

    total_ok = 0
    total = 0

    r = _run_semantic(SEMANTIC_CASES)
    o, n = _print_semantic_group("一、语义层 · 指标口径冻结（离线）", r)
    total_ok += o
    total += n

    r = _run_time(TIME_CASES)
    o, n = _print_time_group("二、时间维度解析（离线）", r)
    total_ok += o
    total += n

    r = asyncio.run(_run_intent(INTENT_CASES, None))
    o, n = _print_group("三、意图层 · 规则命中 / 歧义放行（离线）", r, "写意图", "查询")
    total_ok += o
    total += n

    if use_llm:
        r = asyncio.run(_run_intent(INTENT_LLM_CASES, llm))
        o, n = _print_group(
            "四、意图层 · LLM 兜底（需有效 API Key）", r, "写意图", "查询"
        )
        total_ok += o
        total += n
    else:
        print("\n（跳过「四、意图层 · LLM 兜底」用例组；加 --llm 参数可开启）")

    r = _run_sandbox(SANDBOX_CASES)
    o, n = _print_group("五、SQL 沙箱 · 放行 / 拦截", r, "放行", "拦截")
    total_ok += o
    total += n

    r = _run_empty(EMPTY_CASES)
    o, n = _print_empty_group("六、结果兜底 · 空结果提示（离线）", r)
    total_ok += o
    total += n

    r = _run_clarify(CLARIFY_CASES)
    o, n = _print_clarify_group("七、澄清反问 · 歧义判定（离线）", r)
    total_ok += o
    total += n

    r = _run_summary(SUMMARY_CASES)
    o, n = _print_summary_group("八、结论生成 · 一句话总结（离线）", r)
    total_ok += o
    total += n

    r = _run_rls(RLS_CASES)
    o, n = _print_rls_group("九、权限 · 行级过滤注入 RLS（离线）", r)
    total_ok += o
    total += n

    r = _run_mask(MASK_CASES)
    o, n = _print_mask_group("十、权限 · 列级脱敏 Masking（离线）", r)
    total_ok += o
    total += n

    r = _run_chart(CHART_CASES)
    o, n = _print_chart_group("十一、可视化 · 图表规划 Chart Planning（离线）", r)
    total_ok += o
    total += n

    r = _run_trace(TRACE_CASES)
    o, n = _print_trace_group("十二、可观测 · 链路追踪 Tracing（离线）", r)
    total_ok += o
    total += n

    r = _run_recall(RECALL_CASES)
    o, n = _print_recall_group("十三、召回 · 并发检索 / 去重（离线）", r)
    total_ok += o
    total += n

    r = _run_route(ROUTE_CASES)
    o, n = _print_group("十四、模型路由 · 轻任务关 thinking（离线）", r, "通过", "不通过")
    total_ok += o
    total += n

    print(
        f"\n{'#' * 78}\n总计：{total_ok}/{total} 通过"
        + (" ✅" if total_ok == total else " ❌")
    )
    sys.exit(0 if total_ok == total else 1)


# —— 召回（并发检索 / 去重）用例 ——
# 三路召回改造为“批量向量化 + 并发检索”后，用这组离线用例守住核心行为：
# 批量只发一次、分组顺序稳定、按 id 去重、并发上限生效、异常照常抛出。
# 全部用假检索器，不依赖真实 Qdrant / ES / Embedding 服务。


@dataclass
class RecallCase:
    """一条召回（并发检索 / 去重）测试用例"""

    cid: str
    kind: str
    payload: object
    expect: object
    note: str = ""


class _EchoEmbedder:
    """假 Embedding 客户端：记录批量调用次数，向量用文本长度编码"""

    def __init__(self) -> None:
        self.batch_calls = 0

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [[float(len(text))] for text in texts]


RECALL_CASES: list[RecallCase] = [
    RecallCase(
        "R-01", "batch_embed_once", ["a", "bb", "ccc"], 1, "N 个关键词只批量向量化 1 次"
    ),
    RecallCase("R-02", "embed_empty", [], 0, "空关键词：不触发向量化"),
    RecallCase(
        "R-03",
        "keyword_order",
        ["华北", "华南"],
        ["华北", "华南"],
        "取值检索分组顺序与输入一致",
    ),
    RecallCase("R-04", "dedup_first", None, (["x", "y"], 1), "同 id 只保留首次出现的实体"),
    RecallCase("R-05", "concurrency_cap", 10, 3, "并发上限生效：峰值并发不超过 limit"),
    RecallCase("R-06", "propagate_error", None, "ValueError", "任一任务抛错则整体抛错"),
]


def _eval_recall_case(case: RecallCase) -> object:
    """执行单条召回用例，返回可与 expect 比较的实际值"""

    if case.kind == "batch_embed_once":
        embedder = _EchoEmbedder()

        async def _search(_: list[float]) -> list:
            return []

        asyncio.run(embed_and_search(list(case.payload), embedder, _search, limit=8))
        return embedder.batch_calls

    if case.kind == "embed_empty":
        embedder = _EchoEmbedder()

        async def _search(_: list[float]) -> list:
            return []

        asyncio.run(embed_and_search([], embedder, _search, limit=8))
        return embedder.batch_calls

    if case.kind == "keyword_order":

        async def _search(keyword: str) -> list:
            return [SimpleNamespace(id=keyword)]

        groups = asyncio.run(search_by_keywords(list(case.payload), _search, limit=8))
        return [group[0].id for group in groups]

    if case.kind == "dedup_first":
        first = SimpleNamespace(id="x", score=1)
        duplicate = SimpleNamespace(id="x", score=2)
        other = SimpleNamespace(id="y", score=3)
        merged = dedup_items([[first], [duplicate, other]])
        return [item.id for item in merged], merged[0].score

    if case.kind == "concurrency_cap":
        tasks = int(case.payload)
        state = {"current": 0, "peak": 0}

        async def _probe() -> int:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.02)
            state["current"] -= 1
            return 1

        asyncio.run(gather_limited((_probe() for _ in range(tasks)), limit=3))
        return state["peak"]

    if case.kind == "propagate_error":

        async def _boom() -> int:
            raise ValueError("boom")

        async def _ok() -> int:
            return 1

        try:
            asyncio.run(gather_limited([_boom(), _ok()], limit=2))
        except ValueError:
            return "ValueError"
        return "no-error"

    raise ValueError(f"未知用例类型：{case.kind}")


def _run_recall(cases: list[RecallCase]) -> list[tuple[RecallCase, bool, str]]:
    """执行召回用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        try:
            actual = _eval_recall_case(case)
        except Exception as e:  # noqa: BLE001 - 单条用例出错要打印出来，不能中断整组
            actual = f"抛异常 {type(e).__name__}"
        results.append((case, actual == case.expect, f"实际={actual}"))
    return results


def _print_recall_group(
    title: str, results: list[tuple[RecallCase, bool, str]]
) -> tuple[int, int]:
    """打印召回用例结果，返回 (通过数, 总数)"""
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    ok = 0
    for case, passed, detail in results:
        flag = "PASS" if passed else "FAIL"
        if passed:
            ok += 1
        print(
            f"[{flag}] {case.cid}  {case.kind}  期望={case.expect}"
            f"\n        {detail}   // {case.note}"
        )
    print(f"—— 小结：{ok}/{len(results)} 通过")
    return ok, len(results)


# —— 模型路由（轻任务关 thinking）用例 ——
# B1：把“关键词扩展 / 表·指标过滤”这 5 个轻任务节点切到关闭思维链的 llm_fast，
# generate_sql 等保留默认 llm。这组离线用例守住：路由选择正确、配置可一键回滚、
# 关闭参数确实挂在模型实例上（不需要联网）。
LIGHT_TASKS = [
    "recall_column",
    "recall_value",
    "recall_metric",
    "filter_table",
    "filter_metric",
]

ROUTE_CASES: list[Case] = [
    Case("L-01", "route_fast", "recall_value", True, "轻任务节点走关闭思维链的 llm_fast"),
    Case("L-02", "route_fast_all", "(5 个轻任务节点)", True, "5 个轻任务节点全部走 llm_fast"),
    Case("L-03", "route_default", "generate_sql", True, "SQL 生成节点保留默认 llm（thinking 开）"),
    Case("L-04", "config_cover", "(llm.no_think_nodes)", True, "配置覆盖 5 个轻任务且不含 generate_sql"),
    Case("L-05", "thinking_off_body", "(extra_body)", True, "llm_fast 真的透传 enable_thinking=False"),
    Case("L-06", "rollback", "(改配置)", True, "改配置即可回滚：路由随配置变化"),
]


def _eval_route_case(case: Case) -> bool:
    """执行单条模型路由用例，返回是否通过"""

    if case.group == "route_fast":
        return get_llm(case.payload) is llm_fast

    if case.group == "route_fast_all":
        return all(get_llm(node) is llm_fast for node in LIGHT_TASKS)

    if case.group == "route_default":
        return get_llm(case.payload) is llm

    if case.group == "config_cover":
        configured = set(app_config.llm.no_think_nodes)
        return set(LIGHT_TASKS) <= configured and "generate_sql" not in configured

    if case.group == "thinking_off_body":
        return llm_fast.bound.extra_body == {"enable_thinking": False}

    if case.group == "rollback":
        # 临时改配置验证路由即时跟随，跑完立刻还原，避免污染后续用例
        original = app_config.llm.no_think_nodes
        try:
            app_config.llm.no_think_nodes = ["generate_sql"]
            return get_llm("generate_sql") is llm_fast and get_llm("recall_value") is llm
        finally:
            app_config.llm.no_think_nodes = original

    raise ValueError(f"未知用例类型：{case.group}")


def _run_route(cases: list[Case]) -> list[tuple[Case, bool, str]]:
    """执行模型路由用例，返回 (用例, 是否通过, 说明) 列表"""
    results = []
    for case in cases:
        try:
            passed = _eval_route_case(case)
        except Exception as e:  # noqa: BLE001 - 单条用例出错要打印出来，不能中断整组
            results.append((case, False, f"抛异常 {type(e).__name__}: {e}"))
            continue
        results.append((case, passed, "通过" if passed else "未通过"))
    return results


if __name__ == "__main__":
    main()
