"""
电商问数 Agent 图编排

使用 LangGraph 把问数智能体的各个节点串成一条可观测的执行链路
当前链路已经落地关键词抽取和多路召回，字段和指标走 Qdrant 向量检索，字段取值走 ES 全文检索
整体流程先抽取用户问题关键词，再并行召回字段 字段取值和指标信息，
随后合并召回结果 过滤候选表和指标 补充额外上下文，冻结时间范围与指标口径，最后生成 校验 修正并执行 SQL。
三条短路路径（意图拦截 / 澄清反问 / 安全沙箱拦截）统一收口到 finalize：
既保证审计与链路追踪不漏掉"没查到数"的场景，也让图只有一个终点、便于扩展。
"""

import asyncio

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.context import DataAgentContext
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.check_clarify import check_clarify
from app.agent.nodes.check_intent import check_intent
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.finalize import finalize
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.guard_sql import guard_sql
from app.agent.nodes.inject_rls import inject_rls
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.resolve_metric import resolve_metric
from app.agent.nodes.resolve_time import resolve_time
from app.agent.nodes.rewrite_query import rewrite_query
from app.agent.nodes.run_sql import run_sql
from app.agent.nodes.validate_sql import validate_sql
from app.agent.state import DataAgentState
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.tracing import traced
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

# StateGraph 声明整张图使用的状态结构和运行时上下文结构
graph_builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

# 注册节点：每个节点负责问数链路中的一个清晰步骤。
# 除收尾节点外，所有业务节点统一用 traced(name, fn) 包一层薄埋点——记录节点名、耗时、
# 状态与异常，用于链路追踪。因为各节点骨架一致（writer → try → 干活 → writer），
# 这里包一层即可做到"埋点零侵入"，节点内部一行都不用改。
# finalize 本身是"记录者"（写审计与 trace），给自己记 span 需要多写一次库，故不包。
graph_builder.add_node("rewrite_query", traced("rewrite_query", rewrite_query))
graph_builder.add_node("check_intent", traced("check_intent", check_intent))
graph_builder.add_node("extract_keywords", traced("extract_keywords", extract_keywords))
graph_builder.add_node("recall_column", traced("recall_column", recall_column))
graph_builder.add_node("recall_value", traced("recall_value", recall_value))
graph_builder.add_node("recall_metric", traced("recall_metric", recall_metric))
graph_builder.add_node(
    "merge_retrieved_info", traced("merge_retrieved_info", merge_retrieved_info)
)
graph_builder.add_node("check_clarify", traced("check_clarify", check_clarify))
graph_builder.add_node("filter_metric", traced("filter_metric", filter_metric))
graph_builder.add_node("filter_table", traced("filter_table", filter_table))
graph_builder.add_node(
    "add_extra_context", traced("add_extra_context", add_extra_context)
)
graph_builder.add_node("resolve_time", traced("resolve_time", resolve_time))
graph_builder.add_node("resolve_metric", traced("resolve_metric", resolve_metric))
graph_builder.add_node("generate_sql", traced("generate_sql", generate_sql))
graph_builder.add_node("guard_sql", traced("guard_sql", guard_sql))
graph_builder.add_node("validate_sql", traced("validate_sql", validate_sql))
graph_builder.add_node("correct_sql", traced("correct_sql", correct_sql))
graph_builder.add_node("inject_rls", traced("inject_rls", inject_rls))
graph_builder.add_node("run_sql", traced("run_sql", run_sql))
graph_builder.add_node("finalize", finalize)


def _route_after_clarify(state: DataAgentState):
    """澄清检测后的分支：命中歧义则短路收尾，否则并行进入表/指标两个过滤节点"""
    if state.get("clarify_question"):
        return "finalize"
    return ["filter_table", "filter_metric"]


# 入口先做多轮问题改写：结合历史把口语问题补全成独立问题，再进入意图安全校验
graph_builder.add_edge(START, "rewrite_query")
graph_builder.add_edge("rewrite_query", "check_intent")

# 命中写操作意图直接短路到收尾，正常查询才进入关键词抽取
# 注意：短路也必须经过 finalize——否则澄清 / 拦截这类"没查到数"的场景不会留下
# 审计与链路追踪记录，而这些恰恰是最需要被观测到的情况。
graph_builder.add_conditional_edges(
    source="check_intent",
    path=lambda state: "extract_keywords" if state.get("error") is None else "finalize",
    path_map={"extract_keywords": "extract_keywords", "finalize": "finalize"},
)

# 关键词抽取后并行进入三类召回，分别面向字段 字段值和业务指标
graph_builder.add_edge("extract_keywords", "recall_column")
graph_builder.add_edge("extract_keywords", "recall_value")
graph_builder.add_edge("extract_keywords", "recall_metric")

# 三路召回都完成后，再进入统一的信息合并节点
graph_builder.add_edge("recall_column", "merge_retrieved_info")
graph_builder.add_edge("recall_value", "merge_retrieved_info")
graph_builder.add_edge("recall_metric", "merge_retrieved_info")

# 合并后的候选信息先做歧义检测：命中"指标说不清"就下发反问并短路，
# 把选择权交还用户；无歧义才继续拆成表过滤和指标过滤两条线
graph_builder.add_edge("merge_retrieved_info", "check_clarify")
graph_builder.add_conditional_edges(
    source="check_clarify",
    path=_route_after_clarify,
    path_map={
        "filter_table": "filter_table",
        "filter_metric": "filter_metric",
        "finalize": "finalize",
    },
)
graph_builder.add_edge("filter_table", "add_extra_context")
graph_builder.add_edge("filter_metric", "add_extra_context")
# 上下文齐备后，先冻结时间范围与指标口径，再进入 SQL 生成
graph_builder.add_edge("add_extra_context", "resolve_time")
graph_builder.add_edge("resolve_time", "resolve_metric")
graph_builder.add_edge("resolve_metric", "generate_sql")
# SQL 生成后先过安全沙箱，再做语法校验
graph_builder.add_edge("generate_sql", "guard_sql")

# 安全校验通过才继续走语法校验，被拦截则短路到收尾（同样要留痕）
graph_builder.add_conditional_edges(
    source="guard_sql",
    path=lambda state: "validate_sql" if state.get("error") is None else "finalize",
    path_map={"validate_sql": "validate_sql", "finalize": "finalize"},
)

# SQL 校验通过后，先按角色注入行级权限过滤，再执行；校验失败则先进入修正节点
graph_builder.add_conditional_edges(
    source="validate_sql",
    path=lambda state: "inject_rls" if state.get("error") is None else "correct_sql",
    path_map={"inject_rls": "inject_rls", "correct_sql": "correct_sql"},
)
# 行级过滤注入发生在校正回路之后、执行之前：只注入一次，避免重复叠加
graph_builder.add_edge("inject_rls", "run_sql")
# 修正后的 SQL 仍要重新过一遍安全沙箱，避免修正过程引入危险语句
graph_builder.add_edge("correct_sql", "guard_sql")
# 执行完成后进入 finalize 写审计，再结束
graph_builder.add_edge("run_sql", "finalize")
graph_builder.add_edge("finalize", END)

# 编译后的 graph 是对外使用的 Agent 执行入口
# 挂上 InMemorySaver 后，图会按 thread_id 记住每个会话的状态（含 messages 对话历史），
# 从而支持多轮：同一个 thread_id 的多次调用共享历史，不同 thread_id 相互隔离。
# 注意：内存版 checkpoint 只在本进程生命周期内有效；生产可换成 Redis/Postgres saver。
#
# checkpoint 会把整个 state（含 retrieved_column_infos 等自定义实体）序列化落盘，
# 这些实体不是基础类型，默认会触发 msgpack 的"未注册类型"警告并在未来版本被阻断。
# 因此显式把它们加入白名单，避免警告与后续兼容性风险。
# 若后续在 state 里引入新的自定义实体类型，需要同步补充到这里。
_ALLOWED_MSGPACK_MODULES = [
    ("app.entities.column_info", "ColumnInfo"),
    ("app.entities.metric_info", "MetricInfo"),
    ("app.entities.value_info", "ValueInfo"),
    ("app.entities.table_info", "TableInfo"),
]
checkpointer = InMemorySaver(
    serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
)
graph = graph_builder.compile(checkpointer=checkpointer)

# print(graph.get_graph().draw_mermaid())

if __name__ == "__main__":

    async def test():
        """本地调试关键词抽取和字段 指标 取值三路召回链路"""

        # 多路召回和上下文补全会访问 Qdrant、Embedding、ES、Meta MySQL 和 DW MySQL
        qdrant_client_manager.init()
        embedding_client_manager.init()
        es_client_manager.init()
        meta_mysql_client_manager.init()
        dw_mysql_client_manager.init()

        # Meta MySQL 用来补齐元数据，DW MySQL 用来读取数据库方言和版本
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            meta_mysql_repository = MetaMySQLRepository(meta_session)
            dw_mysql_repository = DWMySQLRepository(dw_session)

            # 字段和指标分别使用不同 Qdrant collection，取值检索使用 ES index
            column_qdrant_repository = ColumnQdrantRepository(
                qdrant_client_manager.client
            )
            metric_qdrant_repository = MetricQdrantRepository(
                qdrant_client_manager.client
            )
            value_es_repository = ValueESRepository(es_client_manager.client)

            # 当前只需要传入原始问题，后续节点会逐步写回召回、过滤和额外上下文结果
            state = DataAgentState(query="统计华北地区的销售总额")
            context = DataAgentContext(
                column_qdrant_repository=column_qdrant_repository,
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=metric_qdrant_repository,
                value_es_repository=value_es_repository,
                meta_mysql_repository=meta_mysql_repository,
                dw_mysql_repository=dw_mysql_repository,
            )

            # stream_mode="custom" 会接收各节点通过 runtime.stream_writer 写出的进度信息
            # 挂了 checkpointer 后必须提供 thread_id，本地调试用一个固定值即可
            async for chunk in graph.astream(
                input=state,
                context=context,
                config={"configurable": {"thread_id": "local-debug"}},
                stream_mode="custom",
            ):
                print(chunk)

        # 关闭显式创建的异步客户端，避免本地调试时连接资源悬挂
        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()

    asyncio.run(test())
