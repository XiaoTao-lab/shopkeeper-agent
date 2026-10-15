"""
鉴权接口路由

两个端点：
- ``POST /api/auth/token``：用账号 + 口令换取访问令牌（**演示用签发点**）
- ``GET  /api/auth/me``：查看当前令牌对应的身份，供前端展示与联调自检

设计要点：**角色不由请求指定**。客户端提交的是账号，服务端按账号查出角色再写进令牌，
因此客户端无法直接索要一个 admin 令牌——除非拿到 admin 的账号。

生产替换点：把 ``/token`` 换成对接公司 SSO / LDAP，或改查用户表并校验密码哈希
（argon2 / bcrypt）。下游解析身份的 ``get_current_identity`` 一行都不用改。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_current_identity
from app.api.schemas.auth_schema import IdentityResponse, TokenRequest, TokenResponse
from app.conf.app_config import app_config
from app.core.log import logger
from app.core.security import Identity, create_access_token, verify_dev_password

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/token", response_model=TokenResponse)
async def issue_token(payload: TokenRequest) -> TokenResponse:
    """用账号 + 口令换取访问令牌"""

    auth = app_config.auth

    # 演示签发点必须显式开启；关闭时返回 404 而不是 403，
    # 避免对外暴露"这里存在一个登录入口"这一事实。
    if not auth.dev_login_enable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="登录接口未开放",
        )

    # 口令与账号分别校验，但对外只回**同一句**错误文案：
    # 若"账号不存在"和"口令错误"返回不同提示，等于免费帮攻击者枚举有效账号。
    password_ok = verify_dev_password(payload.password)
    role = auth.resolve_role_of(payload.username)
    if not password_ok or role is None:
        logger.warning(f"登录失败：username={payload.username!r}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或口令不正确",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(payload.username, role)
    logger.info(f"签发访问令牌：username={payload.username!r} role={role!r}")
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        role=role,
        username=payload.username,
    )


@auth_router.get("/me", response_model=IdentityResponse)
async def read_me(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> IdentityResponse:
    """返回当前令牌对应的身份（前端可用它验证令牌是否仍然有效）"""

    return IdentityResponse(username=identity.subject, role=identity.role)
