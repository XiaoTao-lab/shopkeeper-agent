"""
行级权限注入节点

位于 `validate_sql` 通过之后、`run_sql` 执行之前：此时 SQL 已经过安全沙箱与语法校验，
且在"校验失败→校正"的回路之后，**只注入一次**，从根上避免重复叠加。

节点读当前角色 → 取区域白名单 → 把过滤条件硬注入 SQL；全量角色则原样放行。
结果写回 state，供 run_sql 组装权限提示与 finalize 写审计。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger
from app.core.rls_guard import inject_row_filter


async def inject_rls(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """按角色把区域行级过滤注入到 SQL"""

    writer = runtime.stream_writer
    step = "注入行级权限"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        sql = state["sql"]
        # 角色已在入口归一化；此处再兜一次默认值，保证节点可被单独测试
        role_name = state.get("user_role") or app_config.rbac.default_role
        role_cfg = app_config.rbac.resolve(role_name)

        result = inject_row_filter(sql, role_cfg.region_ids)

        if result.applied and result.modified:
            logger.info(
                f"行级权限注入：角色={role_name} 区域={result.region_ids} → {result.sql}"
            )
        else:
            logger.info(f"行级权限：角色={role_name} 未注入过滤（{result.reason}）")

        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "sql": result.sql,
            "rls_applied": result.applied,
            "rls_region_ids": result.region_ids if result.applied else [],
        }

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
