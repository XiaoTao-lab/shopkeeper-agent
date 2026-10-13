"""
问数查询接口路由

负责定义前端访问的 `/api/query` 接口，把 HTTP 请求交给 QueryService，
并把问数智能体执行过程以 SSE 形式持续返回给客户端。
路由层只处理请求体、依赖声明和响应类型，不直接创建 Repository 或执行图节点。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from starlette.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.schemas.query_schema import QuerySchema
from app.services.query_service import QueryService

# 当前模块只维护查询相关接口，避免后续所有 API 都挤在 main.py 中
query_router = APIRouter()


@query_router.post("/api/query")
async def query_handler(
    # 请求体参数：FastAPI 会把前端 JSON 自动解析成 QuerySchema
    query: QuerySchema,
    # 服务依赖：FastAPI 会调用 get_query_service，递归组装它所需的仓储和客户端
    query_service: Annotated[QueryService, Depends(get_query_service)],
    # 身份入口：演示用请求头携带角色，后端据此做行级权限与脱敏。
    # 生产替换点：把这里换成从 JWT 解出角色即可，下游零改动。
    x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
):
    """接收用户自然语言问题，并流式返回 LangGraph 工作流输出"""

    return StreamingResponse(
        # query.query 是用户问题字符串；query.thread_id 用于多轮会话续接；
        # x_user_role 决定本轮的行级权限与列脱敏范围
        query_service.query(
            query.query, query.thread_id, x_user_role
        ),  # .query是QueryService类里的方法，第二个query是方法（QuerySchema）的变量，第三个是QuerySchema里的参数
        media_type="text/event-stream",
    )
