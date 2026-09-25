"""安全工具：密码哈希、JWT 签发与校验。

bcrypt 5.x 与 passlib 存在兼容问题（passlib 无法正确识别 bcrypt 5.x），
故直接调用 bcrypt 库，并截断超长密码（bcrypt 上限 72 字节）。
"""

from __future__ import annotations

import bcrypt as _bcrypt
from datetime import datetime, timedelta
from typing import Optional

import jwt

from config.settings import settings

ALGO = settings.JWT_ALGORITHM
_MAX_BYTES = 72  # bcrypt 密码字节上限


def _to_bytes(password: str) -> bytes:
    """转为字节并截断到 72 字节，避免 bcrypt 5.x 报错。"""
    b = password.encode("utf-8")
    return b[:_MAX_BYTES]


def hash_password(password: str) -> str:
    """生成 bcrypt 密码哈希（含 salt，12 rounds）。"""
    return _bcrypt.hashpw(_to_bytes(password), _bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """校验密码与哈希。"""
    if not password or not hashed:
        return False
    try:
        return _bcrypt.checkpw(_to_bytes(password), hashed.encode("utf-8"))
    except Exception:
        return False


def create_token(user_id: int, phone: Optional[str] = None,
                 email: Optional[str] = None, role: str = "user") -> str:
    """签发 JWT，过期时间由配置控制。"""
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "phone": phone,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGO)


def decode_token(token: str) -> Optional[dict]:
    """解码并校验 JWT，失败返回 None。"""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    except Exception:
        return None
