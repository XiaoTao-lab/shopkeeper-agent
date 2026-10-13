"""
guard_sql 节点集成测试

用一个只含 guard_sql 的最小 LangGraph 图，隔离验证节点在图中的真实行为：
通过时把加固后的 SQL 写回状态，拦截时写入 error。
不依赖数据库、Qdrant、ES 等外部服务。
"""

import asyncio

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.guard_sql import guard_sql
from app.agent.state import DataAgentState


def _compile_guard_graph():
    """编译只含 guard_sql 的最小图，用于隔离验证节点行为"""
    builder = StateGraph(state_schema=DataAgentState)
    builder.add_node("guard_sql", guard_sql)
    builder.add_edge(START, "guard_sql")
    builder.add_edge("guard_sql", END)
    return builder.compile()


def test_node_passes_and_injects_limit() -> None:
    """正常 SELECT 应通过，并写回注入 LIMIT 的 SQL"""
    graph = _compile_guard_graph()
    result = asyncio.run(graph.ainvoke({"sql": "SELECT * FROM fact_order"}))
    assert result["error"] is None
    assert "LIMIT 1000" in result["sql"].upper()


def test_node_rejects_delete() -> None:
    """DELETE 应被拦截，error 被写入状态"""
    graph = _compile_guard_graph()
    result = asyncio.run(graph.ainvoke({"sql": "DELETE FROM fact_order"}))
    assert result["error"] is not None
    assert "写操作" in result["error"]


def test_node_rejects_multi_statement() -> None:
    """多语句注入应被拦截"""
    graph = _compile_guard_graph()
    result = asyncio.run(
        graph.ainvoke({"sql": "SELECT * FROM fact_order; DROP TABLE dim_customer;"})
    )
    assert result["error"] is not None
