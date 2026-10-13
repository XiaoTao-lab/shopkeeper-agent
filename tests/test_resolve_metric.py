"""
resolve_metric 口径冻结节点测试

覆盖两层：
1. 纯函数 compile_metric_caliber：口径文本编译、空口径跳过、跨表提示、默认过滤拼接
2. 最小图集成：节点在图中的真实行为，写入 metric_caliber_text

不依赖数据库、Qdrant、ES 与大模型，全部离线可跑。
"""

import asyncio

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.resolve_metric import compile_metric_caliber, resolve_metric
from app.agent.state import DataAgentState, MetricInfoState


def _metric(
    name: str,
    formula: str,
    base_table: str = "fact_order",
    unit: str = "元",
    time_column: str = "fact_order.date_id",
    default_filters: list[str] | None = None,
    description: str = "",
) -> MetricInfoState:
    """快速构造一个指标状态，减少测试噪声"""
    return MetricInfoState(
        name=name,
        description=description,
        relevant_columns=[],
        alias=[],
        formula=formula,
        base_table=base_table,
        time_column=time_column,
        default_filters=default_filters or [],
        unit=unit,
    )


# ---------- compile_metric_caliber ----------


def test_no_formula_returns_empty() -> None:
    """没有任何标准计算式时返回空串，避免下发空口径"""
    assert compile_metric_caliber([]) == ""
    assert compile_metric_caliber([_metric("GMV", "")]) == ""


def test_single_metric_carries_formula() -> None:
    """单个指标的口径应完整包含名称 计算式 主表 单位"""
    text = compile_metric_caliber([_metric("GMV", "SUM(fact_order.order_amount)")])
    assert "GMV" in text
    assert "SUM(fact_order.order_amount)" in text
    assert "主表：fact_order" in text
    assert "单位：元" in text
    assert "指标口径约束" in text


def test_two_metrics_both_present() -> None:
    """多指标应各自成段"""
    text = compile_metric_caliber(
        [
            _metric("GMV", "SUM(fact_order.order_amount)"),
            _metric("AOV", "AVG(fact_order.order_amount)"),
        ]
    )
    assert "SUM(fact_order.order_amount)" in text
    assert "AVG(fact_order.order_amount)" in text


def test_metric_without_formula_is_skipped() -> None:
    """无口径的指标不进入约束文本"""
    text = compile_metric_caliber(
        [
            _metric("GMV", "SUM(fact_order.order_amount)"),
            _metric("神秘指标", ""),
        ]
    )
    assert "GMV" in text
    assert "神秘指标" not in text


def test_default_filters_joined() -> None:
    """默认过滤条件应拼接展示"""
    text = compile_metric_caliber(
        [
            _metric(
                "GMV",
                "SUM(fact_order.order_amount)",
                default_filters=["fact_order.order_id IS NOT NULL"],
            )
        ]
    )
    assert "fact_order.order_id IS NOT NULL" in text


def test_multi_base_table_warning() -> None:
    """跨多个主表时应给出关联提示"""
    text = compile_metric_caliber(
        [
            _metric("GMV", "SUM(fact_order.order_amount)", base_table="fact_order"),
            _metric("客户数", "COUNT(dim_customer.customer_id)", base_table="dim_customer"),
        ]
    )
    assert "跨越多个主表" in text
    assert "fact_order" in text and "dim_customer" in text


def test_single_base_table_no_warning() -> None:
    """同主表不应触发跨表提示"""
    text = compile_metric_caliber(
        [
            _metric("GMV", "SUM(fact_order.order_amount)"),
            _metric("AOV", "AVG(fact_order.order_amount)"),
        ]
    )
    assert "跨越多个主表" not in text


# ---------- 最小图集成 ----------


def _compile_resolve_graph():
    """编译只含 resolve_metric 的最小图，用于隔离验证节点行为"""
    builder = StateGraph(state_schema=DataAgentState)
    builder.add_node("resolve_metric", resolve_metric)
    builder.add_edge(START, "resolve_metric")
    builder.add_edge("resolve_metric", END)
    return builder.compile()


def test_node_writes_caliber_text() -> None:
    """节点应把口径文本写入 metric_caliber_text"""
    graph = _compile_resolve_graph()
    result = asyncio.run(
        graph.ainvoke(
            {
                "metric_infos": [
                    _metric("GMV", "SUM(fact_order.order_amount)"),
                ]
            }
        )
    )
    assert "SUM(fact_order.order_amount)" in result["metric_caliber_text"]


def test_node_empty_when_no_metric() -> None:
    """无指标时写入空串，链路继续"""
    graph = _compile_resolve_graph()
    result = asyncio.run(graph.ainvoke({"metric_infos": []}))
    assert result["metric_caliber_text"] == ""
