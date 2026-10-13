"""
rewrite_query 多轮改写节点离线测试

覆盖两部分：
1. ``format_history`` 纯格式化逻辑（无模型、无数据库，可离线跑）；
2. 用只含 rewrite_query 的最小图，验证"首轮无历史时原样透传且不调用模型"。

需要真实模型的多轮改写效果在 app/scripts/run_multiturn.py 里做端到端验证。
"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.rewrite_query import (
    _EMPTY_HISTORY,
    format_history,
    rewrite_query,
)
from app.agent.state import DataAgentState


def _compile_rewrite_graph():
    """编译只含 rewrite_query 的最小图，隔离验证节点在图中的行为"""
    builder = StateGraph(state_schema=DataAgentState)
    builder.add_node("rewrite_query", rewrite_query)
    builder.add_edge(START, "rewrite_query")
    builder.add_edge("rewrite_query", END)
    return builder.compile()


def test_empty_history_returns_placeholder() -> None:
    """无历史时给出占位文案，保证提示词里的 {history} 不为空"""
    assert format_history([]) == _EMPTY_HISTORY


def test_render_user_question_and_sql() -> None:
    """用户问题渲染为 Q:，AI 消息（上一轮 SQL）渲染为 SQL:"""
    messages = [
        HumanMessage(content="统计2025年第一季度各地区的GMV"),
        AIMessage(content="SELECT region_name, SUM(order_amount) FROM fact_order"),
    ]
    text = format_history(messages)
    assert "Q: 统计2025年第一季度各地区的GMV" in text
    assert "SQL: SELECT region_name, SUM(order_amount) FROM fact_order" in text


def test_limit_keeps_most_recent() -> None:
    """超过上限时只保留最近若干条"""
    messages = [HumanMessage(content=f"q{i}") for i in range(10)]
    text = format_history(messages, limit=3)
    lines = text.splitlines()
    assert len(lines) == 3
    assert "q9" in lines[-1]
    assert "q0" not in text


def test_blank_content_is_skipped() -> None:
    """空白消息被跳过，不产生空行"""
    messages = [HumanMessage(content="   "), HumanMessage(content="有效问题")]
    assert format_history(messages).strip() == "Q: 有效问题"


def test_all_blank_falls_back_to_placeholder() -> None:
    """全部为空白消息时退回占位文案"""
    messages = [HumanMessage(content="   "), HumanMessage(content="")]
    assert format_history(messages) == _EMPTY_HISTORY


def test_first_turn_passthrough_without_llm() -> None:
    """首轮无历史：原样透传，写入一条 Human 消息，且清空 error"""
    graph = _compile_rewrite_graph()
    result = asyncio.run(graph.ainvoke({"query": "统计上个月的GMV", "error": "旧错误"}))
    assert result["query"] == "统计上个月的GMV"
    assert result["rewritten_query"] == "统计上个月的GMV"
    # 上一轮残留的 error 必须被清掉，否则会误触发后续条件分支
    assert result["error"] is None
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], HumanMessage)
    assert result["messages"][0].content == "统计上个月的GMV"
