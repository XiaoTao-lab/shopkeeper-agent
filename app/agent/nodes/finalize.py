"""
收尾（审计 + 追踪）节点

链路最后一个节点，把本轮问数的两类事实落库：

- **审计留痕**（``audit_log``）：谁（role）、何时、问了什么、最终执行的 SQL、
  返回多少行、是否做了行级过滤 / 列脱敏——回答"**谁查了什么**"；
- **链路追踪**（``trace`` / ``trace_span``）：全链路耗时、每个节点的耗时与状态、
  LLM 调用次数与 token 消耗——回答"**这步花了多久 / 成没成 / 贵不贵**"。

两者都是旁路能力：写失败不会影响已经返回给用户的结果（仓储层 fail-open），
离线脚本（评测、多轮自测）不注入仓储时自动跳过，不影响主链路。
"""

import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.tracing import get_trace, resolve_run_status
from app.repositories.mysql.meta.audit_log_repository import AuditLogRepository
from app.repositories.mysql.meta.trace_repository import TraceRepository

_STEP = "记录审计"


async def _write_trace(state: DataAgentState, runtime: Runtime[DataAgentContext]) -> None:
    """把本次问数的追踪结果落库（旁路；缺仓储 / 未采样 / 未开追踪时静默跳过）"""
    trace = get_trace()
    trace_repository: TraceRepository | None = runtime.context.get("trace_repository")
    if trace is None or not trace.sampled or trace_repository is None:
        return
    # 状态由纯函数归因：澄清反问是正常业务短路，不能算失败
    await trace_repository.insert(trace, resolve_run_status(state))


async def finalize(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """写入审计与追踪记录，结束本轮问数"""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": _STEP, "status": "running"})

    try:
        started_at = state.get("started_at")
        elapsed_ms = int(time.time() * 1000) - started_at if started_at else None

        # 离线脚本（评测、多轮自测）可能不带审计仓储；缺省时跳过审计，不影响主链路
        audit_log_repository: AuditLogRepository | None = runtime.context.get(
            "audit_log_repository"
        )
        if audit_log_repository is None:
            logger.info("未配置审计仓储，跳过审计留痕")
        else:
            await audit_log_repository.insert(state, elapsed_ms)

        await _write_trace(state, runtime)

        logger.info(
            f"收尾完成：role={state.get('user_role')} rows={state.get('row_count')} "
            f"rls={state.get('rls_applied')} masked={state.get('masked_columns')}"
        )
        writer({"type": "progress", "step": _STEP, "status": "success"})
        return {}

    except Exception as e:
        logger.error(f"{_STEP} failed: {e}")
        writer({"type": "progress", "step": _STEP, "status": "error"})
        raise
