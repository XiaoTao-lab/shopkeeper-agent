"""
审计日志仓储

把每一次问数落一条审计记录到 Meta MySQL 的 audit_log 表：
谁、何时、问了什么、最终执行了什么 SQL、返回多少行、是否做了权限过滤 / 脱敏。

状态区分四种：``ok``（正常出数）、``blocked``（意图/沙箱拦截）、``clarify``（澄清反问）、
``error``（节点真实抛异常，图崩了）。
链路里三条短路路径（意图拦截 / 澄清反问 / 安全沙箱拦截）都会收口到 finalize，
因此"没查到数"的场景同样留痕，而不是被静默丢弃。
"被拦截"与"真报错"必须分开：前者是**预期的安全行为**，后者才是**故障**，
混在一起会让安全审计看不出"系统到底出没出问题"。

写入采取 **fail-open**：审计失败不应连累正常问数，因此异常只在内部记日志，不向上抛出。
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.tracing import resolve_run_status

# 链路归因（success/blocked/error/clarify）→ 审计用词。两者含义一一对应，
# 审计里保留更直白的 blocked（安全拦截）与 error（真实故障），不再把二者混为一谈。
_STATUS_MAP = {
    "success": "ok",
    "blocked": "blocked",
    "error": "error",
    "clarify": "clarify",
}


class AuditLogRepository:
    """负责写入问数审计记录"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(
        self, state: DataAgentState, elapsed_ms: int | None, status: str | None = None
    ) -> None:
        """根据最终状态写一条审计记录（fail-open）。

        Args:
            state: 链路最终状态
            elapsed_ms: 本次问数耗时（毫秒）；取不到时为 None
            status: 显式指定**链路归因结果**（``STATUS_*``）；缺省时由
                ``resolve_run_status(state)`` 自动归因。异常兜底路径需要显式传
                ``STATUS_ERROR``——因为图执行中途抛错时 finalize 不执行，
                本地 state 里并没有 error 标记，自动归因会误判成 ok。
        """

        region_ids = state.get("rls_region_ids") or []
        masked_columns = state.get("masked_columns") or []

        sql = text(
            """
            INSERT INTO audit_log
                (ts, `role`, thread_id, `query`, rewritten_query, `sql`,
                 row_count, rls_applied, region_ids, masked_columns, elapsed_ms, status)
            VALUES
                (:ts, :role, :thread_id, :query, :rewritten_query, :sql,
                 :row_count, :rls_applied, :region_ids, :masked_columns, :elapsed_ms, :status)
            """
        )
        resolved = status or resolve_run_status(state)
        params = {
            "ts": datetime.now(),
            "role": state.get("user_role"),
            "thread_id": state.get("thread_id"),
            "query": state.get("query"),
            "rewritten_query": state.get("rewritten_query"),
            "sql": state.get("sql"),
            "row_count": state.get("row_count"),
            "rls_applied": 1 if state.get("rls_applied") else 0,
            "region_ids": ",".join(region_ids) if region_ids else None,
            "masked_columns": ",".join(masked_columns) if masked_columns else None,
            "elapsed_ms": elapsed_ms,
            # 状态归因与链路追踪共用同一套规则：澄清反问是正常短路，
            # 被拦截是预期的安全行为，只有节点真报错才算 error。
            "status": _STATUS_MAP[resolved],
        }

        try:
            await self.session.execute(sql, params)
            await self.session.commit()
        except Exception as e:
            # 审计是旁路能力：写失败只记日志，绝不影响已经返回给用户的问数结果
            logger.error(f"审计日志写入失败：{e}")
