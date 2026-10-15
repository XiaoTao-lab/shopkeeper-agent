"""JWT 鉴权模块的单元测试。

覆盖范围刻意对着**攻击面**而不是覆盖率：
除了正常的签发/校验往返，重点验证四类绕过手法是否都被挡住——
篡改载荷、伪造签名算法（alg=none）、过期令牌、以及带着已失效角色的旧令牌。
"""

import base64
import json
import time

import jwt
import pytest

from app.conf.app_config import app_config
from app.core.security import (
    Identity,
    TokenError,
    create_access_token,
    decode_access_token,
    validate_auth_config,
    verify_dev_password,
)


def _b64url(obj: dict) -> str:
    """按 JWT 规范做 base64url 编码（去掉尾部 padding）"""
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unsigned_token(payload: dict) -> str:
    """手工构造 ``alg=none`` 的无签名令牌，模拟算法混淆攻击。

    刻意不用 ``jwt.encode(..., algorithm="none")``：不同版本对它的支持不一致，
    而攻击者本身就是手工拼这种令牌的，手工拼出来测才最贴近真实攻击。
    """
    return f"{_b64url({'alg': 'none', 'typ': 'JWT'})}.{_b64url(payload)}."


def test_validate_auth_config_passes_with_current_config() -> None:
    """当前配置本身应当通过启动自检"""
    validate_auth_config()


def test_create_and_decode_roundtrip() -> None:
    """签发的令牌能验回同一身份"""
    token, expires_in = create_access_token("boss", "admin")

    assert expires_in == app_config.auth.expire_minutes * 60
    assert decode_access_token(token) == Identity(subject="boss", role="admin")


def test_identity_is_immutable() -> None:
    """身份对象不可变，避免被下游无意改写"""
    identity = Identity(subject="a", role="b")
    with pytest.raises(Exception):
        identity.role = "admin"  # type: ignore[misc]


def test_rejects_tampered_signature() -> None:
    """改动令牌任意一位，签名即失配"""
    token, _ = create_access_token("boss", "admin")
    head, payload, signature = token.split(".")

    # 只翻签名最后一个字符：载荷完好、算法合法，唯一变化就是签名对不上
    flipped = "A" if signature[-1] != "A" else "B"
    tampered = f"{head}.{payload}.{signature[:-1]}{flipped}"

    with pytest.raises(TokenError):
        decode_access_token(tampered)


def test_rejects_role_escalation_payload() -> None:
    """把 role 从 guest 改成 admin 但不重新签名 → 必须拒绝（这是本改动的核心价值）"""
    token, _ = create_access_token("guest", "guest")
    head, _, _ = token.split(".")

    payload = {
        "sub": "guest",
        "role": "admin",  # 攻击者想要的越权结果
        "iss": app_config.auth.issuer,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    forged = f"{head}.{_b64url(payload)}.{token.split('.')[2]}"

    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_rejects_alg_none_unsigned_token() -> None:
    """alg=none 无签名令牌必须被拒绝（防算法混淆）"""
    payload = {
        "sub": "attacker",
        "role": "admin",
        "iss": app_config.auth.issuer,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    with pytest.raises(TokenError):
        decode_access_token(_unsigned_token(payload))


def test_rejects_expired_token() -> None:
    """过期令牌必须被拒绝（ttl 取负数即可造一张已过期的令牌）"""
    token, _ = create_access_token("boss", "admin", ttl_minutes=-1)
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_rejects_wrong_issuer() -> None:
    """别的系统签发的令牌不能拿来用"""
    payload = {
        "sub": "boss",
        "role": "admin",
        "iss": "some-other-system",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    foreign = jwt.encode(payload, app_config.auth.secret_key, algorithm="HS256")

    with pytest.raises(TokenError):
        decode_access_token(foreign)


def test_rejects_token_without_exp() -> None:
    """缺少 exp 的"永不过期"令牌一律非法"""
    payload = {
        "sub": "boss",
        "role": "admin",
        "iss": app_config.auth.issuer,
        "iat": int(time.time()),
    }
    never_expires = jwt.encode(
        payload, app_config.auth.secret_key, algorithm="HS256"
    )

    with pytest.raises(TokenError):
        decode_access_token(never_expires)


def test_rejects_role_absent_from_rbac_config() -> None:
    """令牌里的角色必须是当前 RBAC 里存在的角色。

    即便签名合法（服务端自己签的），角色也不能凭空出现——
    否则一旦配置里删掉某角色，此前签发的旧令牌仍能继续带着它通行。
    """
    assert "superuser" not in app_config.rbac.roles

    payload = {
        "sub": "boss",
        "role": "superuser",
        "iss": app_config.auth.issuer,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, app_config.auth.secret_key, algorithm="HS256")

    with pytest.raises(TokenError):
        decode_access_token(token)


def test_rejects_garbage_token() -> None:
    """完全不是令牌的字符串不应抛底层异常，而是统一 TokenError"""
    with pytest.raises(TokenError):
        decode_access_token("this-is-not-a-jwt")


def test_verify_dev_password() -> None:
    """演示口令比对：正确通过，错误拒绝"""
    expected = app_config.auth.dev_login_password
    assert expected, "测试前置条件：.env 中应配置 DEV_LOGIN_PASSWORD"

    assert verify_dev_password(expected) is True
    assert verify_dev_password(expected + "x") is False
    assert verify_dev_password("") is False
