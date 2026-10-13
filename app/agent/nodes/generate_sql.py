"""
SQL 生成节点

负责根据用户问题和前面整理出的表结构 指标 日期 数据库环境生成候选 SQL。
本节点只生成 SQL，不做校验和执行，后续会交给 validate_sql 和 run_sql 继续处理。
"""

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """基于已检索和过滤的上下文生成 SQL"""

    writer = runtime.stream_writer
    step = "生成SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 这些上下文都由前置节点准备完成，模型只在给定表 字段 指标口径范围内生成 SQL
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        # 冻结后的指标口径约束（由 resolve_metric 节点产出），为空时给一句兜底说明
        metric_caliber_text = (
            state.get("metric_caliber_text")
            or "（本次未匹配到已冻结的标准指标口径，请基于用户问题语义生成）"
        )
        # 冻结后的时间范围约束（由 resolve_time 节点产出），为空时说明本次无明确时间范围
        time_range_text = (
            state.get("time_range_text")
            or "（本次未解析到明确的时间范围，如问题隐含时间条件请谨慎处理）"
        )
        date_info = state["date_info"]
        db_info = state["db_info"]
        query = state["query"]

        prompt = PromptTemplate(
            template=load_prompt("generate_sql"),
            input_variables=[
                "table_infos",
                "metric_infos",
                "metric_caliber_text",
                "time_range_text",
                "date_info",
                "db_info",
                "query",
            ],
        )
        # SQL 生成节点只需要纯文本 SQL，不能要求模型输出 JSON 或 Markdown 代码块
        output_parser = StrOutputParser()
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                # YAML 更适合放进提示词：保留嵌套结构 顺序和中文说明，方便模型理解表字段关系
                "table_infos": yaml.dump(
                    table_infos, allow_unicode=True, sort_keys=False
                ),
                "metric_infos": yaml.dump(
                    metric_infos, allow_unicode=True, sort_keys=False
                ),
                # 口径与时间约束是纯文本块，直接透传，避免被 YAML 转义干扰模型阅读
                "metric_caliber_text": metric_caliber_text,
                "time_range_text": time_range_text,
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                "query": query,
            }
        )
        logger.info(f"生成的SQL：{result}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"sql": result}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
