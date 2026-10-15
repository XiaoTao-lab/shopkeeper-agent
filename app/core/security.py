"""
JWT 令牌签发与校验

职责边界：本模块是**纯函数**，不依赖 FastAPI、不碰数据库，只做两件事——
把身份签成令牌、把令牌验回身份。HTTP 层从哪里取令牌、失败怎么响应，
由 app/api/dependencies.py 决定，两边互不污染。

为什么不再用请求头传角色
------------------------
之前客户端直接发 ``X-User-Role: admin``，服务端照单全收——相当于进门前
自己喊一声"我是行长"。令牌把这件事反过来：角色由服务端查表后**写进令牌并签名**，
客户端仍然能拿着它到处走，但改一个字签名就对不上，过期了也自动失效。

安全要点（每条都对应一个真实攻击面）
------------------------------------
1. **固定算法**：校验时传 algorithms 白名单，**绝不读取令牌头里的 alg**。
   否则攻击者可声明 ``alg=none``（无签名）或做 HS/RS 混淆来绕过验签。
2. **强制必须有 exp**：不给"永不过期令牌"留口子。
3. **校验签发者 iss**：别的系统签的令牌不能拿来用。
4. **角色回落校验**：令牌里的角色必须仍存在于当前 RBAC 配置，
   避免配置里删掉某角色后，旧令牌还能继续通行。
5. **对外不泄露失败细节**：统一"令牌无效或已过期"，不区分过期/签名错/格式错，
   不给攻击者任何反馈信号；真实原因只写进服务端日志。
"""

import hmac
import time
from dataclasses import dataclass

import jwt

from app.conf.app_config import app_config
from app.core.log import logger

# 服务端唯一允许的签名算法。刻意写成常量而不是从 yaml 取值：
# 想放开新算法就必须改代码，而不是改一行配置就能悄悄放开。
_ALLOWED_ALGORITHMS = ("HS256",)

# 签名密钥最短长度（字符）。太短会被离线暴力破解，等于没签名。
_MIN_SECRET_LENGTH = 32

# 签发方与校验方的机器时间可能有微小差异，留几秒容差避免"刚签发就过期"
_LEEWAY_SECONDS = 5


class TokenError(Exception):
    """令牌签发或校验失败。对外统一映射为 401，不区分具体原因。"""


@dataclass(frozen=True)
class Identity:
    """从令牌中解析出的调用方身份

    ``subject`` 是账号标识（用于审计"谁问的"），``role`` 决定行级权限与列脱敏范围。
    两者都来自令牌 claim，客户端无法篡改。
    """

    subject: str
    role: str


def validate_auth_config() -> None:
    """启动期自检鉴权配置，不合格直接拒绝启动（fail-fast）。

    刻意放在启动期而不是首次请求期：密钥没配这种问题应该在服务起来的那一刻
    就暴露，而不是等线上第一个用户请求才回 401。
    """
    auth = app_config.auth

    secret = auth.secret_key or ""
    if len(secret) < _MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET 缺失或过短（当前 {len(secret)} 字符，要求 >= {_MIN_SECRET_LENGTH}）。"
            "请在 .env 中配置一个足够随机的密钥，例如："
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )

    if auth.algorithm not in _ALLOWED_ALGORITHMS:
        raise RuntimeError(
            f"auth.algorithm={auth.algorithm!r} 不在允许列表 {_ALLOWED_ALGORITHMS} 内。"
            "算法必须由服务端硬性约束，不能通过配置随意放开。"
        )

    if auth.dev_login_enable:
        # 演示签发点本身就是一个后门，必须让它在日志里足够显眼
        logger.warning(
            "演示登录已开启（auth.dev_login_enable=true）：任何人凭共享口令即可换取令牌。"
            "生产环境必须置为 false，并改接真实登录（SSO / 用户表 + 密码哈希）。"
        )


def verify_dev_password(candidate: str) -> bool:
    """恒定时间比对演示口令。

    用 ``hmac.compare_digest`` 而不是 ``==``：普通字符串比较会在第一个不同字符处
    提前返回，攻击者能通过响应耗时逐字节猜出口令。
    """
    expected = app_config.auth.dev_login_password or ""
    if not expected:
        # 未配置口令时一律拒绝，避免"空口令 = 放行"
        return False
    return hmac.compare_digest(candidate, expected)


def create_access_token(
    subject: str, role: str, *, ttl_minutes: int | None = None
) -> tuple[str, int]:
    """把身份签成访问令牌。

    Args:
        subject: 账号标识（写进 ``sub``，用于审计）
        role: 角色（写进自定义 claim ``role``，由服务端查表得出）
        ttl_minutes: 有效期；缺省取 ``auth.expire_minutes``

    Returns:
        ``(token, expires_in_seconds)``；有效期以秒返回，方便接口原样下发前端
    """
    auth = app_config.auth
    now = int(time.time())
    ttl_seconds = (ttl_minutes if ttl_minutes is not None else auth.expire_minutes) * 60

    payload = {
        "sub": subject,
        "role": role,
        "iss": auth.issuer,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    # 签名算法取自服务端配置（已在上面的自检里限定为白名单内）
    token = jwt.encode(payload, auth.secret_key, algorithm=auth.algorithm)
    return token, ttl_seconds


def decode_access_token(token: str) -> Identity:
    """校验令牌并解析出身份。

    Args:
        token: 裸令牌（不含 ``Bearer `` 前缀）

    Returns:
        解析并校验通过的 :class:`Identity`

    Raises:
        TokenError: 签名不符、已过期、签发者不对、字段缺失或角色已失效
    """
    auth = app_config.auth

    try:
        claims = jwt.decode(
            token,
            auth.secret_key,
            # 只信任服务端指定的算法，令牌头里的 alg 完全不参与决策
            algorithms=list(_ALLOWED_ALGORITHMS),
            issuer=auth.issuer,
            options={
                # 这五个字段缺任何一个都视为非法令牌
                "require": ["exp", "iat", "sub", "role", "iss"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_iat": True,
            },
            leeway=_LEEWAY_SECONDS,
        )
    except jwt.InvalidTokenError as exc:
        # 对外不解释原因；服务端日志里留全量信息，必要时能定位到是哪类失败
        logger.warning(f"令牌校验失败：{type(exc).__name__}: {exc}")
        raise TokenError("令牌无效或已过期") from exc

    subject = str(claims.get("sub") or "")
    role = str(claims.get("role") or "")

    # 角色必须在当前 RBAC 配置里仍然存在：配置删掉某角色后，
    # 之前签发的旧令牌不应还能带着这个已废弃的角色通行
    if role not in app_config.rbac.roles:
        logger.warning(f"令牌角色已失效：role={role!r} subject={subject!r}")
        raise TokenError("令牌无效或已过期")

    return Identity(subject=subject, role=role)
