"""
多轮问题改写节点

用户在多轮对话里常用口语延续上一轮查询，例如"那上个月呢""只看华东地区""换成销售额"，
这类句子自身不完整。本节点在图的最前面，先结合对话历史把本轮问题改写成一句
**语义完整、可独立执行**的问数问题，再交给后续链路。

为什么放在最前面而不是改 SQL：问数链路的召回、时间解析、指标口径冻结都依赖
"问题文本"本身，只有先把问题补全，后面每个节点才能拿到完整语义，避免各节点
各自去猜"上个月"相对什么、"它"指哪个指标。

设计要点：
- 首轮无历史时不调用模型，原样透传（规则化、零成本、可离线测试）；
- 历史里存"改写后的独立问题"，保证每一轮都在自洽语境上继续改写，避免指代层层套娃；
- 纯格式化逻辑 ``format_history`` 独立成函数，便于离线单测。
"""

import time

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt

_STEP = "改写多轮问题"
# 参与改写的历史消息条数上限：只取最近若干条，避免长会话把提示词撑爆
_HISTORY_LIMIT = 6
# 无历史时给模型的占位文案，保证提示词里 {history} 永远有内容
_EMPTY_HISTORY = "(无历史，这是第一轮)"


def _ai_label(content: str) -> str:
    """给 AI 消息挑一个前缀：上一轮执行的 SQL 标 ``SQL:``，其余（如澄清提问）标 ``A:``"""
    head = content.strip()[:6].upper()
    if head.startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")):
        return "SQL"
    return "A"


def format_history(messages: list[AnyMessage], limit: int = _HISTORY_LIMIT) -> str:
    """把消息历史渲染成改写提示词用的紧凑文本。

    只保留最近 ``limit`` 条；用户问题标 ``Q:``，AI 消息按内容标 ``SQL:``（上一轮执行的 SQL）
    或 ``A:``（如澄清提问），让模型既能看到此前问过什么，也能看到上一轮的检索条件或澄清语境。

    Args:
        messages: 完整消息历史
        limit: 参与渲染的最近消息条数

    Returns:
        渲染后的历史文本；无有效内容时返回占位文案
    """
    if not messages:
        return _EMPTY_HISTORY

    recent = messages[-limit:]
    lines: list[str] = []
    for message in recent:
        content = str(message.content).strip()
        if not content:
            continue
        if isinstance(message, HumanMessage):
            lines.append(f"Q: {content}")
        elif isinstance(message, AIMessage):
            lines.append(f"{_ai_label(content)}: {content}")

    return "\n".join(lines) if lines else _EMPTY_HISTORY


async def rewrite_query(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """结合对话历史，把本轮口语问题改写成可独立执行的完整问题"""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": _STEP, "status": "running"})

    try:
        query = state["query"]
        history: list[AnyMessage] = state.get("messages") or []

        # 首轮无历史：不调用模型，原样透传，同时清空上一轮可能残留的 error，
        # 避免旧错误经 checkpointer 恢复后误触发后续的条件分支
        if not history:
            logger.info(f"多轮改写：首轮，原样透传 → {query}")
            writer({"type": "progress", "step": _STEP, "status": "success"})
            return {
                "query": query,
                "rewritten_query": query,
                "started_at": int(time.time() * 1000),
                "error": None,
                "messages": [HumanMessage(content=query)],
            }

        prompt = PromptTemplate(
            template=load_prompt("rewrite_query"),
            input_variables=["history", "query"],
        )
        # prompt -> 模型 -> 取纯文本
        chain = prompt | llm | StrOutputParser()
        rewritten = (
            await chain.ainvoke({"history": format_history(history), "query": query})
        ).strip()

        # 模型偶发返回空串时退回原问题：宁可少改写，也不让问题丢失
        if not rewritten:
            logger.warning("多轮改写结果为空，回退为原始问题")
            rewritten = query

        logger.info(f"多轮改写：{query} → {rewritten}")
        writer({"type": "progress", "step": _STEP, "status": "success"})
        # 历史里存"改写后的独立问题"，让后续每一轮都能在自洽语境上继续改写
        return {
            "query": rewritten,
            "rewritten_query": rewritten,
            "started_at": int(time.time() * 1000),
            "error": None,
            "messages": [HumanMessage(content=rewritten)],
        }

    except Exception as e:
        logger.error(f"{_STEP} failed: {e}")
        writer({"type": "progress", "step": _STEP, "status": "error"})
        raise
