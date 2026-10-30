"""管理后台第1批的身份与权限回归：真实JWT/Argon2/SQLite，检索和模型离线替身。

这些测试验证客户端不能伪造身份、提升角色或借用他人的会话；不访问真实大模型。
请把pytest的--basetemp指定到仓库外的授权临时目录，避免提交账号或运行数据库。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
import time
import uuid

import httpx
import jwt
import pytest

from chat_app import create_app
from chat_auth import COOKIE_NAME, create_user
from chat_core import Settings
from stage1_rag import Chunk, LLMConfig, SearchHit


# 仅用于离线测试的独立签名材料；不是业务环境的JWT密钥或供应商API密钥。
TEST_SECRET = "offline-auth-regression-secret-" + "x" * 40
TEST_PASSWORD = "仅供离线回归的长口令-2026"


class OfflineModel:
    """输出合法来源标记，确保鉴权通过后仍能走完整SSE及持久化路径。"""
    def __init__(self):
        self.calls = 0

    async def __call__(self, messages, *args):
        self.calls += 1
        yield "需要重新提交申请。[S1]"


def offline_search(question):
    return [SearchHit(Chunk("S1", "权限测试制度.md", 0, 13, 1, 1,
                            "排期变更需要重新提交申请。"), 0.03, 1.0, 0.8)]


def check_error(response, status):
    """所有接口错误保留中文信封，不能回传密码哈希、令牌或底层异常。"""
    assert response.status_code == status, response.text
    result = response.json()
    assert result["code"] != "OK"
    assert result["request_id"]
    assert any("\u4e00" <= char <= "\u9fff" for char in result["message"])
    assert "password_hash" not in response.text
    assert TEST_PASSWORD not in response.text
    return result


@asynccontextmanager
async def auth_rig(tmp_path: Path, *, secure=False, seed=True, secret=TEST_SECRET):
    """使用真实依赖，不覆盖身份依赖，也不通过演示身份绕过权限检查。"""
    model = OfflineModel()
    settings = Settings(
        data_dir=tmp_path / "data", rag_dir=tmp_path / "rag", jwt_secret=secret,
        auth_cookie_secure=secure,
        llm_config=LLMConfig("compatible", "http://localhost:1234/v1", "offline", "offline"),
    )
    app = create_app(settings, retriever=offline_search, llm_stream=model)
    async with app.router.lifespan_context(app):
        users = {}
        if seed:
            for username, role, department, venue in (
                ("employee.a", "employee", "sales", "east"),
                ("admin.a", "admin", "operations", "west"),
            ):
                users[username] = await create_user(app.state.store, username, TEST_PASSWORD,
                    "测试管理员" if role == "admin" else "测试员工", role, department, venue)
        # HTTPS用于Secure Cookie回归，其余请求沿用本机HTTP开发方式。
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                base_url="https://testserver" if secure else "http://testserver",
                follow_redirects=False) as client:
            yield app, client, users, model


async def login(client, username="employee.a"):
    response = await client.post("/auth/login", json={"username": username, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "OK"
    data = response.json()["data"]
    assert data["csrf_token"]
    assert client.cookies.get(COOKIE_NAME)
    return response, data


def csrf(data):
    return {"X-CSRF-Token": data["csrf_token"]}


def test_anonymous_pages_redirect_and_every_business_api_requires_login(tmp_path, monkeypatch):
    async def scenario():
        # 旧环境开关不能重新打开演示身份旁路。
        monkeypatch.setenv("CHAT_DEMO_MODE", "1")
        async with auth_rig(tmp_path) as (app, client, users, model):
            page = await client.get("/")
            assert page.status_code in {302, 303, 307}
            assert page.headers["location"].startswith("/login")
            login_page = await client.get("/login")
            assert login_page.status_code == 200
            assert "text/html" in login_page.headers["content-type"]
            for method, path, body in (
                ("GET", "/auth/me", None), ("GET", "/config", None),
                ("GET", "/sessions", None), ("POST", "/sessions", {}),
                ("GET", f"/sessions/{uuid.uuid4()}/messages", None),
                ("POST", "/chat", {"session_id": str(uuid.uuid4()), "question": "如何变更？"}),
                ("GET", "/admin/api/status", None),
            ):
                check_error(await client.request(method, path, json=body), 401)
            assert model.calls == 0

    asyncio.run(scenario())


def test_login_uses_http_only_cookie_and_never_returns_credentials(tmp_path):
    async def scenario():
        async with auth_rig(tmp_path) as (app, client, users, model):
            check_error(await client.post("/auth/login", json={"username": "employee.a", "password": "错误密码"}), 401)
            check_error(await client.post("/auth/login", json={"username": "missing.account", "password": TEST_PASSWORD}), 401)
            check_error(await client.post("/auth/login", json={"username": "employee.a"}), 422)
            check_error(await client.post("/auth/login", json={"username": "employee.a", "password": TEST_PASSWORD,
                                                               "role": "admin"}), 422)
            response, data = await login(client, "EMPLOYEE.A")
            cookie = response.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=strict" in cookie
            assert "path=/" in cookie
            assert data["role"] == "employee" and data["department_id"] == "sales"
            assert data["id"] == users["employee.a"]["id"]
            token = client.cookies.get(COOKIE_NAME)
            assert token not in response.text
            assert TEST_PASSWORD not in response.text and "password_hash" not in response.text
            me = await client.get("/auth/me")
            assert me.status_code == 200 and me.json()["data"] == data
            async with app.state.store.connect() as db:
                row = await (await db.execute("SELECT password_hash FROM users WHERE id=?", (data["id"],))).fetchone()
            assert row["password_hash"].startswith("$argon2")
            assert TEST_PASSWORD not in row["password_hash"]

    asyncio.run(scenario())


def test_csrf_and_origin_checks_protect_mutations_without_blocking_reads(tmp_path):
    async def scenario():
        async with auth_rig(tmp_path) as (app, client, users, model):
            check_error(await client.post("/auth/login", json={"username": "employee.a", "password": TEST_PASSWORD},
                                           headers={"Origin": "https://untrusted.example"}), 403)
            _, data = await login(client)
            assert (await client.get("/sessions")).status_code == 200
            for headers in ({}, {"X-CSRF-Token": "forged"},
                            {**csrf(data), "Origin": "https://untrusted.example"},
                            {**csrf(data), "Sec-Fetch-Site": "cross-site"}):
                check_error(await client.post("/sessions", json={}, headers=headers), 403)
            created = await client.post("/sessions", json={}, headers=csrf(data))
            assert created.status_code == 201
            session = created.json()["data"]["session_id"]
            check_error(await client.post("/chat", json={"session_id": session, "question": "如何变更？"},
                         headers={"Idempotency-Key": str(uuid.uuid4())}), 403)
            assert model.calls == 0
            check_error(await client.post("/auth/logout"), 403)
            assert (await client.get("/auth/me")).status_code == 200

    asyncio.run(scenario())


def test_role_checks_apply_to_pages_and_api_and_read_latest_database_role(tmp_path):
    async def scenario():
        async with auth_rig(tmp_path) as (app, client, users, model):
            _, employee = await login(client)
            for path in ("/admin", "/admin/knowledge", "/admin/prompts", "/admin/conversations", "/admin/api/status"):
                check_error(await client.get(path), 403)
            # 相同JWT立即获得当前数据库角色，不依赖令牌中的过时角色字段。
            async with app.state.store.connect() as db:
                await db.execute("UPDATE users SET role='admin',department_id='new-dept',venue_id='new-venue' WHERE id=?",
                                 (employee["id"],))
                await db.commit()
            fresh = (await client.get("/auth/me")).json()["data"]
            assert fresh["role"] == "admin"
            assert fresh["department_id"] == "new-dept" and fresh["venue_id"] == "new-venue"
            assert (await client.get("/admin/api/status")).status_code == 200
            for path in ("/admin/knowledge", "/admin/prompts", "/admin/conversations"):
                page = await client.get(path)
                assert page.status_code == 200, page.text
                assert "text/html" in page.headers["content-type"]
            async with app.state.store.connect() as db:
                await db.execute("UPDATE users SET role='employee' WHERE id=?", (employee["id"],))
                await db.commit()
            check_error(await client.get("/admin/api/status"), 403)
            assert (await client.get("/sessions")).status_code == 200
            async with app.state.store.connect() as db:
                await db.execute("UPDATE users SET is_active=0 WHERE id=?", (employee["id"],))
                await db.commit()
            check_error(await client.get("/auth/me"), 401)
            check_error(await client.get("/sessions"), 401)
            check_error(await client.post("/auth/login", json={"username": "employee.a", "password": TEST_PASSWORD}), 401)

    asyncio.run(scenario())


def test_admin_chat_ownership_and_employee_isolation_survive_real_authentication(tmp_path):
    async def scenario():
        async with auth_rig(tmp_path) as (app, client, users, model):
            _, employee = await login(client)
            created = await client.post("/sessions", json={}, headers=csrf(employee))
            assert created.status_code == 201
            employee_session = created.json()["data"]["session_id"]
            answer = await client.post("/chat", json={"session_id": employee_session, "question": "如何变更？"},
                                      headers={**csrf(employee), "Idempotency-Key": str(uuid.uuid4())})
            assert answer.status_code == 200 and "event: done" in answer.text
            assert model.calls == 1
            await client.post("/auth/logout", headers=csrf(employee))
            _, admin = await login(client, "admin.a")
            assert (await client.get("/admin/api/status")).status_code == 200
            # 管理员审计权限不能提升普通聊天API的会话所有权。
            check_error(await client.get(f"/sessions/{employee_session}/messages"), 404)
            check_error(await client.post("/chat", json={"session_id": employee_session, "question": "尝试冒用"},
                         headers={**csrf(admin), "Idempotency-Key": str(uuid.uuid4())}), 404)
            assert model.calls == 1
            own = await client.post("/sessions", json={}, headers=csrf(admin))
            assert own.status_code == 201
            listed = (await client.get("/sessions")).json()["data"]["items"]
            assert [item["id"] for item in listed] == [own.json()["data"]["session_id"]]

    asyncio.run(scenario())


def test_logout_revokes_the_copied_jwt_and_removes_the_cookie(tmp_path):
    async def scenario():
        async with auth_rig(tmp_path) as (app, client, users, model):
            _, data = await login(client)
            stolen_token = client.cookies.get(COOKIE_NAME)
            signed_out = await client.post("/auth/logout", headers=csrf(data))
            assert signed_out.status_code == 200
            assert not client.cookies.get(COOKIE_NAME)
            check_error(await client.get("/auth/me"), 401)
            check_error(await client.get("/auth/me", headers={"Cookie": f"{COOKIE_NAME}={stolen_token}"}), 401)
            check_error(await client.post("/sessions", json={},
                         headers={**csrf(data), "Cookie": f"{COOKIE_NAME}={stolen_token}"}), 401)
            async with app.state.store.connect() as db:
                row = await (await db.execute("SELECT revoked_at FROM auth_sessions WHERE user_id=?", (data["id"],))).fetchone()
            assert row["revoked_at"] is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["expired", "wrong-signature", "wrong-audience", "unknown-session", "no-signature"])
def test_invalid_jwts_are_rejected_before_business_operations(tmp_path, change):
    async def scenario():
        async with auth_rig(tmp_path) as (app, client, users, model):
            await login(client)
            original = client.cookies.get(COOKIE_NAME)
            claims = jwt.decode(original, options={"verify_signature": False})
            key, algorithm = TEST_SECRET, "HS256"
            if change == "expired":
                claims.update({"iat": int(time.time()) - 120, "nbf": int(time.time()) - 120,
                               "exp": int(time.time()) - 60})
            elif change == "wrong-signature":
                key = "different-offline-signing-key-" + "z" * 40
            elif change == "wrong-audience":
                claims["aud"] = "another-product"
            elif change == "unknown-session":
                claims["jti"] = str(uuid.uuid4())
            else:
                key, algorithm = "", "none"
            forged = jwt.encode(claims, key, algorithm=algorithm)
            check_error(await client.get("/auth/me", headers={"Cookie": f"{COOKIE_NAME}={forged}"}), 401)
            assert model.calls == 0

    asyncio.run(scenario())


def test_secure_cookie_and_server_restart_preserve_valid_login(tmp_path):
    async def scenario():
        # 未显式配置JWT密钥时只在运行目录生成一次，服务重启不丢失已签发登录。
        async with auth_rig(tmp_path, secure=True, secret=None) as (app, client, users, model):
            response, data = await login(client)
            assert "; secure" in response.headers["set-cookie"].lower()
            token = client.cookies.get(COOKIE_NAME)
            key_path = tmp_path / "data" / "jwt-signing.key"
            first_key = key_path.read_bytes()
            assert len(first_key) >= 32
        async with auth_rig(tmp_path, secure=True, seed=False, secret=None) as (app, client, users, model):
            assert key_path.read_bytes() == first_key
            restored = await client.get("/auth/me", headers={"Cookie": f"{COOKIE_NAME}={token}"})
            assert restored.status_code == 200
            assert restored.json()["data"]["id"] == data["id"]

    asyncio.run(scenario())


def test_legacy_database_is_migrated_without_claiming_demo_conversations(tmp_path):
    async def scenario():
        directory = tmp_path / "data"
        directory.mkdir()
        legacy_id, legacy_session = str(uuid.uuid4()), str(uuid.uuid4())
        with sqlite3.connect(directory / "chat.sqlite3") as db:
            db.executescript("""
                CREATE TABLE users(id TEXT PRIMARY KEY,login_subject TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,department_id TEXT NOT NULL,venue_id TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE conversations(id TEXT PRIMARY KEY,owner_user_id TEXT NOT NULL REFERENCES users(id),
                    department_id TEXT NOT NULL,venue_id TEXT NOT NULL,title TEXT NOT NULL,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(id,owner_user_id));
            """)
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,1)",
                       (legacy_id, "demo-employee", "历史演示员工", "demo-sales", "demo-east"))
            db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?)",
                       (legacy_session, legacy_id, "demo-sales", "demo-east", "历史演示会话", "old", "old"))
        async with auth_rig(tmp_path) as (app, client, users, model):
            async with app.state.store.connect() as db:
                legacy = await (await db.execute("SELECT * FROM users WHERE id=?", (legacy_id,))).fetchone()
                conversation = await (await db.execute("SELECT owner_user_id FROM conversations WHERE id=?",
                                                      (legacy_session,))).fetchone()
            assert legacy["is_active"] == 0 and not legacy["password_hash"]
            assert legacy["role"] == "employee"
            assert conversation["owner_user_id"] == legacy_id
            check_error(await client.post("/auth/login", json={"username": "demo-employee", "password": TEST_PASSWORD}), 401)
            await login(client)
            assert (await client.get("/sessions")).json()["data"]["items"] == []
            check_error(await client.get(f"/sessions/{legacy_session}/messages"), 404)

    asyncio.run(scenario())
