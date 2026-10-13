"""
澄清检测节点

在「召回信息合并」之后、「表/指标过滤」之前，判断用户问题是否存在**指标歧义**：
问题里只说了泛化度量词（"金额""数量""数据"），却没说清是哪个指标。

判定有歧义时：写回 ``clarify_question``，把反问作为 ``clarify`` 事件下发，
并让流程在此**短路**（不进入过滤与 SQL 生成），把选择权交还用户。
用户下一轮的回答会经由同会话的多轮改写与历史拼接，自然接续成完整问题。

判定逻辑是纯函数（``app/core/clarify_guard.py``），本节点只负责编排与事件下发，
便于离线单测。
"""

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.clarify_guard import detect_ambiguity
from app.core.log import logger

_STEP = "检测问题歧义"


async def check_clarify(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """检测指标歧义，命中则下发反问并短路"""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": _STEP, "status": "running"})

    try:
        query = state["query"]
        metric_infos = state.get("metric_infos") or []

        clarify = detect_ambiguity(query, metric_infos)

        if not clarify:
            logger.info("歧义检测：未发现指标歧义，继续执行")
            writer({"type": "progress", "step": _STEP, "status": "success"})
            # 显式清空，避免上一轮（经 checkpointer 恢复）的澄清标记残留导致本轮误短路
            return {"clarify_question": "", "clarify_options": []}

        logger.info(f"歧义检测：命中指标歧义 → {clarify.reason}")
        # 事件里带结构化候选（名字 + 含义），前端渲染成可点选芯片
        writer(
            {
                "type": "clarify",
                "message": clarify.question,
                "options": [
                    {"name": option.name, "description": option.description}
                    for option in clarify.options
                ],
            }
        )
        writer({"type": "progress", "step": _STEP, "status": "success"})
        # 把反问记进对话历史：下一轮"改写多轮问题"才能理解用户是在回答这个澄清
        return {
            "clarify_question": clarify.question,
            "clarify_options": [option.name for option in clarify.options],
            "messages": [AIMessage(content=clarify.question)],
        }

    except Exception as e:
        logger.error(f"{_STEP} failed: {e}")
        writer({"type": "progress", "step": _STEP, "status": "error"})
        raise
