"""
问数接口请求体定义

集中声明 API 层输入输出的数据结构，让路由函数只处理业务流程，
字段校验和 OpenAPI 文档生成交给 Pydantic 与 FastAPI 完成。
"""

from pydantic import BaseModel, Field


class QuerySchema(BaseModel):
    """`/api/query` 请求体，承载用户输入的自然语言问题"""

    # 前端请求体中的 query 字段，例如 {"query": "统计华北地区销售额"}
    query: str = Field(min_length=1, max_length=2000)
    # 多轮会话 id：同一会话的多次提问传同一个 thread_id，后端据此续接对话历史；
    # 不传时后端生成一个并随 meta 事件下发，前端可自行保存后复用
    thread_id: str | None = None
    # 前端为一次提交生成的 UUID，用于防双击/网络重复提交；同一逻辑提交的所有重发必须复用
    client_request_id: str | None = None
