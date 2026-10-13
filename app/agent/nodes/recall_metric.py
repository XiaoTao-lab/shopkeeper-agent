"""
指标召回节点

负责根据用户问题从指标向量知识库中召回候选指标
它帮助 Agent 把“销售额 转化率 客单价”等业务表达映射到已定义指标
实现路径和字段召回类似：关键词扩展 -> Embedding -> Qdrant 相似度检索 -> MetricInfo 去重

性能：与字段召回同构——先批量向量化（1 次往返），再按并发度上限并发检索 Qdrant。
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
from app.entities.metric_info import MetricInfo
from app.prompt.prompt_loader import load_prompt

# 本节点模型实例：关键词扩展属“轻任务”，配置里默认关闭思维链（thinking）以显著提速
llm = get_llm("recall_metric")


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题语义相关的业务指标"""

    writer = runtime.stream_writer
    step = "召回指标信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # query 用于让 LLM 生成指标层检索词，keywords 来自上游的通用关键词抽取
        query = state["query"]
        keywords = state["keywords"]
        # 指标召回使用向量检索，需要 Embedding 客户端和指标 Qdrant 仓储配合
        embedding_client = runtime.context["embedding_client"]
        metric_qdrant_repository = runtime.context["metric_qdrant_repository"]

        # 用 LLM 把用户问法扩展成“指标概念”列表，例如“销售总额”可扩展出“GMV”“成交额”
        prompt = PromptTemplate(
            template=load_prompt("extend_keywords_for_metric_recall"),
            input_variables=["query"],
        )
        # 指标扩展 prompt 要求只输出 JSON 数组，解析后 result 就是 list[str]
        output_parser = JsonOutputParser()
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | llm | output_parser

        result = await chain.ainvoke({"query": query})

        # 通用关键词和指标扩展词都参与召回；去重同时保留顺序，保证候选指标顺序稳定
        ordered_keywords = list(dict.fromkeys([*keywords, *result]))

        # 批量向量化 + 并发检索，并发度受配置上限约束
        search_results = await embed_and_search(
            ordered_keywords,
            embedding_client,
            metric_qdrant_repository.search,
            limit=app_config.recall.concurrency,
        )

        # 用指标 id 做唯一键，避免多个关键词命中同一个指标时重复写入 state
        retrieved_metric_infos: list[MetricInfo] = dedup_items(search_results)

        # 写回 state 的是业务实体列表，后续过滤节点不需要关心 Qdrant 原始 point 结构
        logger.info(f"检索到指标信息：{[info.id for info in retrieved_metric_infos]}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_metric_infos": retrieved_metric_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
