"""员工提交与显式场馆审核；审批记录不等于资源占用。"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from dhls.core.agent_business import AgentBusiness, Candidate, canonical, digest
from dhls.core.chat import ChatError
from dhls.core.store import Store, now


STATUSES = {"draft", "submitted", "approved", "returned"}
STATUS_LABEL = {"draft": "草稿", "submitted": "待审核", "approved": "审核通过", "returned": "已退回"}
FIELDS = [("venue_id", "场馆"), ("resource_id", "资源"), ("start_at", "开始时间"),
          ("end_at", "结束时间"), ("attendees", "人数"), ("purpose", "用途")]


class ApprovalService:
    def __init__(self, store: Store, business: AgentBusiness):
        self.store, self.business = store, business

    async def initialize(self):
        """只在实例锁保护的启动/维护阶段迁移，既有授权默认没有审核权。"""
        async with self.store.connect() as db:
            grant_columns = {r["name"] for r in await (await db.execute("PRAGMA table_info(agent_business_grants)")).fetchall()}
            if "can_review_drafts" not in grant_columns:
                await db.execute("ALTER TABLE agent_business_grants ADD COLUMN can_review_drafts INTEGER NOT NULL DEFAULT 0 CHECK(can_review_drafts IN (0,1))")
            columns = {r["name"] for r in await (await db.execute("PRAGMA table_info(agent_business_drafts)")).fetchall()}
            for column in ("submitted_at", "reviewed_at", "approval_venue_id", "submission_json"):
                if column not in columns:
                    await db.execute(f"ALTER TABLE agent_business_drafts ADD COLUMN {column} TEXT")
            await db.executescript("""
                CREATE INDEX IF NOT EXISTS approval_draft_queue
                ON agent_business_drafts(approval_venue_id,status,submitted_at);
                CREATE TABLE IF NOT EXISTS agent_approval_events (
                    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES agent_business_drafts(id),
                    actor_user_id TEXT NOT NULL REFERENCES users(id), actor_name TEXT NOT NULL,
                    actor_role TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('submit','approve','return')),
                    from_status TEXT NOT NULL, to_status TEXT NOT NULL, draft_version INTEGER NOT NULL,
                    comment TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(draft_id,draft_version)
                );
                CREATE INDEX IF NOT EXISTS approval_events_draft ON agent_approval_events(draft_id,draft_version);
                CREATE TRIGGER IF NOT EXISTS approval_events_no_update BEFORE UPDATE ON agent_approval_events
                BEGIN SELECT RAISE(ABORT,'approval events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS approval_events_no_delete BEFORE DELETE ON agent_approval_events
                BEGIN SELECT RAISE(ABORT,'approval events are immutable'); END;
            """)
            await db.commit()

    @staticmethod
    def _candidate(row):
        try:
            return Candidate.model_validate_json(row["candidate_json"])
        except (ValidationError, ValueError, TypeError):
            raise ChatError("DRAFT_INVALID", "草稿内容无效，请重新生成草稿。", 409) from None

    async def _user(self, db, identifier):
        row = await (await db.execute("""SELECT id,display_name,role,department_id,venue_id,is_active,
            password_hash IS NOT NULL AS has_password FROM users WHERE id=?""", (identifier,))).fetchone()
        if not row or not row["is_active"] or not row["has_password"]:
            raise ChatError("BUSINESS_FORBIDDEN", "申请人账号已停用或不可用，不能继续提交或审批。", 403)
        return dict(row)

    async def _reviewer_venues(self, db, user):
        actor = await self._user(db, user["id"])
        if actor["role"] != "admin":
            raise ChatError("FORBIDDEN", "当前账号没有管理后台访问权限。", 403)
        rows = await (await db.execute("""SELECT v.id,v.name FROM agent_business_grants g
            JOIN agent_business_venues v ON v.id=g.venue_id
            WHERE g.user_id=? AND g.is_active=1 AND g.can_review_drafts=1 AND g.department_id=? ORDER BY v.id""",
            (actor["id"], actor["department_id"]))).fetchall()
        return [dict(row) for row in rows]

    async def _row(self, db, user, draft_id, reviewer=False):
        row = await (await db.execute("""SELECT d.*,u.display_name AS owner_name,
            u.department_id AS owner_department,u.venue_id AS owner_venue FROM agent_business_drafts d
            JOIN users u ON u.id=d.owner_user_id WHERE d.id=?""", (draft_id,))).fetchone()
        if reviewer:
            venues = {v["id"] for v in await self._reviewer_venues(db, user)}
            allowed = (row and row["status"] in {"submitted", "approved", "returned"}
                       and row["approval_venue_id"] in venues
                       and self._candidate(row).venue_id == row["approval_venue_id"])
        else:
            allowed = row and row["owner_user_id"] == user["id"]
        if not allowed:
            raise ChatError("DRAFT_NOT_FOUND", "草稿不存在或无权访问。", 404)
        return row

    def _summary(self, row, latest_comment=""):
        candidate = self._candidate(row).model_dump()
        return {"id": row["id"], "draft_id": row["id"], "status": row["status"],
                "status_label": STATUS_LABEL.get(row["status"], row["status"]), "version": row["version"],
                "request_type": candidate["request_type"], "candidate": candidate,
                "owner": {"id": row["owner_user_id"], "display_name": row["owner_name"],
                          "department_id": row["owner_department"], "venue_id": row["owner_venue"]},
                "created_at": row["created_at"], "submitted_at": row["submitted_at"],
                "reviewed_at": row["reviewed_at"], "latest_comment": latest_comment,
                "scope_label": "审核只记录通过或退回，不占档、不修改原已批准排期。"}

    async def _assessment(self, db, row):
        candidate = self._candidate(row)
        issues, conflicts, versions = [], [], {}
        baseline, capacity = None, None
        actor = None

        def issue(code, message):
            if not any(item["code"] == code for item in issues):
                issues.append({"code": code, "message": message})

        try:
            actor = await self._user(db, row["owner_user_id"])
            await self.business._authorize(db, actor, candidate.venue_id, write=True)
        except ChatError as error:
            issue(error.code, error.message)
        grant = await (await db.execute("""SELECT department_id,is_active,can_create_draft FROM agent_business_grants
            WHERE user_id=? AND venue_id=?""", (row["owner_user_id"], candidate.venue_id))).fetchone()
        versions["applicant"] = ({"department_id": actor["department_id"], "active": actor["is_active"]} if actor else None)
        versions["applicant_grant"] = dict(grant) if grant else None
        venue = await (await db.execute("SELECT * FROM agent_business_venues WHERE id=?", (candidate.venue_id,))).fetchone()
        rules = None
        if venue:
            versions.update(venue_revision=venue["revision"], rule_version=venue["rule_version"],
                            document_version=self.business.document_version)
            try:
                rules = self.business._rules(venue)
            except ChatError as error:
                issue(error.code, error.message)
        else:
            issue("BUSINESS_NOT_READY", "场馆基础资料尚未配置，不能提交或审批。")

        validation = await (await db.execute("""SELECT candidate_json,document_version,rule_version FROM agent_business_validations
            WHERE id=? AND owner_user_id=? AND run_id=?""",
            (row["validation_id"], row["owner_user_id"], row["run_id"]))).fetchone()
        if (not validation or digest(json.loads(validation["candidate_json"])) != digest(candidate.model_dump())):
            issue("DRAFT_INVALID", "原草稿校验内容不一致，请重新生成草稿。")
        elif (validation["document_version"] != self.business.document_version or
              venue and validation["rule_version"] != venue["rule_version"]):
            issue("POLICY_CHANGED", "草稿依据的制度或规则已变化，请重新生成并校验草稿。")

        resource = await (await db.execute("SELECT * FROM agent_business_resources WHERE id=? AND venue_id=?",
                                          (candidate.resource_id, candidate.venue_id))).fetchone()
        if resource:
            capacity = resource["capacity"]
            versions["resource_version"] = resource["version"]
            versions["resource_capacity"] = capacity
            if candidate.attendees > capacity:
                issue("CAPACITY_EXCEEDED", "申请人数超过当前资源容量。")
            overlapping = await (await db.execute("""SELECT start_at,end_at FROM agent_business_bookings
                WHERE resource_id=? AND status='approved' AND start_at<? AND end_at>?
                AND (? IS NULL OR id<>?) ORDER BY start_at LIMIT 20""",
                (candidate.resource_id, candidate.end_at, candidate.start_at,
                 candidate.original_booking_id, candidate.original_booking_id))).fetchall()
            conflicts = [dict(item) for item in overlapping]
            if conflicts:
                issue("RESOURCE_CONFLICT", "当前资源已有重叠的已批准占用，请调整方案后重新申请。")
        else:
            issue("RESOURCE_NOT_FOUND", "资源不存在或不属于申请场馆。")

        if candidate.request_type == "booking_change":
            baseline_row = await (await db.execute("""SELECT id,version,status,venue_id,resource_id,start_at,end_at,attendees,purpose
                FROM agent_business_bookings WHERE id=? AND owner_user_id=?""",
                (candidate.original_booking_id, row["owner_user_id"]))).fetchone()
            if baseline_row and baseline_row["venue_id"] == candidate.venue_id:
                baseline = dict(baseline_row)
                versions["baseline_version"] = baseline["version"]
                versions["baseline_status"] = baseline["status"]
                if baseline["version"] != candidate.expected_booking_version or baseline["status"] != "approved":
                    issue("BASELINE_CHANGED", "原排期版本或状态已变化，请重新读取原排期并生成变更草稿。")
            else:
                issue("BOOKING_NOT_FOUND", "原排期不存在或不属于申请人。")
        if rules:
            hours = (datetime.fromisoformat(candidate.end_at) - datetime.fromisoformat(candidate.start_at)).total_seconds() / 3600
            if hours > rules["max_duration_hours"]:
                issue("DURATION_EXCEEDED", "申请时长超过当前已确认演示规则的上限。")
        assessment = {"status": "BLOCKED" if issues else "PASS", "issues": issues,
                      "capacity": capacity, "conflicts": conflicts, "versions": versions}
        return assessment, baseline

    @staticmethod
    def _snapshot(row, assessment, baseline):
        return {"candidate": json.loads(row["candidate_json"]), "baseline": baseline,
                "assessment": assessment, "draft_version": row["version"],
                "preview_hash": row["preview_hash"], "checked_at": now()}

    @staticmethod
    def _review_hash(row, assessment):
        return digest({"draft_id": row["id"], "version": row["version"], "status": row["status"],
                       "candidate": json.loads(row["candidate_json"]), "submission": row["submission_json"],
                       "assessment": assessment})

    async def _events(self, db, draft_id):
        rows = await (await db.execute("SELECT * FROM agent_approval_events WHERE draft_id=? ORDER BY draft_version", (draft_id,))).fetchall()
        return [{"id": r["id"], "action": r["action"], "actor": {"id": r["actor_user_id"],
                 "display_name": r["actor_name"], "role": r["actor_role"]}, "from_status": r["from_status"],
                 "to_status": r["to_status"], "version": r["draft_version"], "comment": r["comment"],
                 "created_at": r["created_at"], "snapshot": json.loads(r["snapshot_json"])} for r in rows]

    async def list_drafts(self, user, status=None, limit=50, reviewer=False):
        if status is not None and status not in STATUSES:
            raise ChatError("INVALID_REQUEST", "草稿状态不正确。", 422)
        async with self.store.connect() as db:
            await db.execute("BEGIN")
            venues = await self._reviewer_venues(db, user) if reviewer else []
            if reviewer:
                if not venues:
                    return {"items": [], "reviewable_venues": []}
                placeholders = ",".join("?" for _ in venues)
                scope = f"d.approval_venue_id IN ({placeholders}) AND d.status IN ('submitted','approved','returned')"
                args = [v["id"] for v in venues]
            else:
                scope, args = "d.owner_user_id=?", [user["id"]]
            if status:
                scope += " AND d.status=?"
                args.append(status)
            args.append(min(100, max(1, int(limit))))
            rows = await (await db.execute(f"""SELECT d.*,u.display_name AS owner_name,u.department_id AS owner_department,
                u.venue_id AS owner_venue,(SELECT e.comment FROM agent_approval_events e WHERE e.draft_id=d.id
                AND e.action IN ('approve','return') ORDER BY e.draft_version DESC LIMIT 1) AS latest_comment
                FROM agent_business_drafts d JOIN users u ON u.id=d.owner_user_id WHERE {scope}
                ORDER BY COALESCE(d.submitted_at,d.created_at) DESC,d.id DESC LIMIT ?""", args)).fetchall()
            return {"items": [self._summary(r, r["latest_comment"] or "") for r in rows], "reviewable_venues": venues}

    async def detail(self, user, draft_id, reviewer=False):
        async with self.store.connect() as db:
            await db.execute("BEGIN")
            row = await self._row(db, user, draft_id, reviewer)
            assessment, current_baseline = await self._assessment(db, row)
            submitted = json.loads(row["submission_json"]) if row["submission_json"] else None
            baseline = submitted["baseline"] if submitted else current_baseline
            candidate = self._candidate(row).model_dump()
            changes = [{"field": field, "label": label, "before": baseline.get(field) if baseline else None,
                        "after": candidate[field]} for field, label in FIELDS
                       if not baseline or baseline.get(field) != candidate[field]]
            events = await self._events(db, draft_id)
            latest_comment = next((e["comment"] for e in reversed(events) if e["action"] in {"approve", "return"}), "")
            result = self._summary(row, latest_comment)
            is_pending = row["status"] == "submitted"
            not_self = row["owner_user_id"] != user["id"]
            result.update(baseline=baseline, changes=changes, assessment=assessment,
                          review_hash=self._review_hash(row, assessment), events=events,
                          actions={"can_submit": not reviewer and row["status"] in {"draft", "returned"} and assessment["status"] == "PASS",
                                   "can_approve": reviewer and is_pending and not_self and assessment["status"] == "PASS",
                                   "can_return": reviewer and is_pending and not_self})
            return result

    async def overview(self, user, days=7):
        """同一只读快照内汇总全部授权申请；状态按份数，趋势按事件次数。"""
        if days not in {7, 30}:
            raise ChatError("INVALID_REQUEST", "趋势范围仅支持最近7天或30天。", 422)
        stamp = now()
        today = datetime.fromisoformat(stamp).astimezone(timezone(timedelta(hours=8))).date()
        first = today - timedelta(days=days - 1)
        counts = {status: 0 for status in ("submitted", "approved", "returned")}
        daily = {(first + timedelta(days=i)).isoformat(): {"submitted": 0, "processed": 0}
                 for i in range(days)}
        async with self.store.connect() as db:
            await db.execute("BEGIN")
            venues = await self._reviewer_venues(db, user)
            if venues:
                placeholders = ",".join("?" for _ in venues)
                scope = f"d.approval_venue_id IN ({placeholders}) AND d.status IN ('submitted','approved','returned')"
                args = [venue["id"] for venue in venues]
                rows = await (await db.execute(f"""SELECT d.status,COUNT(*) AS total
                    FROM agent_business_drafts d WHERE {scope} GROUP BY d.status""", args)).fetchall()
                for row in rows:
                    counts[row["status"]] = row["total"]
                rows = await (await db.execute(f"""SELECT date(e.created_at,'+8 hours') AS day,
                    SUM(CASE WHEN e.action='submit' THEN 1 ELSE 0 END) AS submitted,
                    SUM(CASE WHEN e.action IN ('approve','return') THEN 1 ELSE 0 END) AS processed
                    FROM agent_approval_events e JOIN agent_business_drafts d ON d.id=e.draft_id
                    WHERE {scope} AND date(e.created_at,'+8 hours') BETWEEN ? AND ?
                    AND julianday(e.created_at)<=julianday(?) GROUP BY day ORDER BY day""",
                    [*args, first.isoformat(), today.isoformat(), stamp])).fetchall()
                for row in rows:
                    if row["day"] in daily:
                        daily[row["day"]] = {"submitted": row["submitted"], "processed": row["processed"]}
        return {"counts": counts, "total": sum(counts.values()), "days": days,
                "date_from": first.isoformat(), "date_to": today.isoformat(),
                "timezone": "Asia/Shanghai", "generated_at": stamp,
                "trend": [{"date": day, **values} for day, values in daily.items()],
                "reviewable_venues": venues}

    @staticmethod
    def _expected(row, expected_version):
        if type(expected_version) is not int or row["version"] != expected_version:
            raise ChatError("DRAFT_VERSION_CONFLICT", "申请版本已变化，请刷新后重试。", 409)

    async def _event(self, db, row, actor, action, target, version, comment, snapshot):
        await db.execute("""INSERT INTO agent_approval_events
            (id,draft_id,actor_user_id,actor_name,actor_role,action,from_status,to_status,draft_version,comment,snapshot_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), row["id"], actor["id"], actor["display_name"],
            actor["role"], action, row["status"], target, version, comment, canonical(snapshot), now()))

    async def submit(self, user, draft_id, expected_version):
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await self._row(db, user, draft_id)
            self._expected(row, expected_version)
            if row["status"] not in {"draft", "returned"}:
                raise ChatError("DRAFT_STATE_CONFLICT", "只有未提交或已退回的草稿可以提交审核。", 409)
            assessment, baseline = await self._assessment(db, row)
            if assessment["status"] != "PASS":
                raise ChatError("SUBMISSION_BLOCKED", "当前方案未通过重新核验，请查看问题并重新生成草稿。", 409)
            actor = await self._user(db, user["id"])
            snapshot = self._snapshot(row, assessment, baseline)
            candidate = self._candidate(row)
            version, timestamp = row["version"] + 1, now()
            cursor = await db.execute("""UPDATE agent_business_drafts SET status='submitted',version=?,submitted_at=?,
                reviewed_at=NULL,approval_venue_id=?,submission_json=? WHERE id=? AND version=? AND status IN ('draft','returned')""",
                (version, timestamp, candidate.venue_id, canonical(snapshot), draft_id, expected_version))
            if cursor.rowcount != 1:
                raise ChatError("DRAFT_VERSION_CONFLICT", "申请版本已变化，请刷新后重试。", 409)
            await self._event(db, row, actor, "submit", "submitted", version, "", snapshot)
            await db.commit()
        return await self.detail(user, draft_id)

    async def decide(self, user, draft_id, expected_version, decision, comment="", review_hash=None):
        comment = comment.strip()
        if decision not in {"approve", "return"} or len(comment) > 2000 or decision == "return" and not comment:
            raise ChatError("INVALID_REQUEST", "请选择通过或退回；退回时必须填写原因。", 422)
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await self._row(db, user, draft_id, reviewer=True)
            if row["owner_user_id"] == user["id"]:
                raise ChatError("SELF_REVIEW_FORBIDDEN", "不能审核本人提交的申请，请由其他有权管理员处理。", 403)
            self._expected(row, expected_version)
            if row["status"] != "submitted":
                raise ChatError("DRAFT_STATE_CONFLICT", "申请已处理或尚未提交，请刷新状态。", 409)
            assessment, baseline = await self._assessment(db, row)
            if decision == "approve":
                if not isinstance(review_hash, str) or not secrets.compare_digest(self._review_hash(row, assessment), review_hash):
                    raise ChatError("REVIEW_STALE", "复核结果或申请内容已变化，请刷新详情后重新核对。", 409)
                if assessment["status"] != "PASS":
                    raise ChatError("REVIEW_BLOCKED", "当前权限、制度或资源复核未通过，不能批准，请查看问题或退回申请。", 409)
            actor = await self._user(db, user["id"])
            target, version = ("approved" if decision == "approve" else "returned"), row["version"] + 1
            cursor = await db.execute("""UPDATE agent_business_drafts SET status=?,version=?,reviewed_at=?
                WHERE id=? AND version=? AND status='submitted'""", (target, version, now(), draft_id, expected_version))
            if cursor.rowcount != 1:
                raise ChatError("DRAFT_VERSION_CONFLICT", "申请版本已变化，请刷新后重试。", 409)
            await self._event(db, row, actor, decision, target, version, comment, self._snapshot(row, assessment, baseline))
            await db.commit()
        return await self.detail(user, draft_id, reviewer=True)

    async def grant_review(self, username, venue_id):
        """仅本机运维调用：授予已有业务范围的管理员明确审核权。"""
        async with self.store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            actor = await (await db.execute("""SELECT id,role,department_id,is_active,password_hash IS NOT NULL AS has_password
                FROM users WHERE login_subject=?""", (username.strip().lower(),))).fetchone()
            if not actor or actor["role"] != "admin" or not actor["is_active"] or not actor["has_password"]:
                raise ChatError("INVALID_REVIEWER", "须指定已启用的真实管理员账号。", 422)
            cursor = await db.execute("""UPDATE agent_business_grants SET can_review_drafts=1
                WHERE user_id=? AND venue_id=? AND department_id=? AND is_active=1""",
                (actor["id"], venue_id, actor["department_id"]))
            if cursor.rowcount != 1:
                raise ChatError("BUSINESS_FORBIDDEN", "请先为该管理员开通匹配当前部门的场馆业务授权。", 403)
            await db.commit()
        return {"username": username.strip().lower(), "venue_id": venue_id, "can_review_drafts": True}
