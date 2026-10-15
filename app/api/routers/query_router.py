"""
问数查询接口路由

负责定义前端访问的 `/api/query` 接口，把 HTTP 请求交给 QueryService，
并把问数智能体执行过程以 SSE 形式持续返回给客户端。
路由层只处理请求体、依赖声明和响应类型，不直接创建 Repository 或执行图节点。

身份来源：**从令牌解析**，不再接受客户端自报请求头。
此前这里读的是 `X-User-Role`，等于谁都能自己写一句"我是管理员"就拿到全量数据；
现在角色由 `get_current_identity` 从签名令牌里解出，客户端改不动。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.api.dependencies import (
    get_current_identity,
    get_query_service,
    get_session_service,
)
from app.api.schemas.query_schema import QuerySchema
from app.core.security import Identity
from app.repositories.mysql.meta.history_repository import HistoryError
from app.services.query_service import QueryService
from app.services.session_service import SessionService

# 当前模块只维护查询相关接口，避免后续所有 API 都挤在 main.py 中
query_router = APIRouter()


@query_router.post("/api/query")
async def query_handler(
    request: Request,
    query: QuerySchema,
    identity: Annotated[Identity, Depends(get_current_identity)],
    query_service: Annotated[QueryService, Depends(get_query_service)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
):
    """接收用户自然语言问题，并流式返回 LangGraph 工作流输出"""

    # 会话 ID：前端未提供时后端创建新会话
    session_id = query.thread_id
    if not session_id:
        created = await session_service.create(identity)
        session_id = created["id"]

    # 请求键：前端未提供时生成，用于防重复提交
    client_request_id = query.client_request_id or str(uuid.uuid4())

    # 预登记：校验归属/权限、防重复、设置期限，必须在 SSE 响应开始前完成
    try:
        prepared = await session_service.prepare(identity, session_id, client_request_id, query.query)
    except HistoryError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, **exc.details})

    # 如果有已保存的终态（重复请求），直接回放
    if prepared.replay:
        async def replay_stream():
            yield f"data: {__import__('json').dumps({'type': 'meta', 'thread_id': session_id, 'run_id': prepared.run_id}, ensure_ascii=False)}\n\n"
            yield f"data: {__import__('json').dumps(prepared.replay.get('terminal_event', {'type': 'error', 'message': '已保存的结果'}), ensure_ascii=False)}\n\n"
            yield f"data: {__import__('json').dumps({'type': 'done', 'run_id': prepared.run_id, 'status': prepared.replay.get('status', 'completed'), 'outcome': prepared.replay.get('outcome', 'result')}, ensure_ascii=False)}\n\n"
        return StreamingResponse(replay_stream(), media_type="text/event-stream")

    # 正常执行：传入 prepared 和完整 identity
    return StreamingResponse(
        query_service.query(prepared=prepared, identity=identity),
        media_type="text/event-stream",
    )
