"""【可复用核心】Agent运行、逐轮审计和可信员工确认的SQLite持久层。

LangGraph断点保存消息/执行位置；本模块保存业务所有权、五轮预算和确认决定。
二者分工明确：浏览器和模型都不能直接修改断点，更不能把自然语言当作确认。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from chat_core import ChatError
from chat_store import Store, now
from stage1_rag import redact_for_llm

MAX_ROUNDS = 5


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def safe_arguments(value) -> str:
    """审计保留参数结构，遮盖常见个人字段与密钥；不是完整企业脱敏实现。"""
    def scrub(item):
        if isinstance(item, dict):
            return {key: ("[已隐藏]" if any(word in key.lower() for word in ("password", "secret", "api_key", "authorization"))
                          else scrub(val)) for key, val in item.items()}
        if isinstance(item, list):
            return [scrub(val) for val in item]
        return redact_for_llm(item) if isinstance(item, str) else item
    encoded = dumps(scrub(value))
    return encoded if len(encoded) <= 12000 else dumps({"truncated": True, "excerpt": encoded[:11000]})


class AgentStateStore:
    def __init__(self, store: Store):
        self.store = store

    async def initialize(self):
        async with self.store.connect() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
                    question TEXT NOT NULL, client_request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    status TEXT NOT NULL, execution_id TEXT, round_count INTEGER NOT NULL DEFAULT 0 CHECK(round_count BETWEEN 0 AND 5),
                    answer TEXT NOT NULL DEFAULT '', error_code TEXT,
                    pending_validation_id TEXT, confirmation_id TEXT, confirmation_status TEXT,
                    preview_hash TEXT, preview_json TEXT, confirmation_expires_at REAL,
                    draft_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id,client_request_id)
                );
                CREATE TABLE IF NOT EXISTS agent_decision_logs (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    round_no INTEGER NOT NULL, event_type TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL DEFAULT '', tool_name TEXT,
                    parameters_json TEXT NOT NULL DEFAULT '{}', duration_ms REAL NOT NULL DEFAULT 0,
                    result_summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    error_code TEXT, started_at TEXT NOT NULL, finished_at TEXT,
                    UNIQUE(run_id,round_no,event_type,tool_call_id)
                );
                CREATE INDEX IF NOT EXISTS agent_log_run ON agent_decision_logs(run_id,round_no,started_at);
            """)
            # 只有OS实例锁持有者执行恢复；未确认的草稿不在这里执行或自动批准。
            await db.execute("UPDATE agent_runs SET status='interrupted',updated_at=? WHERE status='running'", (now(),))
            await db.execute("""UPDATE agent_decision_logs SET status='interrupted',finished_at=?,
                result_summary='执行被中断，结果须核对。' WHERE status='running'""", (now(),))
            await db.commit()

    async def owned(self, owner: str, run_id: str) -> dict:
        async with self.store.connect() as db:
            row = await (await db.execute("SELECT * FROM agent_runs WHERE id=? AND owner_user_id=?", (run_id, owner))).fetchone()
        if not row:
            raise ChatError("RUN_NOT_FOUND", "执行记录不存在或无权访问。", 404)
        return dict(row)

    async def prepare(self, owner: str, question: str, key: str) -> dict:
        digest = hashlib.sha256(question.encode()).hexdigest()
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT * FROM agent_runs WHERE owner_user_id=? AND client_request_id=?", (owner, key))).fetchone()
            if row:
                if row["request_hash"] != digest:
                    raise ChatError("IDEMPOTENCY_CONFLICT", "请求编号已用于其他需求，请重新发起。", 409)
                return dict(row)
            identifier, stamp = str(uuid.uuid4()), now()
            await db.execute("""INSERT INTO agent_runs
                (id,owner_user_id,question,client_request_id,request_hash,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'queued',?,?)""", (identifier, owner, question, key, digest, stamp, stamp))
            await db.commit()
        return await self.owned(owner, identifier)

    async def claim(self, owner: str, run_id: str):
        execution_id = str(uuid.uuid4())
        async with self.store.connect() as db:
            cursor = await db.execute("""UPDATE agent_runs SET status='running',execution_id=?,updated_at=?
                WHERE id=? AND owner_user_id=? AND status IN ('queued','interrupted')""", (execution_id, now(), run_id, owner))
            await db.commit()
            if cursor.rowcount != 1:
                raise ChatError("RUN_BUSY", "当前执行正在处理或已结束，请刷新查看。", 409)
        return execution_id

    async def reserve_round(self, run_id: str) -> int:
        """发起模型请求前原子消耗预算；失败/重启不返还，确保实际请求最多五次。"""
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT round_count FROM agent_runs WHERE id=?", (run_id,))).fetchone()
            if not row or row[0] >= MAX_ROUNDS:
                raise ChatError("AGENT_ROUND_LIMIT", "已达到五轮决策上限，请核对结果后重新发起需求。", 409)
            number = row[0] + 1
            await db.execute("UPDATE agent_runs SET round_count=?,updated_at=? WHERE id=?", (number, now(), run_id))
            await db.commit()
            return number

    async def log_start(self, run_id, number, event_type, call_id="", tool_name=None, arguments=None):
        async with self.store.connect() as db:
            await db.execute("""INSERT OR IGNORE INTO agent_decision_logs
                (id,run_id,round_no,event_type,tool_call_id,tool_name,parameters_json,status,started_at)
                VALUES (?,?,?,?,?,?,?,'running',?)""", (str(uuid.uuid4()), run_id, number, event_type,
                call_id, tool_name, safe_arguments(arguments or {}), now()))
            await db.commit()

    async def log_finish(self, run_id, number, event_type, duration, summary, *, call_id="", tool_name=None,
                         arguments=None, status="completed", error_code=None):
        async with self.store.connect() as db:
            await db.execute("""UPDATE agent_decision_logs SET duration_ms=?,result_summary=?,status=?,
                error_code=?,finished_at=?,tool_name=COALESCE(?,tool_name),parameters_json=COALESCE(?,parameters_json)
                WHERE run_id=? AND round_no=? AND event_type=? AND tool_call_id=?""", (
                round(max(0.0, duration), 3), redact_for_llm(summary)[:1000], status, error_code, now(),
                tool_name, safe_arguments(arguments) if arguments is not None else None,
                run_id, number, event_type, call_id))
            await db.commit()

    async def pending(self, owner, run_id, validation_id, preview):
        """中断节点重跑时复用同一确认编号，不能产生第二份不同内容的确认卡。"""
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT * FROM agent_runs WHERE id=? AND owner_user_id=?", (run_id, owner))).fetchone()
            if row["pending_validation_id"] == validation_id and row["confirmation_id"]:
                return dict(row)
            await db.execute("""UPDATE agent_runs SET pending_validation_id=?,confirmation_id=?,
                confirmation_status='pending',preview_hash=?,preview_json=?,confirmation_expires_at=?,updated_at=?
                WHERE id=? AND owner_user_id=?""", (validation_id, str(uuid.uuid4()), preview["preview_hash"],
                dumps(preview), preview["expires_at"], now(), run_id, owner))
            await db.commit()
        return await self.owned(owner, run_id)

    async def confirm(self, owner, run_id, confirmation_id, preview_hash, approved):
        """唯一可信确认入口使用CAS，拒绝跨用户、内容替换和并发重复消费。"""
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT * FROM agent_runs WHERE id=? AND owner_user_id=?", (run_id, owner))).fetchone()
            if not row:
                raise ChatError("RUN_NOT_FOUND", "执行记录不存在或无权访问。", 404)
            if row["confirmation_id"] != confirmation_id or row["preview_hash"] != preview_hash:
                raise ChatError("CONFIRMATION_MISMATCH", "确认内容与预览不一致，请刷新后核对。", 409)
            target = "approved" if approved else "rejected"
            if row["confirmation_status"] in {"approved", "rejected"}:
                if row["confirmation_status"] == target and row["status"] in {"interrupted", "awaiting_confirmation"}:
                    await db.execute("UPDATE agent_runs SET status='running',execution_id=?,updated_at=? WHERE id=?", (str(uuid.uuid4()), now(), run_id))
                    await db.commit()
                    return True
                if row["confirmation_status"] == target and row["status"] != "running":
                    return False
                raise ChatError("CONFIRMATION_CONSUMED", "本次确认已处理，请刷新查看执行结果。", 409)
            if row["status"] != "awaiting_confirmation" or row["confirmation_status"] != "pending":
                raise ChatError("CONFIRMATION_NOT_READY", "草稿尚未进入可确认状态，请稍后刷新。", 409)
            if approved and row["confirmation_expires_at"] <= time.time():
                raise ChatError("VALIDATION_EXPIRED", "校验已过期，请重新发起需求并校验。", 409)
            await db.execute("UPDATE agent_runs SET confirmation_status=?,status='running',execution_id=?,updated_at=? WHERE id=?",
                             (target, str(uuid.uuid4()), now(), run_id))
            await db.commit()
        return True

    async def update(self, run_id, status, *, answer=None, error_code=None, draft=None):
        async with self.store.connect() as db:
            await db.execute("""UPDATE agent_runs SET status=?,answer=COALESCE(?,answer),error_code=?,
                draft_json=COALESCE(?,draft_json),updated_at=? WHERE id=?""", (
                status, answer, error_code, dumps(draft) if draft else None, now(), run_id))
            await db.commit()

    async def close_unfinished_logs(self, run_id, status, error_code):
        """失败/断线也必须有审计终态，不能把已结束的工具永远记成运行中。"""
        async with self.store.connect() as db:
            await db.execute("""UPDATE agent_decision_logs SET status=?,error_code=?,finished_at=?,
                result_summary='执行未完成，请核对工作流与已有草稿。'
                WHERE run_id=? AND status='running'""", (status, error_code, now(), run_id))
            await db.commit()

    async def restore_waiting(self, run_id, execution_id):
        """断点核对期间可能已有确认请求启动；CAS禁止旧状态查询覆盖新执行。"""
        async with self.store.connect() as db:
            await db.execute("""UPDATE agent_runs SET status='awaiting_confirmation',updated_at=?
                WHERE id=? AND status='interrupted' AND execution_id IS ?""", (now(), run_id, execution_id))
            await db.commit()

    async def interrupt_execution(self, run_id, execution_id):
        """清理状态与未结束审计在同一事务完成，旧流不能误伤新流的日志。"""
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("""UPDATE agent_runs SET status='interrupted',updated_at=?
                WHERE id=? AND status='running' AND execution_id=?""", (now(), run_id, execution_id))
            if cursor.rowcount == 1:
                await db.execute("""UPDATE agent_decision_logs SET status='interrupted',error_code='STREAM_INTERRUPTED',
                    finished_at=?,result_summary='执行中断，请核对本次状态。' WHERE run_id=? AND status='running'""", (now(), run_id))
            await db.commit()

    async def public(self, owner, run_id):
        row = await self.owned(owner, run_id)
        async with self.store.connect() as db:
            logs = await (await db.execute("""SELECT round_no,event_type,tool_name,parameters_json,duration_ms,
                result_summary,status,error_code,started_at,finished_at FROM agent_decision_logs
                WHERE run_id=? ORDER BY round_no,started_at,id LIMIT 100""", (run_id,))).fetchall()
        pending = None
        if row["confirmation_id"] and row["status"] == "awaiting_confirmation":
            pending = {"id": row["confirmation_id"], "preview_hash": row["preview_hash"],
                       "expires_at": row["confirmation_expires_at"], "preview": json.loads(row["preview_json"])}
        return {key: row[key] for key in ("id", "question", "status", "round_count", "answer", "error_code", "created_at", "updated_at")} | {
            "max_rounds": MAX_ROUNDS, "pending_confirmation": pending,
            "draft": json.loads(row["draft_json"]) if row["draft_json"] else None,
            "logs": [dict(log) for log in logs]}
