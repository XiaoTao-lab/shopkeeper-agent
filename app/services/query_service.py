"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建初始 State、组装 Runtime Context、开启链路追踪上下文、消费 graph.astream 的流式输出，
并统一包装成 SSE 文本返回给路由层。

另外承担一个兜底职责：图执行中途抛异常时，收尾节点不会执行，
这里补写一条 error trace 与审计记录，避免真实故障成为观测盲区。
"""

import json
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import api_graph
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.context import request_id_ctx_var
from app.core.log import logger
from app.core.security import Identity
from app.core.tracing import (
    STATUS_ERROR,
    TraceContext,
    needs_fallback_persist,
    new_trace,
    reset_trace,
    set_trace,
)
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.audit_log_repository import AuditLogRepository
from app.repositories.mysql.meta.history_repository import (
    HistoryRepository,
    PreparedRun,
)
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.mysql.meta.trace_repository import TraceRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.session_service import scope_hash


def _decode_context(context_json: list[dict]) -> list:
    """从数据库恢复对话上下文，构造 LangChain 消息"""
    messages = []
    for item in context_json or []:
        msg_type = item.get("type")
        content = item.get("content", "")
        msg_id = item.get("id", str(uuid.uuid4()))
        if msg_type == "human":
            messages.append(HumanMessage(content=content, id=msg_id))
        elif msg_type == "ai":
            messages.append(AIMessage(content=content, id=msg_id))
    return messages


def _encode_context(messages: list, limit: int = 6) -> list[dict]:
    """提取最近 N 条 human/ai 消息，序列化存储"""
    result = []
    for msg in messages[-limit:]:
        msg_type = "human" if isinstance(msg, HumanMessage) else ("ai" if isinstance(msg, AIMessage) else None)
        if msg_type:
            result.append({"type": msg_type, "id": getattr(msg, "id", None) or str(uuid.uuid4()), "content": msg.content})
    return result


def _resolve_committed_terminal(
    persisted: dict,
    event: dict,
    status: str,
    outcome: str,
) -> tuple[dict, str, str]:
    """Use the database terminal state when this request lost the run race.

    A run can expire or be recovered while its graph is still finishing.  In
    that case the in-memory result is stale and must never be streamed as a
    success after the repository has rejected its commit.
    """
    persisted_status = persisted.get("status") or status
    persisted_outcome = persisted.get("outcome") or outcome
    if persisted_status == status and persisted_outcome == outcome:
        return event, status, outcome

    persisted_event = persisted.get("terminal_event")
    if isinstance(persisted_event, dict):
        return persisted_event, persisted_status, persisted_outcome

    return (
        {"type": "error", "message": "查询状态已变化，请重新提问。"},
        persisted_status,
        persisted_outcome,
    )


class QueryService:
    """封装一次问数查询所需的业务编排逻辑"""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
        audit_log_repository: AuditLogRepository,
        trace_repository: TraceRepository,
        history_repository: HistoryRepository | None = None,
    ):
        # MySQL 仓储分别负责元数据补全和真实数仓环境信息读取
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository

        # 召回链路依赖的向量检索、Embedding 和全文检索能力由依赖层注入
        self.embedding_client = embedding_client
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository

        # 审计日志仓储：每次问数结束后写一条留痕（旁路能力，fail-open）
        self.audit_log_repository = audit_log_repository

        # 链路追踪仓储：写 trace 头 + 节点 span，供"慢在哪 / 错在哪 / 贵在哪"分析
        self.trace_repository = trace_repository

        # 历史仓储：管理会话、查询记录、用户归属
        self.history_repository = history_repository

    async def query(
        self, prepared: PreparedRun, identity: Identity
    ):
        """执行一次问数工作流，并逐段产出 SSE 消息。

        Args:
            prepared: 预登记的查询信息（含 session_id, run_id, context, deadline_at）
            identity: 当前用户身份
        """
        session_id = prepared.session_id
        run_id = prepared.run_id

        # 角色先归一化：未知/缺失 → default_role，避免"给不出角色就放行全量"
        effective_role = app_config.rbac.resolve_name(identity.role)

        # 从数据库恢复最近 6 条消息
        initial_messages = _decode_context(prepared.context)

        # State 只放会被图节点读写和合并的业务数据，外部工具对象不塞进 State
        state = DataAgentState(
            query=prepared.raw_query,
            user_role=effective_role,
            thread_id=session_id,
            messages=initial_messages,
        )
        # Context 保存本次图执行需要复用的外部依赖，节点通过 runtime.context 读取
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
            audit_log_repository=self.audit_log_repository,
            trace_repository=self.trace_repository,
        )

        # 开启本次问数的追踪上下文
        trace = new_trace(
            query=prepared.raw_query,
            thread_id=session_id,
            request_id=request_id_ctx_var.get(),
            role=effective_role,
        )
        trace_token = set_trace(trace)

        # 图执行中随时可能抛异常，except 分支要用它组装"已跑到的状态"；
        # 必须在 try 之前初始化，否则 meta 事件就断连时会 NameError
        last_state = state

        try:
            # 先回一条 meta，把本次会话 id 和 run_id 同步给前端
            yield (
                "data: "
                + json.dumps(
                    {"type": "meta", "thread_id": session_id, "run_id": run_id}, ensure_ascii=False
                )
                + "\n\n"
            )

            # 执行图，收集 custom 事件和最终 values
            terminal_event = None

            async for mode, payload in api_graph.astream(
                input=state,
                context=context,
                stream_mode=["custom", "values"],
            ):
                if mode == "values":
                    last_state = payload
                elif mode == "custom":
                    if isinstance(payload, dict):
                        event_type = payload.get("type")
                        if event_type == "progress":
                            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        elif event_type in {"result", "clarify", "error"}:
                            terminal_event = payload

            # 根据 terminal_event 确定 outcome 和 status
            if terminal_event:
                event_type = terminal_event.get("type", "error")
                if event_type == "result":
                    outcome = "empty" if terminal_event.get("empty") else "result"
                    status = "completed"
                elif event_type == "clarify":
                    outcome = "clarify"
                    status = "completed"
                else:
                    outcome = "error"
                    status = "failed"
            else:
                # 没有终态事件，视为异常
                terminal_event = {"type": "error", "message": "流程已结束，后端未返回查询结果。"}
                outcome = "error"
                status = "failed"

            # 提取最终上下文（最近 6 条消息）
            final_messages = last_state.get("messages", [])
            new_context = _encode_context(final_messages)

            # 提取改写后的查询
            rewritten_query = last_state.get("rewritten_query")

            # 持久化到历史
            if self.history_repository:
                try:
                    scope = scope_hash(identity.role)
                    persisted_run = await self.history_repository.finish(
                        prepared=prepared,
                        owner=identity.subject,
                        scope=scope,
                        status=status,
                        outcome=outcome,
                        event=terminal_event,
                        context=new_context,
                        rewritten=rewritten_query,
                    )
                    terminal_event, status, outcome = _resolve_committed_terminal(
                        persisted_run,
                        terminal_event,
                        status,
                        outcome,
                    )
                except Exception as persist_err:
                    logger.error(f"历史保存失败：{persist_err}")
                    # 历史写失败时，返回错误而不是成功
                    terminal_event = {"type": "error", "message": "结果保存失败，请重试。"}
                    yield f"data: {json.dumps(terminal_event, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'run_id': run_id, 'status': 'failed', 'outcome': 'error'}, ensure_ascii=False)}\n\n"
                    return

            # 发送终态事件和 done
            yield f"data: {json.dumps(terminal_event, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id, 'status': status, 'outcome': outcome}, ensure_ascii=False)}\n\n"

        except Exception as e:
            # 图执行中途抛异常 → LangGraph 直接中断，收尾节点 finalize 不会执行。
            # 这里兜底补写 trace / 审计，否则"真崩掉"的问数在库里完全没有记录
            await self._persist_failure(trace, state, e)

            # 持久化失败状态
            if self.history_repository:
                try:
                    scope = scope_hash(identity.role)
                    error_event = {"type": "error", "message": str(e)}
                    # 异常中断也要把本轮问题补进历史：下一轮追问才知道问过什么。
                    # 改写已成功则用改写后的独立问题（避免与已追加的消息重复）
                    question = (
                        last_state.get("rewritten_query") if isinstance(last_state, dict) else None
                    ) or prepared.raw_query
                    messages_now = list(last_state.get("messages") or []) if isinstance(last_state, dict) else []
                    tail = [str(m.content).strip() for m in messages_now[-3:]]
                    if question.strip() not in tail:
                        messages_now.append(HumanMessage(content=question))
                    persisted_run = await self.history_repository.finish(
                        prepared=prepared,
                        owner=identity.subject,
                        scope=scope,
                        status="failed",
                        outcome="error",
                        event=error_event,
                        context=_encode_context(messages_now),
                    )
                    error_event, status, outcome = _resolve_committed_terminal(
                        persisted_run,
                        error_event,
                        "failed",
                        "error",
                    )
                except Exception:
                    error_event = {"type": "error", "message": str(e)}
                    status, outcome = "failed", "error"
            else:
                error_event = {"type": "error", "message": str(e)}
                status, outcome = "failed", "error"

            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id, 'status': status, 'outcome': outcome}, ensure_ascii=False)}\n\n"
        finally:
            # 还原上下文：避免同一进程里并发请求之间 trace 串味
            reset_trace(trace_token)

    async def _persist_failure(
        self, trace: TraceContext, state: DataAgentState, exc: Exception
    ) -> None:
        """图执行抛异常时的兜底留痕（trace + 审计），全程 fail-open。

        正常路径由 finalize 落库；异常路径 finalize 不会执行，若不在此补写，
        "真崩掉"的问数在 trace / audit 里就完全没有记录。

        Args:
            trace: 本次问数的追踪上下文（已记好各节点 span）
            state: 本次问数的初始状态
            exc: 导致中断的异常
        """
        try:
            # 已落库（finalize 成功执行过）则不重复写，靠 persisted 标记去重
            if needs_fallback_persist(trace):
                await self.trace_repository.insert(trace, STATUS_ERROR)
            # 审计同样补一条：显式传 error，避免被自动归因成 ok
            await self.audit_log_repository.insert(
                {**state, "error": f"{type(exc).__name__}: {exc}"},
                None,
                status=STATUS_ERROR,
            )
        except Exception as inner:  # noqa: BLE001 - 兜底留痕是最后一道防线，绝不能再抛
            logger.error(f"异常兜底留痕失败：{inner}")
