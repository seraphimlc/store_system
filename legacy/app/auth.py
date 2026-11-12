# -*- coding: utf-8 -*-
"""会话与密码：argon2 哈希、签名 cookie（itsdangerous，含过期）、CSRF token。

cookie 'ss' = URLSafeTimedSerializer 载荷 {"uid": int, "csrf": str}。
POST 表单必须带与载荷一致的 csrf（登录页除外，见 auth 路由）。
"""
import secrets
from typing import Optional

from argon2 import PasswordHasher
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE = "ss"
CSRF_FIELD = "csrf_token"

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, pw_hash: str) -> bool:
    try:
        return _ph.verify(pw_hash, pw)
    except Exception:
        return False


def _serializer():
    return URLSafeTimedSerializer(get_settings().secret_key, salt="session")


def new_session_token(uid: int, csrf: Optional[str] = None) -> str:
    csrf = csrf or secrets.token_hex(16)
    return _serializer().dumps({"uid": uid, "csrf": csrf})


def read_session_token(token: Optional[str]):
    """-> {"uid","csrf"} 或 None（无效/过期）。"""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=3600 * get_settings().session_ttl_hours)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "uid" not in data:
        return None
    return data
