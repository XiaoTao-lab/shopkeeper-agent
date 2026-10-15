"""
FastAPI 应用入口

负责创建后端应用实例，注册应用生命周期函数，并把各业务模块中的 router
挂载到同一个 app 上。HTTP 请求会先进入这里创建的 app，再按路由分发到
具体的接口处理函数。
"""

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.lifespan import lifespan
from app.api.routers.auth_router import auth_router
from app.api.routers.health_router import health_router
from app.api.routers.query_router import query_router
from app.api.routers.session_router import router as session_router
from app.core.context import request_id_ctx_var
from app.repositories.mysql.meta.history_repository import HistoryError

# uv run fastapi dev main.py后端
#

# lifespan 交给 FastAPI 管理，用于在服务启动和关闭时统一初始化与释放外部客户端
app = FastAPI(lifespan=lifespan)


@app.exception_handler(HistoryError)
async def history_error_handler(request: Request, exc: HistoryError):
    """把 HistoryError 转成带结构化 detail 的 HTTP 响应"""
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, **exc.details}},
    )

# 把各业务路由注册进应用；没有挂载时，/docs 和真实 HTTP 请求都访问不到该接口
app.include_router(auth_router)
app.include_router(query_router)
app.include_router(session_router)
app.include_router(health_router)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    # 请求被处理之前
    request_id = uuid.uuid4()
    request_id_ctx_var.set(request_id)
    response = await call_next(request)
    # 请求被处理之后
    return response


#观测命令下钻单次（这一次卡在哪一步）	uv run python -m app.scripts.show_trace	    每个节点的耗时 / 状态 / 占比 / 报错，以及本次总耗时、LLM 次数、token
# 列出最近	                    uv run python -m app.scripts.show_trace --last 20	        最近 N 次问数的状态、总耗时、节点数、LLM 次数
# 横向聚合（哪一步系统性最慢）	    uv run python -m app.scripts.report_obs --days 7	        各节点 P50 / P95 / 耗时占比排行、token 成本、失败归因
# 原始数据	                    MySQL trace / trace_span 两表	                            想自己 SQL 分析的底表
