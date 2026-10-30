"""【可复用核心】Agent业务工具：只读查询、确定性校验与受确认保护的草稿提交。

这里不依赖模型判断“校验是否通过”。有效规则、资源版本、用户授权及凭据归属
全部由SQLite复查。当前只显式支持虚构演示规则；未接入企业规则时不会默认放行。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from dhls.core.chat import ChatError
from dhls.core.store import Store, now
from dhls.core.rag import redact_for_llm

RECEIPT_SECONDS = 600
DEMO_RULE_VERSION = "DEMO-AGENT-1"


def canonical(value) -> str:
    """稳定序列化用于内容绑定；不是给模型自行计算的“授权签名”。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def stamp() -> int:
    return int(time.time())


class Candidate(BaseModel):
    """【可复用核心】候选业务内容。未知字段不能混入身份、审批状态或跳过校验开关。

    所有日期须带时区，统一UTC后再计算内容摘要和时间重叠。布撤展等更复杂资源
    规则须在后续规则适配器加入，本演示仅有一项排他资源和人数容量限制。
    """
    model_config = ConfigDict(extra="forbid")
    request_type: Literal["new_booking", "booking_change"]
    venue_id: str = Field(min_length=1, max_length=100)
    resource_id: str = Field(min_length=1, max_length=150)
    start_at: str = Field(min_length=1, max_length=50)
    end_at: str = Field(min_length=1, max_length=50)
    attendees: int = Field(strict=True, gt=0, le=1000000)
    purpose: str = Field(min_length=1, max_length=1000)
    original_booking_id: str | None = Field(default=None, max_length=150)
    expected_booking_version: int | None = Field(default=None, strict=True, gt=0)
    change_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("venue_id", "resource_id", "purpose", "original_booking_id", "change_reason")
    @classmethod
    def trim_text(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("字段不能为空白")
        return value

    @field_validator("start_at", "end_at")
    @classmethod
    def utc_time(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("时间必须含明确时区")
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")

    @model_validator(mode="after")
    def complete_shape(self):
        if self.start_at >= self.end_at:
            raise ValueError("结束时间必须晚于开始时间")
        if self.request_type == "booking_change":
            if not self.original_booking_id or not self.expected_booking_version or not self.change_reason:
                raise ValueError("变更须提供原排期、版本和原因")
        elif self.original_booking_id is not None or self.expected_booking_version is not None or self.change_reason is not None:
            raise ValueError("新申请不能携带原排期变更字段")
        return self


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    venue_id: str = Field(min_length=1, max_length=100)


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    booking_id: str = Field(min_length=1, max_length=150)


class AvailabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: Candidate


class ValidateInput(AvailabilityInput):
    availability_check_id: str = Field(min_length=1, max_length=100)
    policy_evidence_id: str = Field(min_length=1, max_length=100)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    validation_id: str = Field(min_length=1, max_length=100)


INPUT_MODELS = {"search_policy": SearchInput, "get_booking_context": BookingInput,
                "check_availability": AvailabilityInput, "validate_request": ValidateInput,
                "create_draft": DraftInput}


class AgentBusiness:
    def __init__(self, store: Store, retriever=None, document_version: str = "", knowledge_is_demo: bool = False):
        self.store, self.retriever = store, retriever
        self.document_version, self.knowledge_is_demo = document_version, knowledge_is_demo

    async def initialize(self):
        """只增量建表，不自动给真实账号授权、填入容量或把演示规则当作企业制度。"""
        async with self.store.connect() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_business_venues (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    rule_version TEXT NOT NULL, rules_json TEXT NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0, is_demo INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS agent_business_resources (
                    id TEXT PRIMARY KEY, venue_id TEXT NOT NULL REFERENCES agent_business_venues(id),
                    name TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity>0),
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS agent_business_grants (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    venue_id TEXT NOT NULL REFERENCES agent_business_venues(id),
                    department_id TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
                    can_create_draft INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(user_id,venue_id)
                );
                CREATE TABLE IF NOT EXISTS agent_business_bookings (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
                    venue_id TEXT NOT NULL REFERENCES agent_business_venues(id),
                    resource_id TEXT NOT NULL REFERENCES agent_business_resources(id),
                    start_at TEXT NOT NULL, end_at TEXT NOT NULL, attendees INTEGER NOT NULL,
                    purpose TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'approved'
                );
                CREATE INDEX IF NOT EXISTS agent_booking_window
                    ON agent_business_bookings(resource_id,status,start_at,end_at);
                CREATE TABLE IF NOT EXISTS agent_business_evidence (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    venue_id TEXT NOT NULL, rule_version TEXT NOT NULL, document_version TEXT NOT NULL,
                    sources_json TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_business_checks (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    venue_id TEXT NOT NULL, candidate_hash TEXT NOT NULL, venue_revision INTEGER NOT NULL,
                    resource_version INTEGER NOT NULL, result_json TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_business_booking_reads (
                    owner_user_id TEXT NOT NULL, run_id TEXT NOT NULL, booking_id TEXT NOT NULL,
                    booking_version INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                    PRIMARY KEY(owner_user_id,run_id,booking_id)
                );
                CREATE TABLE IF NOT EXISTS agent_business_validations (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    venue_id TEXT NOT NULL, candidate_json TEXT NOT NULL, candidate_hash TEXT NOT NULL,
                    preview_hash TEXT NOT NULL, venue_revision INTEGER NOT NULL,
                    resource_version INTEGER NOT NULL, rule_version TEXT NOT NULL,
                    document_version TEXT NOT NULL, evidence_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER,
                    status TEXT NOT NULL DEFAULT 'PASS'
                );
                CREATE TABLE IF NOT EXISTS agent_business_drafts (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
                    run_id TEXT NOT NULL UNIQUE, validation_id TEXT NOT NULL UNIQUE,
                    candidate_json TEXT NOT NULL, preview_hash TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft'
                );
                -- 新增占用同样改变场馆版本，避免只检查旧行版本而漏掉并发插入的冲突。
                CREATE TRIGGER IF NOT EXISTS agent_booking_insert AFTER INSERT ON agent_business_bookings
                BEGIN UPDATE agent_business_venues SET revision=revision+1 WHERE id=NEW.venue_id; END;
                CREATE TRIGGER IF NOT EXISTS agent_booking_delete AFTER DELETE ON agent_business_bookings
                BEGIN UPDATE agent_business_venues SET revision=revision+1 WHERE id=OLD.venue_id; END;
                CREATE TRIGGER IF NOT EXISTS agent_booking_update AFTER UPDATE ON agent_business_bookings
                BEGIN UPDATE agent_business_venues SET revision=revision+1 WHERE id IN (OLD.venue_id,NEW.venue_id); END;
                CREATE TRIGGER IF NOT EXISTS agent_resource_insert AFTER INSERT ON agent_business_resources
                BEGIN UPDATE agent_business_venues SET revision=revision+1 WHERE id=NEW.venue_id; END;
                CREATE TRIGGER IF NOT EXISTS agent_resource_delete AFTER DELETE ON agent_business_resources
                BEGIN UPDATE agent_business_venues SET revision=revision+1 WHERE id=OLD.venue_id; END;
                CREATE TRIGGER IF NOT EXISTS agent_resource_update AFTER UPDATE ON agent_business_resources
                BEGIN UPDATE agent_business_venues SET revision=revision+1 WHERE id IN (OLD.venue_id,NEW.venue_id); END;
                CREATE TRIGGER IF NOT EXISTS agent_rule_update
                AFTER UPDATE OF rule_version,rules_json,approved,is_demo ON agent_business_venues
                BEGIN UPDATE agent_business_venues SET revision=revision+1 WHERE id=NEW.id; END;
            """)
            await db.commit()

    @staticmethod
    def schemas() -> list[dict]:
        """【可复用核心】工具白名单及参数协议，不开放SQL、Shell或任意Python执行。"""
        descriptions = {
            "search_policy": "检索有权读取的制度与当前场馆资源目录；返回policy_evidence_id及条文。",
            "get_booking_context": "变更已有排期前必须读取原排期及版本；仅当前用户自己的排期。",
            "check_availability": "查询候选方案的容量和冲突，返回availability_check_id；不占档。",
            "validate_request": "在制度检索、资源查询及变更基线读取后执行确定性校验；仅PASS返回validation_id。",
            "create_draft": "用户要求准备草稿且校验PASS后调用，仅传有效validation_id。本次调用首先发起草稿预览与确认中断，不会直接写入草稿；不要先用聊天索要确认。员工通过可信页面确认后才建草稿，不能替用户确认，不占档或审批。",
        }
        return [{"type": "function", "function": {"name": name, "description": descriptions[name],
                "parameters": model.model_json_schema()}} for name, model in INPUT_MODELS.items()]

    async def execute(self, name: str, args: dict, user: dict, run_id: str) -> dict:
        """模型只能选择白名单业务入口；建草稿的写入路径不会由模型直接调用。"""
        if name not in INPUT_MODELS:
            raise ChatError("TOOL_NOT_ALLOWED", "未授权的工具，不能执行。", 403)
        try:
            parsed = INPUT_MODELS[name].model_validate(args)
        except ValidationError:
            # 不回显完整Pydantic错误中的原始输入，避免把身份、密钥等注入文本写入错误。
            raise ChatError("TOOL_ARGUMENT_INVALID", "工具参数缺失或格式不正确，请补充必填字段和明确时区。", 422) from None
        if name == "create_draft":
            raise ChatError("CONFIRMATION_REQUIRED", "创建草稿前必须展示预览并等待员工确认。", 409)
        return await getattr(self, "_" + name)(parsed, user, run_id)

    async def _authorize(self, db, user: dict, venue_id: str, write=False):
        """每个工具重新联查真实用户及显式业务授权；admin角色不自动产生业务权限。"""
        row = await (await db.execute("""SELECT u.department_id,g.can_create_draft
            FROM users u JOIN agent_business_grants g ON g.user_id=u.id
            WHERE u.id=? AND u.is_active=1 AND u.password_hash IS NOT NULL
            AND g.venue_id=? AND g.is_active=1 AND g.department_id=u.department_id""",
            (user["id"], venue_id))).fetchone()
        if row is None or (write and not row["can_create_draft"]):
            raise ChatError("BUSINESS_FORBIDDEN", "当前账号没有该场馆的业务访问或草稿权限。", 403)
        return row

    async def _venue(self, db, venue_id):
        venue = await (await db.execute("SELECT * FROM agent_business_venues WHERE id=?", (venue_id,))).fetchone()
        if venue is None:
            raise ChatError("BUSINESS_NOT_READY", "场馆基础资料尚未配置，请联系管理员。", 503)
        return venue

    async def _resource(self, db, candidate):
        row = await (await db.execute("SELECT * FROM agent_business_resources WHERE id=? AND venue_id=?",
                                     (candidate.resource_id, candidate.venue_id))).fetchone()
        if row is None:
            raise ChatError("RESOURCE_NOT_FOUND", "资源不存在或不属于该场馆，请先查询资源目录。", 404)
        return row

    async def _baseline(self, db, candidate: Candidate, user: dict, run_id: str, require_read=False):
        if candidate.request_type != "booking_change":
            return None
        row = await (await db.execute("SELECT * FROM agent_business_bookings WHERE id=? AND owner_user_id=?",
                                     (candidate.original_booking_id, user["id"]))).fetchone()
        if row is None or row["venue_id"] != candidate.venue_id:
            raise ChatError("BOOKING_NOT_FOUND", "原排期不存在或无权变更该排期。", 404)
        if row["version"] != candidate.expected_booking_version or row["status"] != "approved":
            raise ChatError("BASELINE_CHANGED", "原排期版本或状态已改变，请重新读取并校验。", 409)
        if require_read:
            read = await (await db.execute("""SELECT * FROM agent_business_booking_reads
                WHERE owner_user_id=? AND run_id=? AND booking_id=? AND expires_at>?""",
                (user["id"], run_id, row["id"], stamp()))).fetchone()
            if read is None or read["booking_version"] != row["version"]:
                raise ChatError("BASELINE_READ_REQUIRED", "变更前必须先读取当前原排期及版本。", 409)
        return row

    async def _assessment(self, db, candidate: Candidate, user: dict, run_id: str):
        """确定性容量/排他冲突计算。返回对方占用时间，不泄露他人的标题、姓名及编号。"""
        resource = await self._resource(db, candidate)
        await self._baseline(db, candidate, user, run_id)
        overlaps = await (await db.execute("""SELECT start_at,end_at FROM agent_business_bookings
            WHERE resource_id=? AND status='approved' AND start_at<? AND end_at>?
            AND (? IS NULL OR id<>?) ORDER BY start_at LIMIT 20""", (
            candidate.resource_id, candidate.end_at, candidate.start_at,
            candidate.original_booking_id, candidate.original_booking_id))).fetchall()
        issues = []
        if candidate.attendees > resource["capacity"]:
            issues.append("申请人数超过当前资源容量。")
        if overlaps:
            issues.append("该资源在申请时段存在已批准占用；需要调整时间后重新查询。")
        return resource, {"status": "CONFLICT" if issues else "AVAILABLE", "issues": issues,
                          "capacity": resource["capacity"], "conflicts": [dict(r) for r in overlaps],
                          "scope_label": "虚构演示排期，不代表企业正式档期"}

    async def _search_policy(self, args: SearchInput, user, run_id):
        async with self.store.connect() as db:
            await self._authorize(db, user, args.venue_id)
            venue = dict(await self._venue(db, args.venue_id))
            resources = [dict(r) for r in await (await db.execute(
                "SELECT id,name,capacity FROM agent_business_resources WHERE venue_id=? ORDER BY id",
                (args.venue_id,))).fetchall()]
        if self.retriever is None:
            raise ChatError("KNOWLEDGE_NOT_READY", "制度检索尚未就绪，请联系管理员。", 503)
        # CPU向量检索不占用事件循环；异步注入适配器则直接await。
        if inspect.iscoroutinefunction(self.retriever):
            hits = await self.retriever(args.query)
        else:
            hits = await asyncio.to_thread(self.retriever, args.query)
            if inspect.isawaitable(hits):
                hits = await hits
        if not hits:
            raise ChatError("RETRIEVAL_EMPTY", "制度中未找到可用依据，请补充问题或人工核实。", 422)
        sources = [{"source_id": h.chunk.chunk_id, "filename": Path(h.chunk.source).name,
                    "excerpt": redact_for_llm(h.chunk.text), "start_line": h.chunk.start_line,
                    "end_line": h.chunk.end_line} for h in hits[:3]]
        identifier = str(uuid.uuid4())
        async with self.store.connect() as db:
            await self._authorize(db, user, args.venue_id)
            current = await self._venue(db, args.venue_id)
            if current["rule_version"] != venue["rule_version"]:
                raise ChatError("RULE_VERSION_CHANGED", "检索期间规则版本已变化，请重新检索。", 409)
            await db.execute("INSERT INTO agent_business_evidence VALUES (?,?,?,?,?,?,?,?)", (
                identifier, user["id"], run_id, args.venue_id, venue["rule_version"],
                self.document_version, canonical(sources), stamp() + RECEIPT_SECONDS))
            await db.commit()
        return {"policy_evidence_id": identifier, "sources": sources, "resources": resources,
                "resource_catalog": [{"resource_id": r["id"], "name": r["name"], "capacity": r["capacity"]} for r in resources],
                "rule_version": venue["rule_version"], "scope_label": "当前执行规则仅适用于显式初始化的虚构演示场景"}

    async def _get_booking_context(self, args: BookingInput, user, run_id):
        async with self.store.connect() as db:
            row = await (await db.execute("SELECT * FROM agent_business_bookings WHERE id=? AND owner_user_id=?",
                                         (args.booking_id, user["id"]))).fetchone()
            if row is None:
                raise ChatError("BOOKING_NOT_FOUND", "原排期不存在或无权访问。", 404)
            await self._authorize(db, user, row["venue_id"])
            await db.execute("""INSERT INTO agent_business_booking_reads VALUES (?,?,?,?,?)
                ON CONFLICT(owner_user_id,run_id,booking_id) DO UPDATE SET
                booking_version=excluded.booking_version,expires_at=excluded.expires_at""",
                (user["id"], run_id, row["id"], row["version"], stamp() + RECEIPT_SECONDS))
            await db.commit()
        return {"booking": dict(row), "scope_label": "虚构演示原排期，草稿不改变该基线"}

    async def _check_availability(self, args: AvailabilityInput, user, run_id):
        candidate = args.candidate
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await self._authorize(db, user, candidate.venue_id)
            venue = await self._venue(db, candidate.venue_id)
            resource, result = await self._assessment(db, candidate, user, run_id)
            identifier = str(uuid.uuid4())
            await db.execute("INSERT INTO agent_business_checks VALUES (?,?,?,?,?,?,?,?,?)", (
                identifier, user["id"], run_id, candidate.venue_id, digest(candidate.model_dump()),
                venue["revision"], resource["version"], canonical(result), stamp() + RECEIPT_SECONDS))
            await db.commit()
        return {**result, "availability_check_id": identifier, "venue_revision": venue["revision"]}

    def _rules(self, venue):
        # RAG top3只帮助解释。执行依据是另行明确配置的完整规则；不把模型提炼当作规则。
        if not self.knowledge_is_demo or not venue["approved"] or not venue["is_demo"] or venue["rule_version"] != DEMO_RULE_VERSION:
            raise ChatError("RULES_NEED_REVIEW", "尚无与当前制度匹配的已确认执行规则，请人工核实。", 409)
        try:
            rules = json.loads(venue["rules_json"])
            if set(rules) != {"exclusive", "max_duration_hours", "buffer_minutes", "invoice_required"}:
                raise ValueError()
            if (rules["exclusive"] is not True or rules["buffer_minutes"] != 0 or rules["invoice_required"] is not False
                    or type(rules["max_duration_hours"]) is not int or rules["max_duration_hours"] <= 0):
                raise ValueError()
            return rules
        except (ValueError, TypeError):
            raise ChatError("RULES_NEED_REVIEW", "执行规则缺失或当前适配器不支持，不能默认通过。", 409) from None

    async def _validate_request(self, args: ValidateInput, user, run_id):
        candidate = args.candidate
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await self._authorize(db, user, candidate.venue_id, write=True)
            venue = await self._venue(db, candidate.venue_id)
            try:
                rules = self._rules(venue)
            except ChatError as error:
                return {"status": "NEEDS_REVIEW", "issues": [error.message]}
            evidence = await (await db.execute("""SELECT * FROM agent_business_evidence
                WHERE id=? AND owner_user_id=? AND run_id=? AND venue_id=? AND expires_at>?""",
                (args.policy_evidence_id, user["id"], run_id, candidate.venue_id, stamp()))).fetchone()
            if not evidence or evidence["rule_version"] != venue["rule_version"] or evidence["document_version"] != self.document_version:
                raise ChatError("POLICY_EVIDENCE_REQUIRED", "缺少当前工作流的有效制度依据，请重新检索。", 409)
            await self._baseline(db, candidate, user, run_id, require_read=True)
            resource, result = await self._assessment(db, candidate, user, run_id)
            check = await (await db.execute("""SELECT * FROM agent_business_checks
                WHERE id=? AND owner_user_id=? AND run_id=? AND venue_id=? AND expires_at>?""",
                (args.availability_check_id, user["id"], run_id, candidate.venue_id, stamp()))).fetchone()
            if (not check or check["candidate_hash"] != digest(candidate.model_dump())
                    or check["venue_revision"] != venue["revision"] or check["resource_version"] != resource["version"]):
                raise ChatError("AVAILABILITY_CHECK_REQUIRED", "缺少同一方案的有效资源查询，或资源已变化，请重新查询。", 409)
            duration = (datetime.fromisoformat(candidate.end_at) - datetime.fromisoformat(candidate.start_at)).total_seconds()
            if duration > rules["max_duration_hours"] * 3600:
                result["issues"].append("申请时长超过当前虚构演示规则的上限。")
            if result["issues"]:
                return {"status": "CONFLICT", "issues": result["issues"]}
            identifier, created = str(uuid.uuid4()), stamp()
            data = candidate.model_dump()
            # 预览摘要包含业务内容、规则和依赖版本；后续确认必须精确绑定该摘要。
            preview_hash = digest({"candidate": data, "rule_version": venue["rule_version"],
                                   "venue_revision": venue["revision"], "document_version": self.document_version})
            await db.execute("""INSERT INTO agent_business_validations
                (id,owner_user_id,run_id,venue_id,candidate_json,candidate_hash,preview_hash,
                 venue_revision,resource_version,rule_version,document_version,evidence_id,created_at,expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                identifier, user["id"], run_id, candidate.venue_id, canonical(data), digest(data), preview_hash,
                venue["revision"], resource["version"], venue["rule_version"], self.document_version,
                evidence["id"], created, min(created + RECEIPT_SECONDS, evidence["expires_at"], check["expires_at"])))
            await db.commit()
        return {"status": "PASS", "validation_id": identifier, "candidate": data,
                "scope_label": "仅虚构演示规则校验通过，尚未创建草稿、审批或占档"}

    async def _receipt(self, db, validation_id, user, run_id):
        row = await (await db.execute("""SELECT * FROM agent_business_validations
            WHERE id=? AND owner_user_id=? AND run_id=?""", (validation_id, user["id"], run_id))).fetchone()
        if row is None or row["status"] != "PASS":
            raise ChatError("VALIDATION_REQUIRED", "缺少当前方案的通过凭据，不能创建草稿。", 409)
        await self._authorize(db, user, row["venue_id"], write=True)
        return row

    async def preview(self, validation_id: str, user: dict, run_id: str) -> dict:
        """先验证凭据再输出服务端保存的内容；模型不能自行拼一份不同内容的确认卡片。"""
        async with self.store.connect() as db:
            row = await self._receipt(db, validation_id, user, run_id)
            await self._recheck(db, row, user, run_id)
        return {"validation_id": row["id"], "preview_hash": row["preview_hash"],
                "candidate": json.loads(row["candidate_json"]), "expires_at": row["expires_at"],
                "scope_label": "虚构演示草稿；确认仅创建草稿，不会审批或占档"}

    async def _recheck(self, db, receipt, user, run_id):
        """【可复用核心】校验到执行之间的变化检测；必须在最终写事务中再次调用。"""
        if receipt["expires_at"] <= stamp():
            raise ChatError("VALIDATION_EXPIRED", "校验凭据已过期，请重新校验后确认。", 409)
        venue = await self._venue(db, receipt["venue_id"])
        self._rules(venue)
        candidate = Candidate.model_validate_json(receipt["candidate_json"])
        resource = await self._resource(db, candidate)
        if (venue["revision"] != receipt["venue_revision"] or venue["rule_version"] != receipt["rule_version"]
                or resource["version"] != receipt["resource_version"] or self.document_version != receipt["document_version"]):
            raise ChatError("VALIDATION_STALE", "制度、资源或排期版本已变化，请重新校验后确认。", 409)
        if digest(candidate.model_dump()) != receipt["candidate_hash"]:
            raise ChatError("VALIDATION_INVALID", "校验内容完整性检查失败，请重新发起申请。", 409)
        await self._baseline(db, candidate, user, run_id, require_read=True)
        _, result = await self._assessment(db, candidate, user, run_id)
        if result["issues"]:
            raise ChatError("RESOURCE_CONFLICT", "确认前资源已存在冲突，请调整后重新校验。", 409)

    async def commit(self, validation_id: str, user: dict, run_id: str, preview_hash: str) -> dict:
        """唯一草稿写入口：父级可信HTTP确认后调用，且本事务再检查已批准的工作流记录。

        同一运行最多一份草稿。重试返回同一结果；凭据消费和草稿写入原子完成。
        草稿表与已批准排期表独立，因此创建草稿不会释放旧排期或占用新资源。
        """
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await self._receipt(db, validation_id, user, run_id)
            run = await (await db.execute("""SELECT * FROM agent_runs
                WHERE id=? AND owner_user_id=?""", (run_id, user["id"]))).fetchone()
            if (run is None or run["pending_validation_id"] != validation_id or not run["confirmation_id"]
                    or run["confirmation_status"] != "approved"
                    or not secrets.compare_digest(run["preview_hash"] or "", preview_hash)
                    or not secrets.compare_digest(row["preview_hash"], preview_hash)):
                raise ChatError("CONFIRMATION_REQUIRED", "缺少员工对本次完整预览的有效确认，不能创建草稿。", 409)
            previous = await (await db.execute("SELECT * FROM agent_business_drafts WHERE run_id=?", (run_id,))).fetchone()
            if previous:
                if previous["validation_id"] != validation_id or previous["preview_hash"] != preview_hash:
                    raise ChatError("DRAFT_CONFLICT", "该工作流已经生成其他草稿，请核对已有记录。", 409)
                return self._draft_result(previous)
            if row["consumed_at"] is not None:
                raise ChatError("VALIDATION_CONSUMED", "校验凭据已被使用，请核对已有草稿。", 409)
            await self._recheck(db, row, user, run_id)
            if run["confirmation_expires_at"] is None or run["confirmation_expires_at"] <= stamp():
                raise ChatError("CONFIRMATION_EXPIRED", "确认凭据已过期，请重新预览并校验。", 409)
            identifier = str(uuid.uuid4())
            await db.execute("""INSERT INTO agent_business_drafts
                (id,owner_user_id,run_id,validation_id,candidate_json,preview_hash,created_at)
                VALUES (?,?,?,?,?,?,?)""", (identifier, user["id"], run_id, validation_id,
                row["candidate_json"], preview_hash, now()))
            await db.execute("UPDATE agent_business_validations SET consumed_at=? WHERE id=?", (stamp(), validation_id))
            saved = await (await db.execute("SELECT * FROM agent_business_drafts WHERE id=?", (identifier,))).fetchone()
            await db.commit()
        return self._draft_result(saved)

    @staticmethod
    def _draft_result(row):
        return {"draft_id": row["id"], "status": "draft", "candidate": json.loads(row["candidate_json"]),
                "version": row["version"], "scope_label": "草稿已创建，尚未提交、审批或占档"}


async def bootstrap_demo(store: Store, username: str) -> dict:
    """显式初始化虚构样本与单用户业务授权；只INSERT OR IGNORE，重跑不覆盖运行状态。

    容量100人、排他资源、最长72小时均为便于验证的虚构配置，不是企业制度推断。
    已有演示配置或授权被停用后，重跑不会自动恢复、覆盖或提升其权限。
    """
    await AgentBusiness(store).initialize()
    async with store.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        user = await (await db.execute("SELECT * FROM users WHERE login_subject=? AND is_active=1 AND password_hash IS NOT NULL",
                                      (username.strip().lower(),))).fetchone()
        if user is None:
            raise ChatError("ACCOUNT_NOT_FOUND", "真实账号不存在或已停用，请先创建账号。", 404)
        venue_id, resource_id, booking_id = user["venue_id"], f"demo-room-{user['venue_id']}", f"demo-booking-{user['id']}"
        rules = {"exclusive": True, "max_duration_hours": 72, "buffer_minutes": 0, "invoice_required": False}
        await db.execute("""INSERT OR IGNORE INTO agent_business_venues
            (id,name,rule_version,rules_json,approved,is_demo) VALUES (?,?,?,?,1,1)""",
            (venue_id, "虚构演示场馆", DEMO_RULE_VERSION, canonical(rules)))
        existing = await (await db.execute("SELECT is_demo,rule_version FROM agent_business_venues WHERE id=?",
                                            (venue_id,))).fetchone()
        # 避免演示初始化碰巧遇到同编号正式场馆时，给真实业务资料增加演示授权。
        # 被业务维护者换过规则的场馆也不能通过重跑演示命令恢复旧的演示能力。
        if not existing["is_demo"] or existing["rule_version"] != DEMO_RULE_VERSION:
            raise ChatError("DEMO_SCOPE_CONFLICT", "该场馆已配置其他业务规则，不能用演示初始化修改或授权。", 409)
        await db.execute("""INSERT OR IGNORE INTO agent_business_resources
            (id,venue_id,name,capacity) VALUES (?,?,?,100)""", (resource_id, venue_id, "虚构演示会议区"))
        await db.execute("""INSERT OR IGNORE INTO agent_business_grants
            (user_id,venue_id,department_id) VALUES (?,?,?)""", (user["id"], venue_id, user["department_id"]))
        start = (datetime.now(timezone.utc) + timedelta(days=7)).replace(hour=2, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=2)
        await db.execute("""INSERT OR IGNORE INTO agent_business_bookings
            (id,owner_user_id,venue_id,resource_id,start_at,end_at,attendees,purpose)
            VALUES (?,?,?,?,?,?,20,?)""", (booking_id, user["id"], venue_id, resource_id,
            start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), "虚构已批准样例活动"))
        await db.commit()
    return {"venue_id": venue_id, "resource_id": resource_id, "booking_id": booking_id,
            "rule_version": DEMO_RULE_VERSION, "capacity": 100,
            "scope_label": "仅用于虚构演示，不代表企业制度或正式排期"}
