"""
FastAPI 依赖组装

集中声明 API 层需要的依赖函数，把 Session、Repository、Client 和 Service
按职责组装起来。路由层只通过 Depends 声明自己需要什么对象，具体创建细节
都收敛在这里，避免 HTTP 处理函数直接感知底层基础设施。
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.conf.app_config import app_config
from app.core.security import Identity, TokenError, decode_access_token
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.audit_log_repository import AuditLogRepository
from app.repositories.mysql.meta.history_repository import HistoryRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.mysql.meta.trace_repository import TraceRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.query_service import QueryService
from app.services.session_service import SessionService

# auto_error=False：由本模块自己决定 401 的响应体与响应头，
# 而不是让 FastAPI 返回它的默认文案（默认文案不带头部，且格式和项目其余错误不一致）。
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    """统一的 401 响应

    带 ``WWW-Authenticate: Bearer`` 是 HTTP 规范要求，客户端据此知道
    "换个令牌再来"，而不是以为接口坏了。
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_identity(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> Identity:
    """从 ``Authorization: Bearer <token>`` 解析调用方身份

    **fail-closed**：缺少令牌、格式不对、签名不符、已过期，任何一步不通过都直接 401，
    绝不回落到某个默认角色继续执行——"拿不到身份就放行"正是这次要修掉的问题。

    角色从令牌 claim 取出，客户端无法篡改：改了内容签名就对不上。
    """
    # HTTPBearer 已经保证凭证以 Bearer 开头；没带或格式不对时这里拿到的是 None
    if credentials is None or not credentials.credentials:
        raise _unauthorized("缺少访问令牌")

    try:
        return decode_access_token(credentials.credentials)
    except TokenError as exc:
        # TokenError 的文案本身就不区分"过期/签名错/格式错"，这里原样透出即可
        raise _unauthorized(str(exc)) from exc


async def get_meta_session():
    """创建一次请求内使用的元数据库 Session"""

    # yield 之后的清理逻辑由 async with 负责，FastAPI 会在请求结束后继续执行退出流程
    async with meta_mysql_client_manager.session_factory() as meta_session:
        yield meta_session


async def get_meta_mysql_repository(
    session: Annotated[AsyncSession, Depends(get_meta_session)],
) -> MetaMySQLRepository:
    """基于请求级 Session 创建元数据仓储"""

    return MetaMySQLRepository(session)


async def get_audit_log_repository(
    session: Annotated[AsyncSession, Depends(get_meta_session)],
) -> AuditLogRepository:
    """基于请求级 Meta Session 创建审计仓储（与元数据仓储共用同一连接）"""

    return AuditLogRepository(session)


async def get_trace_repository(
    session: Annotated[AsyncSession, Depends(get_meta_session)],
) -> TraceRepository:
    """基于请求级 Meta Session 创建链路追踪仓储（旁路能力，写入 fail-open）"""

    return TraceRepository(session)


async def get_embedding_client() -> HuggingFaceEndpointEmbeddings:
    """获取应用启动阶段初始化好的 Embedding 客户端"""

    return embedding_client_manager.client


async def get_dw_session():
    """创建一次请求内使用的数仓 Session"""

    async with dw_mysql_client_manager.session_factory() as dw_session:
        yield dw_session


async def get_dw_mysql_repository(
    session: Annotated[AsyncSession, Depends(get_dw_session)],
) -> DWMySQLRepository:
    """基于请求级 Session 创建数仓仓储"""

    return DWMySQLRepository(session)


async def get_column_qdrant_repository() -> ColumnQdrantRepository:
    """创建字段向量检索仓储"""

    return ColumnQdrantRepository(qdrant_client_manager.client)


async def get_metric_qdrant_repository() -> MetricQdrantRepository:
    """创建指标向量检索仓储"""

    return MetricQdrantRepository(qdrant_client_manager.client)


async def get_value_es_repository() -> ValueESRepository:
    """创建字段取值全文检索仓储"""

    return ValueESRepository(es_client_manager.client)


def get_session_service() -> SessionService:
    """History uses its own short transactions, never the audit/query request session."""
    return SessionService(HistoryRepository(meta_mysql_client_manager.session_factory, app_config.query_run_timeout_seconds))


async def get_query_service(
    meta_mysql_repository: Annotated[
        MetaMySQLRepository, Depends(get_meta_mysql_repository)
    ],
    embedding_client: Annotated[
        HuggingFaceEndpointEmbeddings, Depends(get_embedding_client)
    ],
    dw_mysql_repository: Annotated[DWMySQLRepository, Depends(get_dw_mysql_repository)],
    column_qdrant_repository: Annotated[
        ColumnQdrantRepository, Depends(get_column_qdrant_repository)
    ],
    metric_qdrant_repository: Annotated[
        MetricQdrantRepository, Depends(get_metric_qdrant_repository)
    ],
    value_es_repository: Annotated[ValueESRepository, Depends(get_value_es_repository)],
    audit_log_repository: Annotated[
        AuditLogRepository, Depends(get_audit_log_repository)
    ],
    trace_repository: Annotated[TraceRepository, Depends(get_trace_repository)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
) -> QueryService:
    """组装一次查询所需的业务服务"""

    # QueryService 只接收已经创建好的依赖对象，本身不关心这些对象来自 MySQL、Qdrant 还是 ES
    return QueryService(
        meta_mysql_repository=meta_mysql_repository,
        embedding_client=embedding_client,
        dw_mysql_repository=dw_mysql_repository,
        column_qdrant_repository=column_qdrant_repository,
        metric_qdrant_repository=metric_qdrant_repository,
        value_es_repository=value_es_repository,
        audit_log_repository=audit_log_repository,
        trace_repository=trace_repository,
        history_repository=session_service.history,
    )
