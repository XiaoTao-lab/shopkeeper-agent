"""
时间维度解析测试

覆盖三层：
1. 规则层：常见中文时间表达 → 半开区间（含跨年边界）
2. 纯函数：口径文本渲染、无时间表达返回 None
3. 最小图集成：resolve_time 节点写入 time_range / time_range_text

规则层不依赖数据库与模型，全部离线可跑；节点用例通过 monkeypatch 避免联网。
"""

import asyncio
from datetime import date

import pytest
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.resolve_time import render_time_range_text, resolve_time
from app.agent.state import DataAgentState
from app.core.time_parser import TimeRange, parse_time_range, parse_time_range_by_rule

# 固定参考日，保证用例确定性（2026-09-23 是周三）
TODAY = date(2026, 9, 23)


@pytest.mark.parametrize(
    ("query", "start", "end", "granularity"),
    [
        ("统计今天的销售额", "2026-09-23", "2026-09-24", "day"),
        ("昨天卖了多少", "2026-09-22", "2026-09-23", "day"),
        ("前天呢", "2026-09-21", "2026-09-22", "day"),
        ("本周GMV", "2026-09-21", "2026-09-28", "week"),
        ("上周的订单量", "2026-09-14", "2026-09-21", "week"),
        ("本月销售额", "2026-09-01", "2026-10-01", "month"),
        ("上个月的GMV", "2026-08-01", "2026-09-01", "month"),
        ("本季度订单量", "2026-07-01", "2026-10-01", "quarter"),
        ("上个季度", "2026-04-01", "2026-07-01", "quarter"),
        ("今年的GMV", "2026-01-01", "2027-01-01", "year"),
        ("去年销售额", "2025-01-01", "2026-01-01", "year"),
        ("最近7天的订单", "2026-09-17", "2026-09-24", "day"),
        ("近30日GMV", "2026-08-25", "2026-09-24", "day"),
        ("最近两周", "2026-09-10", "2026-09-24", "week"),
        ("最近3个月", "2026-07-01", "2026-10-01", "month"),
        ("上半年销售额", "2026-01-01", "2026-07-01", "half_year"),
        ("下半年", "2026-07-01", "2027-01-01", "half_year"),
        ("2025年上半年的GMV", "2025-01-01", "2025-07-01", "half_year"),
        ("2025年3月销售额", "2025-03-01", "2025-04-01", "month"),
        ("2025Q1的订单", "2025-01-01", "2025-04-01", "quarter"),
        ("2025年第三季度", "2025-07-01", "2025-10-01", "quarter"),
        ("2025-03的数据", "2025-03-01", "2025-04-01", "month"),
        ("2025年全年", "2025-01-01", "2026-01-01", "year"),
        ("3月的销售额", "2026-03-01", "2026-04-01", "month"),
        # 跨月区间：必须包含整个结束月，不能只算起始月
        ("2025年2月到3月的GMV", "2025-02-01", "2025-04-01", "month"),
        ("2月到3月的销售额", "2026-02-01", "2026-04-01", "month"),
        ("2025年2月至3月", "2025-02-01", "2025-04-01", "month"),
        ("2025年2月-3月", "2025-02-01", "2025-04-01", "month"),
        # 跨年：12月到1月 → 今年12月 ~ 明年1月
        ("12月到1月", "2026-12-01", "2027-02-01", "month"),
    ],
)
def test_rule_layer_examples(
    query: str, start: str, end: str, granularity: str
) -> None:
    """规则层应把常见时间表达解析成预期半开区间"""
    result = parse_time_range_by_rule(query, TODAY)
    assert result is not None
    assert (result.start, result.end, result.granularity) == (start, end, granularity)
    assert result.source == "rule"


def test_no_time_expression_returns_none() -> None:
    """无时间表达时不应凭空造区间"""
    assert parse_time_range_by_rule("各地区的订单量", TODAY) is None


@pytest.mark.parametrize(
    ("query", "start", "end"),
    [
        ("上月", "2025-12-01", "2026-01-01"),
        ("上季度", "2025-10-01", "2026-01-01"),
        ("去年", "2025-01-01", "2026-01-01"),
        ("最近30天", "2025-12-17", "2026-01-16"),
    ],
)
def test_cross_year_boundary(query: str, start: str, end: str) -> None:
    """跨年边界必须算对（模型最容易错的场景）"""
    result = parse_time_range_by_rule(query, date(2026, 1, 15))
    assert result is not None
    assert (result.start, result.end) == (start, end)


@pytest.mark.parametrize(
    ("query", "start", "end", "granularity"),
    [
        ("查一下25年第一季度的交易总额", "2025-01-01", "2025-04-01", "quarter"),
        ("25年第一季度", "2025-01-01", "2025-04-01", "quarter"),
        ("25年Q2的数据", "2025-04-01", "2025-07-01", "quarter"),
        ("25年3月的销售额", "2025-03-01", "2025-04-01", "month"),
        ("25年的GMV", "2025-01-01", "2026-01-01", "year"),
        ("25年上半年", "2025-01-01", "2025-07-01", "half_year"),
        # 两位数按 strptime %y 约定：69~99 落到 19xx
        ("99年的数据", "1999-01-01", "2000-01-01", "year"),
        # 四位年份不能被两位规则打断
        ("2025年第一季度", "2025-01-01", "2025-04-01", "quarter"),
    ],
)
def test_two_digit_year(
    query: str, start: str, end: str, granularity: str
) -> None:
    """两位数年份（25年）应进规则层，不再依赖 LLM 兜底"""
    result = parse_time_range_by_rule(query, TODAY)
    assert result is not None
    assert (result.start, result.end, result.granularity) == (start, end, granularity)
    assert result.source == "rule"


def test_half_open_invariant() -> None:
    """区间必须是半开区间：起始 < 结束"""
    result = parse_time_range_by_rule("上个月", TODAY)
    assert result is not None
    assert result.start < result.end


def test_render_time_range_text() -> None:
    """渲染文本应包含起止 粒度与原始表达"""
    text = render_time_range_text(
        TimeRange("2026-08-01", "2026-09-01", "month", "上个月", "rule")
    )
    assert "2026-08-01" in text
    assert "2026-09-01" in text
    assert "上个月" in text
    assert "month" in text


def test_parse_time_range_without_llm() -> None:
    """无 LLM 时：规则命中返回区间，未命中返回 None"""
    hit = asyncio.run(parse_time_range("上个月", TODAY))
    assert hit is not None and hit.start == "2026-08-01"
    assert asyncio.run(parse_time_range("各地区的订单量", TODAY)) is None


# ---------- 最小图集成 ----------


def _compile_time_graph():
    """编译只含 resolve_time 的最小图，用于隔离验证节点行为"""
    builder = StateGraph(state_schema=DataAgentState)
    builder.add_node("resolve_time", resolve_time)
    builder.add_edge(START, "resolve_time")
    builder.add_edge("resolve_time", END)
    return builder.compile()


def _state(query: str) -> dict:
    """构造带固定日期的最小输入状态"""
    return {
        "query": query,
        "date_info": {"date": "2026-09-23", "weekday": "Wednesday", "quarter": "Q3"},
    }


def test_node_resolves_time_from_rule() -> None:
    """节点应把规则解析结果写入 time_range 与 time_range_text"""
    graph = _compile_time_graph()
    result = asyncio.run(graph.ainvoke(_state("统计上个月的销售额")))
    assert result["time_range"]["start"] == "2026-08-01"
    assert result["time_range"]["end"] == "2026-09-01"
    assert "2026-08-01" in result["time_range_text"]


def test_node_no_time_degrades_gracefully(monkeypatch) -> None:
    """无时间表达时降级为空约束，链路继续（置空 llm 避免联网）"""
    monkeypatch.setattr("app.agent.nodes.resolve_time.llm", None)
    graph = _compile_time_graph()
    result = asyncio.run(graph.ainvoke(_state("各地区的订单量")))
    assert result["time_range"] is None
    assert result["time_range_text"] == ""
