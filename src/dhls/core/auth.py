"""【可复用核心】本地账号、JWT、可撤销登录会话和接口级角色/CSRF校验。

浏览器只持有HttpOnly认证Cookie，不把JWT放进localStorage。角色、启用状态和
部门/场馆始终从数据库读取；签名令牌中的旧权限不能覆盖数据库的新权限。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import sqlite3
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path

import jwt
from fastapi import Depends, Request
from pwdlib import PasswordHash

from dhls.core.chat import ChatError, Settings
from dhls.core.store import Store

COOKIE_NAME = "auth_token"
JWT_ISSUER, JWT_AUDIENCE = "venue-chat", "venue-chat-web"
PASSWORD_HASH = PasswordHash.recommended()
# 不存在的账号也执行一次相同算法，避免通过哈希耗时直接区分账号是否存在。
DUMMY_HASH = PASSWORD_HASH.hash(secrets.token_urlsafe(32))
PUBLIC_FIELDS = ("id", "display_name", "role", "department_id", "venue_id")


def public_user(user: dict) -> dict:
    """只显式输出允许前端查看的字段，绝不序列化password_hash或登录令牌。"""
    return {field: user[field] for field in PUBLIC_FIELDS}


def clean_username(username: str) -> str:
    """账号统一小写，仅允许可见ASCII账号字符，避免同名大小写歧义。"""
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,63}", username):
        raise ChatError("INVALID_ACCOUNT", "账号须为3至64位字母、数字或_.@-，且以字母或数字开头。", 422)
    return username


def validate_password(password: str):
    # 支持长口令和中文，不强制复杂度组合；设置上限避免恶意超大输入消耗资源。
    if not 12 <= len(password) <= 128 or not password.strip():
        raise ChatError("INVALID_PASSWORD", "密码须为12至128个字符，请使用独立的长口令。", 422)


async def create_user(store: Store, username: str, password: str, display_name: str,
                      role: str, department_id: str, venue_id: str) -> dict:
    """CLI与测试共用的开户入口；没有默认账号/密码，不能由匿名HTTP请求调用。"""
    username = clean_username(username)
    validate_password(password)
    if role not in {"employee", "admin"}:
        raise ChatError("INVALID_ROLE", "角色只能是employee或admin。", 422)
    labels = (display_name.strip(), department_id.strip(), venue_id.strip())
    if any(not item or len(item) > 100 for item in labels):
        raise ChatError("INVALID_ACCOUNT", "姓名、部门和场馆须为1至100个字符。", 422)
    password_hash = await asyncio.to_thread(PASSWORD_HASH.hash, password)
    user = dict(zip(PUBLIC_FIELDS, (str(uuid.uuid4()), labels[0], role, labels[1], labels[2])))
    try:
        async with store.connect() as db:
            await db.execute("""INSERT INTO users
                (id,login_subject,display_name,department_id,venue_id,is_active,role,password_hash)
                VALUES (?,?,?,?,?,1,?,?)""", (user["id"], username, *labels, role, password_hash))
            await db.commit()
    except sqlite3.IntegrityError:
        raise ChatError("ACCOUNT_EXISTS", "账号已存在，请使用其他账号。", 409) from None
    return user


def load_signing_secret(settings: Settings) -> str:
    """本机首次启动生成独立随机签名密钥，持久化在忽略的运行目录，重启可验旧JWT。

    企业部署通过AUTH_JWT_SECRET注入密钥；这个文件不是大模型API密钥。POSIX设置
    0600；Windows继承运行目录ACL，部署时应限制该目录仅服务账号可读写。
    """
    if settings.jwt_secret is not None:
        return settings.jwt_secret
    path: Path = settings.data_dir / "jwt-signing.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # x模式防止意外覆盖已有密钥；实例锁保证正常启动只有一个服务写入。
        with path.open("x", encoding="ascii") as stream:
            path.chmod(0o600)
            stream.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    secret = path.read_text(encoding="ascii").strip()
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("本地JWT签名密钥无效，请恢复密钥或通过AUTH_JWT_SECRET配置。")
    return secret


def check_same_origin(request: Request):
    """Cookie认证仅接受同源写请求；不信任X-Forwarded-*来构造许可来源。"""
    expected = f"{request.url.scheme}://{request.url.netloc}"
    origin = request.headers.get("origin")
    if (origin is not None and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
        raise ChatError("CSRF_INVALID", "请求来源校验失败，请刷新当前站点后重试。", 403)


class AuthService:
    """【可复用核心】JWT签名＋SQLite登录记录；登出立刻撤销，过期后必须重新登录。"""
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.secret = load_signing_secret(settings)
        # 本机单进程原型的有界登录限流；不保存明文账号/IP。生产多实例须共享限流。
        self.attempts: OrderedDict[str, deque] = OrderedDict()
        self.password_slots = asyncio.Semaphore(2)

    def throttle(self, request: Request, username: str):
        host = request.client.host if request.client else "unknown"
        # 同时限制来源和账号，不能只更换其中之一绕过暴力尝试。
        for kind, value, maximum in (("host", host, 30), ("account", username, 8)):
            key = hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()
            attempts = self.attempts.setdefault(key, deque())
            self.attempts.move_to_end(key)
            stamp = time.monotonic()
            while attempts and attempts[0] < stamp - 60:
                attempts.popleft()
            if len(attempts) >= maximum:
                raise ChatError("LOGIN_RATE_LIMITED", "登录尝试过于频繁，请一分钟后再试。", 429, True)
            attempts.append(stamp)
        while len(self.attempts) > 4096:
            self.attempts.popitem(last=False)

    async def login(self, request: Request, username: str, password: str) -> tuple[str, dict]:
        check_same_origin(request)
        username = clean_username(username)
        self.throttle(request, username)
        async with self.store.connect() as db:
            row = await (await db.execute("SELECT * FROM users WHERE login_subject=?", (username,))).fetchone()
        user = dict(row) if row else None
        stored_hash = user.get("password_hash") if user else None
        async with self.password_slots:
            try:
                valid = await asyncio.to_thread(PASSWORD_HASH.verify, password, stored_hash or DUMMY_HASH)
            except Exception:
                valid = False
        if not valid or not stored_hash or not user["is_active"]:
            raise ChatError("LOGIN_FAILED", "账号或密码错误，或账号已停用。", 401)
        # 哈希计算在事务外；之后重读，防止停用/重置密码与登录并发时签发旧凭证。
        identifier, csrf, stamp = str(uuid.uuid4()), secrets.token_urlsafe(32), int(time.time())
        expires = stamp + self.settings.auth_ttl_minutes * 60
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            fresh = await (await db.execute("SELECT * FROM users WHERE id=?", (user["id"],))).fetchone()
            if not fresh or not fresh["is_active"] or fresh["password_hash"] != stored_hash:
                raise ChatError("LOGIN_FAILED", "账号或密码错误，或账号已停用。", 401)
            user = dict(fresh)
            await db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (stamp,))
            await db.execute("INSERT INTO auth_sessions VALUES (?,?,?,?,?,NULL)", (
                identifier, user["id"], hashlib.sha256(csrf.encode()).hexdigest(), stamp, expires))
            await db.commit()
        token = jwt.encode({"sub": user["id"], "jti": identifier, "iat": stamp, "nbf": stamp,
                            "exp": expires, "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "csrf": csrf},
                           self.secret, algorithm="HS256")
        return token, {**public_user(user), "csrf_token": csrf}

    async def authenticate(self, request: Request) -> dict:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            raise ChatError("UNAUTHORIZED", "请先登录后再操作。", 401)
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=JWT_ISSUER,
                                audience=JWT_AUDIENCE,
                                options={"require": ["sub", "jti", "iat", "nbf", "exp", "iss", "aud", "csrf"]})
            if not all(isinstance(claims[key], str) and claims[key] for key in ("sub", "jti", "csrf")):
                raise ValueError("令牌字段无效")
        except (jwt.InvalidTokenError, ValueError, TypeError):
            raise ChatError("UNAUTHORIZED", "登录已失效，请重新登录。", 401) from None
        async with self.store.connect() as db:
            row = await (await db.execute("""SELECT u.*,s.id AS auth_session_id,s.csrf_hash
                FROM auth_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.id=? AND s.user_id=? AND s.revoked_at IS NULL AND s.expires_at>?""",
                (claims["jti"], claims["sub"], int(time.time())))).fetchone()
        if not row or not row["is_active"] or not row["password_hash"] or row["role"] not in {"employee", "admin"}:
            raise ChatError("UNAUTHORIZED", "登录已失效或账号已停用，请重新登录。", 401)
        if not secrets.compare_digest(row["csrf_hash"], hashlib.sha256(claims["csrf"].encode()).hexdigest()):
            raise ChatError("UNAUTHORIZED", "登录已失效，请重新登录。", 401)
        return {**public_user(dict(row)), "csrf_token": claims["csrf"], "auth_session_id": row["auth_session_id"]}

    async def logout(self, user: dict):
        async with self.store.connect() as db:
            await db.execute("UPDATE auth_sessions SET revoked_at=? WHERE id=? AND user_id=?",
                             (int(time.time()), user["auth_session_id"], user["id"]))
            await db.commit()


async def current_user(request: Request) -> dict:
    """所有受保护接口共用的真实身份依赖；测试可显式覆盖，运行环境没有演示旁路。"""
    user = await request.app.state.auth.authenticate(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_same_origin(request)
        provided = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(provided.encode(), user["csrf_token"].encode()):
            raise ChatError("CSRF_INVALID", "操作校验已失效，请刷新页面后重试。", 403)
    request.state.user = user
    return user


async def require_admin(user: dict = Depends(current_user)) -> dict:
    """【可复用核心】后端校验管理员角色；隐藏前端菜单不能代替此依赖。"""
    if user["role"] != "admin":
        raise ChatError("FORBIDDEN", "当前账号没有管理后台访问权限。", 403)
    return user
