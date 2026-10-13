"""
链路追踪仓储

把一次问数的追踪结果写入 Meta MySQL：

- ``trace``：一次问数一行（总耗时 / 节点数 / token 成本 / 最终状态）；
- ``trace_span``：每个业务节点一行（节点名 / 耗时 / 状态 / 错误）。

写入采取 **fail-open**：追踪是旁路能力，写失败只记日志、绝不连累正常问数
（与 ``AuditLogRepository`` 同一套纪律）。
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.conf.app_config import app_config
from app.core.log import logger
from app.core.tracing import TraceContext

_INSERT_TRACE = text(
    """
    INSERT INTO `trace`
        (trace_id, thread_id, request_id, `query`, `role`, status,
         total_ms, span_ms, span_count, llm_calls, prompt_tokens, completion_tokens, created_at)
    VALUES
        (:trace_id, :thread_id, :request_id, :query, :role, :status,
         :total_ms, :span_ms, :span_count, :llm_calls, :prompt_tokens, :completion_tokens, :created_at)
    """
)

_INSERT_SPAN = text(
    """
    INSERT INTO trace_span (trace_id, seq, node, status, elapsed_ms, error)
    VALUES (:trace_id, :seq, :node, :status, :elapsed_ms, :error)
    """
)


class TraceRepository:
    """负责写入问数链路追踪记录"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, trace: TraceContext, status: str) -> None:
        """写一条 trace 头 + N 条 span 明细（fail-open）。

        Args:
            trace: 本次问数的追踪上下文（含已记账的 span 与 token）
            status: 本次问数的最终状态（success / error / clarify）
        """
        header = {
            "trace_id": trace.trace_id,
            "thread_id": trace.thread_id,
            "request_id": trace.request_id,
            "query": trace.query,
            "role": trace.role,
            "status": status,
            "total_ms": trace.elapsed_ms(),
            "span_ms": trace.span_ms,
            "span_count": len(trace.spans),
            "llm_calls": trace.llm_calls,
            "prompt_tokens": trace.prompt_tokens,
            "completion_tokens": trace.completion_tokens,
            "created_at": datetime.now(),
        }

        try:
            await self.session.execute(_INSERT_TRACE, header)

            # store_span 关闭时只留头表，省掉明细写入（高并发场景）
            if app_config.observability.store_span and trace.spans:
                rows = [
                    {
                        "trace_id": trace.trace_id,
                        "seq": span.seq,
                        "node": span.node,
                        "status": span.status,
                        "elapsed_ms": span.elapsed_ms,
                        "error": span.error,
                    }
                    for span in trace.spans
                ]
                # 批量插入：一次 executemany，避免 N 次往返
                await self.session.execute(_INSERT_SPAN, rows)

            await self.session.commit()
            # 落库成功才置真：服务层的异常兜底据此判断"是否还需要补写"，
            # 保证同一条问数（trace_id 有唯一键）不会被写两次。
            trace.persisted = True
        except Exception as e:
            # 追踪是旁路能力：写失败只记日志，绝不影响已经返回给用户的问数结果
            logger.error(f"链路追踪写入失败：{e}")
