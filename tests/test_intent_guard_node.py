"""
check_intent 节点集成测试

用一个只含 check_intent 的最小 LangGraph 图，隔离验证节点在图中的真实行为：
命中写意图时写入 error（触发中止），正常查询时放行。
写意图用例走真实规则层（不联网）；放行用例用假判别函数替身，避免真实 LLM 调用。
"""

import asyncio

from langgraph.graph import END, START, StateGraph

from app.agent.state import DataAgentState
from app.core.intent_guard import IntentResult


def _compile_intent_graph():
    """编译只含 check_intent 的最小图，用于隔离验证节点行为"""
    from app.agent.nodes.check_intent import check_intent

    builder = StateGraph(state_schema=DataAgentState)
    builder.add_node("check_intent", check_intent)
    builder.add_edge(START, "check_intent")
    builder.add_edge("check_intent", END)
    return builder.compile()


def test_node_blocks_write_intent() -> None:
    """写意图应被拦截，error 被写入状态（走真实规则层，不联网）"""
    graph = _compile_intent_graph()
    result = asyncio.run(graph.ainvoke({"query": "把华东地区的GMV改为1"}))
    assert result["error"] is not None


def test_node_passes_normal_query(monkeypatch) -> None:
    """正常查询应放行，error 为 None（用假判别函数避免联网）"""

    async def _fake_check(query, llm=None):
        return IntentResult(is_write=False, reason="查询", source="llm")

    monkeypatch.setattr(
        "app.agent.nodes.check_intent.check_intent_safety", _fake_check
    )
    graph = _compile_intent_graph()
    result = asyncio.run(graph.ainvoke({"query": "统计华北地区的销售总额"}))
    assert result["error"] is None
