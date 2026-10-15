"""
鉴权接口请求体与响应体定义

与 query_schema 一致：这里只声明数据结构，字段校验交给 Pydantic，
不掺杂任何业务逻辑。
"""

from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    """换取令牌的请求体

    注意这里**只有账号和口令，没有角色字段**——角色由服务端按账号查表得出。
    客户端能声明"我是谁"，但不能声明"我是什么权限"，这正是本次改造的关键。
    """

    username: str = Field(min_length=1, max_length=64, description="账号")
    password: str = Field(min_length=1, max_length=128, description="口令")


class TokenResponse(BaseModel):
    """令牌下发结果

    ``expires_in`` 是剩余有效秒数，前端可据此提前换新令牌，避免请求打到一半过期。
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    username: str


class IdentityResponse(BaseModel):
    """当前令牌对应的身份，供前端展示与联调自检"""

    username: str
    role: str
