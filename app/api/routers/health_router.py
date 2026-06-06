"""
健康检查接口

提供一个简单的探活端点，方便外部监控系统（Docker Healthcheck / K8s LivenessProbe）
确认服务是否正常运行。
"""

from fastapi import APIRouter

health_router = APIRouter()


@health_router.get("/health")
async def health_check():
    """返回服务状态，供监控系统探活"""
    return {"status": "ok"}
