"""
字段召回节点

负责根据关键词从字段向量知识库中召回候选字段
它解决的是“用户问题可能对应哪些数据库字段”的问题
本章的主线是：关键词扩展 -> Embedding -> Qdrant 相似度检索 -> ColumnInfo 去重

性能：关键词扩展后先一次性批量向量化（1 次网络往返），再按并发度上限并发检索 Qdrant。
原先的“逐词串行”要付 2N 次往返（N 次 Embedding + N 次检索），现在压到 1 + N/并发。
"""

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger
from app.core.recall import dedup_items, embed_and_search
from app.entities.column_info import ColumnInfo
from app.prompt.prompt_loader import load_prompt

# 本节点模型实例：关键词扩展属“轻任务”，配置里默认关闭思维链（thinking）以显著提速
llm = get_llm("recall_column")


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题语义相关的字段元数据"""

    writer = runtime.stream_writer
    step = "召回字段信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # state 保存图内业务中间结果：原始问题和上游抽取出的关键词
        keywords = state["keywords"]
        query = state["query"]
        # context 保存外部运行时工具：向量仓储和 Embedding 客户端
        column_qdrant_repository = runtime.context["column_qdrant_repository"]
        embedding_client = runtime.context["embedding_client"]

        # 用 LLM 把用户问法扩展成“字段语义”列表，例如“销售总额”可扩展出“销售金额”
        prompt = PromptTemplate(
            template=load_prompt("extend_keywords_for_column_recall"),
            input_variables=["query"],
        )
        # 提示词要求模型只输出 JSON 数组，解析后 result 就是 list[str]
        output_parser = JsonOutputParser()
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | llm | output_parser

        result = await chain.ainvoke({"query": query})

        # 原始关键词和 LLM 扩展词一起参与召回；用 dict.fromkeys 去重并保留顺序，
        # 让每次召回的检索顺序（进而候选字段顺序）稳定可复现
        ordered_keywords = list(dict.fromkeys([*keywords, *result]))

        # 批量向量化 + 并发检索（并发度受配置上限约束），返回“每个关键词一组命中”
        search_results = await embed_and_search(
            ordered_keywords,
            embedding_client,
            column_qdrant_repository.search,
            limit=app_config.recall.concurrency,
        )

        # 用字段 id 做唯一键：多个关键词、同一字段的多个向量点都可能命中同一个字段
        retrieved_column_infos: list[ColumnInfo] = dedup_items(search_results)

        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_column_infos": retrieved_column_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
