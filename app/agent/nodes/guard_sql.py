"""
SQL 安全校验节点

在 SQL 进入数据库执行前，对 LLM 生成的 SQL 做只读强制校验与加固。
校验通过时把加固后的 SQL（例如已注入 LIMIT）写回状态，继续走校验链路；
校验失败时写入 error + blocked_by 标记并短路到 finalize 收尾（留痕后结束），杜绝危险语句落到生产库。
blocked_by 用于把"被沙箱拦下"和"节点真报错"分开归因，避免污染故障率。
本节点只做安全边界控制，不改写业务语义。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger

# 核心校验函数与节点函数同名，这里用别名导入避免命名冲突
from app.core.sql_guard import guard_sql as check_sql_safety


async def guard_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """对生成的 SQL 做只读安全校验与加固"""

    writer = runtime.stream_writer
    step = "SQL安全校验"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # sql 可能来自 generate_sql 初次生成，也可能来自 correct_sql 修正结果
        sql = state["sql"]
        result = check_sql_safety(sql, max_rows=app_config.sql_guard.max_rows)

        # 拦截：写入 error + blocked_by，条件边据此分支到 finalize 收尾，SQL 不再进入数据库
        if not result.passed:
            logger.warning(f"SQL安全校验拦截：{result.reason}；原始SQL：{sql}")
            # 直接向流中写一条 error 消息，前端可立即展示拦截原因
            writer(
                {"type": "error", "message": f"SQL 安全校验未通过：{result.reason}"}
            )
            writer({"type": "progress", "step": step, "status": "error"})
            return {"error": result.reason, "blocked_by": "sql_guard"}

        # 通过：若 SQL 被加固（如注入 LIMIT），把加固后的 SQL 写回状态
        if result.modified:
            logger.info(f"SQL安全加固：{sql} -> {result.sql}")

        writer({"type": "progress", "step": step, "status": "success"})
        # 显式把 error / blocked_by 置回空值，避免 correct_sql 残留的旧错误、
        # 或上一轮经 checkpointer 恢复的拦截标记，影响后续条件分支
        return {"sql": result.sql, "error": None, "blocked_by": ""}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
