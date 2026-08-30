"""登录认证：本地账号 + HMAC 签名 token。

- 账号存 ``data/users.json``（用户名 → {salt, hash, role}，PBKDF2-SHA256），
  文件不存在时自动播种默认账号（见 ``DEFAULT_USERS``）
- 签名密钥存 ``data/auth_secret.key``（首次随机生成，跨重启保持 token 有效）
- token 为 ``base64url(payload).base64url(hmac)``，payload 含 user/role/exp；
  无状态校验，不落库

默认账号（家庭本地工具，首次启动播种，可删 users.json 重置后改密码）：
- malin / 123456（学生：只能背单词 + 提交成绩）
- teacher / 123456（教师：全部功能）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

DEFAULT_USERS = {
    "malin": ("123456", "student"),
    "teacher": ("123456", "teacher"),
}
TOKEN_TTL = 30 * 24 * 3600  # 30 天

_DATA_DIRS = (
    Path(__file__).resolve().parent.parent / "data",
    Path(__file__).resolve().parent / "data",
)


def _default_dir() -> Path:
    for d in _DATA_DIRS:
        if d.exists():
            return d
    return _DATA_DIRS[0]


def _pbkdf2(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 60_000).hex()


class UserStore:
    """users.json 的读写与校验。"""

    def __init__(self, path: Path | None = None):
        path_env = os.environ.get("GRAMMAR_KB_USERS")
        self.path = Path(path_env) if path_env else (path or _default_dir() / "users.json")
        self._users: dict | None = None

    def _load(self) -> dict:
        if self._users is not None:
            return self._users
        if self.path.exists():
            try:
                self._users = json.loads(self.path.read_text(encoding="utf-8"))
                return self._users
            except Exception:
                pass  # 损坏则重建（重播种默认账号）
        users = {}
        for name, (pwd, role) in DEFAULT_USERS.items():
            salt = secrets.token_hex(8)
            users[name] = {"salt": salt, "hash": _pbkdf2(pwd, salt), "role": role}
        self._users = users
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass  # 只读环境也能跑（每次进程内重建）
        return users

    def verify(self, user: str, password: str) -> str | None:
        """校验成功返回角色，失败返回 None。"""
        rec = self._load().get(user)
        if not rec:
            return None
        if hmac.compare_digest(_pbkdf2(password, rec["salt"]), rec["hash"]):
            return rec["role"]
        return None


def _secret_path() -> Path:
    env = os.environ.get("GRAMMAR_KB_AUTH_SECRET")
    return Path(env) if env else _default_dir() / "auth_secret.key"


def _load_secret() -> bytes:
    p = _secret_path()
    if p.exists():
        return p.read_bytes()
    s = secrets.token_bytes(32)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(s)
    except Exception:
        pass
    return s


def make_token(user: str, role: str, ttl: int = TOKEN_TTL) -> str:
    payload = json.dumps(
        {"user": user, "role": role, "exp": int(time.time()) + ttl}, separators=(",", ":")
    ).encode()
    body = base64.urlsafe_b64encode(payload)
    sig = hmac.new(_load_secret(), body, hashlib.sha256).digest()
    return f"{body.decode()}.{base64.urlsafe_b64encode(sig).decode()}"


def read_token(token: str) -> dict | None:
    """校验签名与过期时间；有效返回 payload，无效返回 None。"""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = body_b64.encode()
        expect = hmac.new(_load_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(base64.urlsafe_b64decode(sig_b64), expect):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
