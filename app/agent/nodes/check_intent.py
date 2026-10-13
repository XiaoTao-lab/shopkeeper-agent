"""
意图安全校验节点

问数链路的入口闸门：在抽取关键词之前，先判断用户问题里有没有「写操作」意图
（修改 / 删除 / 新增数据）。一旦命中，直接中止链路并给出明确提示，
避免后续用一条伪造的查询 SQL 去迎合用户，把本不该改的数据伪装成"查得到"。

它是 SQL 安全沙箱之外补齐的「意图层」防线：
- 意图层（本节点）：拦住"想改数据"的问法
- 生成层（generate_sql prompt）：约束模型只生成查询
- 执行层（guard_sql 沙箱）：拦住真落到数据库的写语句
三层从后到前依次兜底，任何一层命中都能把危险请求挡在库外。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState

# 节点函数与核心判别函数同名，这里用别名导入避免命名冲突
from app.core.intent_guard import check_intent as check_intent_safety
from app.core.log import logger


async def check_intent(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """判断用户问题是否为写操作意图，是则中止链路"""

    writer = runtime.stream_writer
    step = "意图安全校验"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        result = await check_intent_safety(query, llm)

        # 命中写意图：写入 error + blocked_by 标记，条件边据此短路到 finalize 收尾
        # （不再直接走 END——必须经 finalize 留下审计与链路追踪，否则拦截场景无据可查）
        # blocked_by 用来把"被意图防线拦下"和"节点真报错"分开归因，避免污染故障率。
        if result.is_write:
            logger.warning(
                f"意图安全校验拦截：{result.reason}；原始问题：{query}"
            )
            writer(
                {
                    "type": "error",
                    "message": (
                        f"当前问题包含「写操作」意图（{result.reason}），"
                        "本系统仅支持数据查询，无法修改、删除或新增数据。"
                    ),
                }
            )
            writer({"type": "progress", "step": step, "status": "error"})
            return {"error": result.reason, "blocked_by": "intent"}

        # 通过：显式把 error / blocked_by 置回空值，保证后续条件边不会误判
        # （state 经 checkpointer 跨轮持久化，不清理会把上一轮的拦截标记带进本轮）
        logger.info(f"意图安全校验通过（来源={result.source}）：{result.reason}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"error": None, "blocked_by": ""}

    except Exception as e:
        # 意图判别属于安全前置，抛错会让整条链路不可用；
        # 这里保守放行（不写 error），交由后续 SQL 沙箱兜底，并记录异常
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"error": None, "blocked_by": ""}
