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

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.context import request_id_ctx_var
from app.core.log import logger
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
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.mysql.meta.trace_repository import TraceRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


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

    async def query(
        self, query: str, thread_id: str | None = None, user_role: str | None = None
    ):
        """执行一次问数工作流，并逐段产出 SSE 消息。

        Args:
            query: 用户本轮自然语言问题
            thread_id: 多轮会话 id；同一会话各轮传同一个值以共享对话历史。
                传入 None 时后端生成一个，并通过首条 meta 事件下发给前端复用。
            user_role: 调用方角色（来自请求头 X-User-Role）；未知或缺失回落到默认角色。
        """

        # 会话 id：前端未提供时后端生成，保证每次调用都有一个可续接的 thread_id
        session_id = thread_id or str(uuid.uuid4())
        # 挂了 checkpointer 后，LangGraph 依据 config.configurable.thread_id 读写对应会话状态
        config = {"configurable": {"thread_id": session_id}}

        # 角色先归一化：未知/缺失 → default_role，避免"给不出角色"被当成全量放行
        effective_role = app_config.rbac.resolve_name(user_role)

        # State 只放会被图节点读写和合并的业务数据，外部工具对象不塞进 State
        state = DataAgentState(
            query=query, user_role=effective_role, thread_id=session_id
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

        # 开启本次问数的追踪上下文：节点埋点（traced）与 token 回调（TraceUsageCallback）
        # 都通过这个 contextvar 找到"当前这次问数"，从而把散落的 span 归到同一条 trace 上。
        # 追踪是可关可采样的旁路能力，未采样时所有埋点直接空转。
        trace = new_trace(
            query=query,
            thread_id=session_id,
            request_id=request_id_ctx_var.get(),
            role=effective_role,
        )
        trace_token = set_trace(trace)

        try:
            # 先回一条 meta，把本次会话 id 同步给前端（前端未传时据此续接同一会话）
            yield (
                "data: "
                + json.dumps(
                    {"type": "meta", "thread_id": session_id}, ensure_ascii=False
                )
                + "\n\n"
            )

            # stream_mode="custom" 对应节点内部 writer(...) 写出的进度消息
            async for chunk in graph.astream(
                input=state, context=context, config=config, stream_mode="custom"
            ):
                # SSE 要求每条消息以 data: 开头，并以两个换行符结束
                # ensure_ascii=False 保留中文进度文案，default=str 兜底处理日期等非 JSON 类型

                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            # 图执行中途抛异常 → LangGraph 直接中断，收尾节点 finalize 不会执行。
            # 这里兜底补写 trace / 审计，否则"真崩掉"的问数在库里完全没有记录，
            # 观测里只剩下"被拦截"的痕迹，看不出系统出没出故障。
            await self._persist_failure(trace, state, e)

            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
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
