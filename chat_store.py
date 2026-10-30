"""SQLite持久层：所有权、完整问答历史、数据库级幂等和会话并发约束。"""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from chat_core import ChatError


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Store:
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def connect(self):
        """每次操作独立连接，短事务；不跨检索或大模型调用持有写锁。"""
        async with aiosqlite.connect(self.path, timeout=5) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            yield db

    async def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, login_subject TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, department_id TEXT NOT NULL,
                    venue_id TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
                    department_id TEXT NOT NULL, venue_id TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(id, owner_user_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    seq_no INTEGER NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('generating','completed','failed','interrupted')),
                    sources_json TEXT NOT NULL DEFAULT '[]', client_request_id TEXT,
                    request_hash TEXT, error_code TEXT, error_message TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id,owner_user_id) REFERENCES conversations(id,owner_user_id),
                    UNIQUE(conversation_id,seq_no), UNIQUE(turn_id,role),
                    UNIQUE(owner_user_id,client_request_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_generation_per_session
                ON messages(conversation_id) WHERE role='assistant' AND status='generating';
                CREATE INDEX IF NOT EXISTS message_history ON messages(conversation_id,seq_no);
                CREATE INDEX IF NOT EXISTS session_list ON conversations(owner_user_id,updated_at);
            """)
            await db.commit()
        await self.recover_interrupted()

    async def recover_interrupted(self):
        """单进程实例锁持有后才恢复：崩溃遗留的生成任务不能永久占用会话。"""
        async with self.connect() as db:
            await db.execute("""UPDATE messages SET status='interrupted',error_code='STREAM_INTERRUPTED',
                error_message='服务已重启，上次回答未完成。',updated_at=?
                WHERE role='assistant' AND status='generating'""", (now(),))
            await db.commit()

    async def ensure_user(self, user: dict):
        async with self.connect() as db:
            await db.execute("""INSERT OR IGNORE INTO users
                (id,login_subject,display_name,department_id,venue_id) VALUES (?,?,?,?,?)""",
                (user["id"], user.get("login_subject", user["id"]), user.get("display_name", "员工"),
                 user.get("department_id", "demo-sales"), user.get("venue_id", "demo-east")))
            await db.commit()
            row = await (await db.execute("SELECT is_active FROM users WHERE id=?", (user["id"],))).fetchone()
            if row is None or not row[0]:
                raise ChatError("UNAUTHORIZED", "账号不可用，请联系管理员。", 401)

    async def create_session(self, user: dict, title: str | None = None) -> dict:
        await self.ensure_user(user)
        stamp, identifier = now(), str(uuid.uuid4())
        async with self.connect() as db:
            await db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?)", (
                identifier, user["id"], user.get("department_id", "demo-sales"),
                user.get("venue_id", "demo-east"), title or "新的制度咨询", stamp, stamp))
            await db.commit()
        return {"id": identifier, "session_id": identifier, "title": title or "新的制度咨询",
                "created_at": stamp, "updated_at": stamp}

    async def list_sessions(self, owner: str, limit: int) -> dict:
        async with self.connect() as db:
            rows = await (await db.execute("""SELECT id,title,created_at,updated_at FROM conversations
                WHERE owner_user_id=? ORDER BY updated_at DESC,id DESC LIMIT ?""", (owner, limit + 1))).fetchall()
        return {"items": [dict(row) for row in rows[:limit]], "has_more": len(rows) > limit}

    async def owned_session(self, db, owner: str, session: str):
        row = await (await db.execute("SELECT * FROM conversations WHERE id=? AND owner_user_id=?",
                                      (session, owner))).fetchone()
        if row is None:
            raise ChatError("SESSION_NOT_FOUND", "会话不存在或无权访问。", 404)
        return row

    async def list_messages(self, owner: str, session: str, limit: int, before: int | None) -> dict:
        async with self.connect() as db:
            await self.owned_session(db, owner, session)
            rows = await (await db.execute("""SELECT * FROM messages WHERE conversation_id=?
                AND owner_user_id=? AND (? IS NULL OR seq_no<?) ORDER BY seq_no DESC LIMIT ?""",
                (session, owner, before, before, limit + 1))).fetchall()
        items = [self.public_message(row) for row in reversed(rows[:limit])]
        return {"items": items, "has_more": len(rows) > limit,
                "next_before": items[0]["seq_no"] if len(rows) > limit else None}

    @staticmethod
    def public_message(row) -> dict:
        result = {key: row[key] for key in ("id", "turn_id", "role", "content", "status", "seq_no",
                                             "error_code", "error_message", "created_at", "updated_at")}
        result["sources"] = json.loads(row["sources_json"])
        return result

    async def prepare_turn(self, owner: str, session: str, question: str, client_key: str) -> dict:
        """一个写事务检查幂等、会话忙状态并创建成对消息；唯一索引是第二道防线。"""
        digest = hashlib.sha256(json.dumps([session, question], ensure_ascii=False).encode()).hexdigest()
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            conversation = await self.owned_session(db, owner, session)
            previous = await (await db.execute("SELECT * FROM messages WHERE owner_user_id=? AND client_request_id=?",
                                               (owner, client_key))).fetchone()
            if previous is not None:
                if previous["request_hash"] != digest:
                    raise ChatError("IDEMPOTENCY_CONFLICT", "请求编号已用于其他内容，请重新发送。", 409)
                answer = await (await db.execute("SELECT * FROM messages WHERE turn_id=? AND role='assistant'",
                                                 (previous["turn_id"],))).fetchone()
                if answer["status"] == "generating":
                    raise ChatError("REQUEST_IN_PROGRESS", "这条问题正在处理中，请稍后查看。", 409, True)
                if answer["status"] == "completed":
                    return {"turn_id": previous["turn_id"], "user_message_id": previous["id"],
                            "assistant_message_id": answer["id"], "replayed": True,
                            "content": answer["content"], "sources": json.loads(answer["sources_json"])}
            busy = await (await db.execute("SELECT 1 FROM messages WHERE conversation_id=? AND status='generating'",
                                           (session,))).fetchone()
            if busy:
                raise ChatError("SESSION_BUSY", "当前会话正在生成回答，请稍后再试。", 409, True)
            stamp = now()
            if previous is not None:
                # 失败后的同键重试复用原来的问答对，不重复插入用户问题。
                await db.execute("""UPDATE messages SET content='',sources_json='[]',status='generating',
                    error_code=NULL,error_message=NULL,updated_at=? WHERE id=?""", (stamp, answer["id"]))
                turn, user_id, assistant_id = previous["turn_id"], previous["id"], answer["id"]
            else:
                turn, user_id, assistant_id = (str(uuid.uuid4()) for _ in range(3))
                row = await (await db.execute("SELECT COALESCE(MAX(seq_no),0) FROM messages WHERE conversation_id=?",
                                              (session,))).fetchone()
                seq = row[0]
                for mid, role, text, state, number, key in (
                    (user_id, "user", question, "completed", seq + 1, client_key),
                    (assistant_id, "assistant", "", "generating", seq + 2, None),
                ):
                    await db.execute("""INSERT INTO messages
                        (id,conversation_id,owner_user_id,turn_id,seq_no,role,content,status,
                         client_request_id,request_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (mid, session, owner, turn, number, role, text, state, key,
                         digest if key else None, stamp, stamp))
                if conversation["title"] == "新的制度咨询":
                    await db.execute("UPDATE conversations SET title=? WHERE id=?", (question[:24], session))
            await db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (stamp, session))
            await db.commit()
            return {"turn_id": turn, "user_message_id": user_id,
                    "assistant_message_id": assistant_id, "replayed": False}

    async def history(self, owner_id: str, session_id: str, limit: int = 6) -> list[dict]:
        """以成功的assistant为锚，取完整问答对，失败/生成中轮次不进入上下文。"""
        async with self.connect() as db:
            await self.owned_session(db, owner_id, session_id)
            rows = await (await db.execute("""SELECT u.content AS question,a.content AS answer
                FROM messages a JOIN messages u ON u.turn_id=a.turn_id AND u.role='user'
                WHERE a.conversation_id=? AND a.owner_user_id=? AND a.role='assistant'
                AND a.status='completed' AND u.status='completed'
                ORDER BY a.seq_no DESC LIMIT ?""", (session_id, owner_id, limit))).fetchall()
        return [message for row in reversed(rows) for message in (
            {"role": "user", "content": row["question"]}, {"role": "assistant", "content": row["answer"]})]

    async def checkpoint(self, turn: str, owner: str, content: str):
        async with self.connect() as db:
            await db.execute("UPDATE messages SET content=?,updated_at=? WHERE turn_id=? AND owner_user_id=? AND status='generating'",
                             (content, now(), turn, owner))
            await db.commit()

    async def finish_turn(self, turn_id: str, owner_id: str, status: str, content: str,
                          sources: list | None = None, error_code: str | None = None,
                          error_message: str | None = None) -> bool:
        """条件更新保证取消/完成竞争只有一个终态；完成正文与来源同事务提交。"""
        async with self.connect() as db:
            cursor = await db.execute("""UPDATE messages SET content=?,status=?,sources_json=?,
                error_code=?,error_message=?,updated_at=? WHERE turn_id=? AND owner_user_id=?
                AND role='assistant' AND status='generating'""", (
                content, status, json.dumps(sources or [], ensure_ascii=False), error_code,
                error_message, now(), turn_id, owner_id))
            await db.commit()
            return cursor.rowcount == 1
