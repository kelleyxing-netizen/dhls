"""提交、复核和审批的业务契约：真实HTTP/JWT/SQLite，模型与检索离线替身。

草稿经现有Agent人工确认入口生成；审批测试不向真实数据目录写入，不调用外部模型。
审批结果不产生资源占用，也不直接改写原排期。
"""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

import pytest

from dhls.core.auth import create_user
from dhls.core.agent_business import AgentBusiness, bootstrap_demo
from dhls.core.store import Store

# Reuse the existing real Agent HTTP fixture rather than synthesizing a passed
# validation or inserting an already-confirmed draft into each test database.
from test_agent import (TEST_PASSWORD, ScriptedDecision, agent_rig, confirm_run,
                        decode_events, get_run, login, start_run)


REVIEWER = "approval.admin"
OWNER = "agent.employee"


class ApprovalDecision(ScriptedDecision):
    """Only the original-booking read is additional to the existing Agent fake."""
    async def __call__(self, messages, tools, config, client):
        if self.plan[min(len(self.calls), len(self.plan) - 1)] == "get_booking_context":
            self.calls.append(copy.deepcopy(messages))
            return {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"approval-call-{len(self.calls)}", "type": "function",
                                "function": {"name": "get_booking_context", "arguments": json.dumps(
                                    {"booking_id": self.candidate["original_booking_id"]})}}],
            }
        return await super().__call__(messages, tools, config, client)


def ok(response, status=200):
    assert response.status_code == status, response.text
    result = response.json()
    assert result["code"] == "OK", result
    assert result["request_id"]
    assert TEST_PASSWORD not in response.text and "password_hash" not in response.text
    return result["data"]


def denied(response, status, code=None):
    assert response.status_code == status, response.text
    result = response.json()
    assert result["request_id"] and result["code"] != "OK"
    if code:
        assert result["code"] == code, result
    assert TEST_PASSWORD not in response.text and "password_hash" not in response.text
    return result


@asynccontextmanager
async def approval_rig(tmp_path, *, reviewer_grant=True):
    model = ApprovalDecision()
    async with agent_rig(tmp_path, model=model) as (app, client, model, seed):
        reviewer = await create_user(app.state.store, REVIEWER, TEST_PASSWORD,
                                     "场馆运营审核员", "admin", "operations", "east")
        await bootstrap_demo(app.state.store, REVIEWER)
        if reviewer_grant:
            async with app.state.store.connect() as db:
                await db.execute("""UPDATE agent_business_grants SET can_review_drafts=1
                    WHERE user_id=? AND venue_id='east'""", (reviewer["id"],))
                await db.commit()
        async with app.state.store.connect() as db:
            owner = dict(await (await db.execute("""SELECT id,role,department_id,venue_id
                FROM users WHERE login_subject=?""", (OWNER,))).fetchone())
        yield SimpleNamespace(app=app, client=client, model=model, seed=seed,
                              owner=owner, reviewer=reviewer)


async def make_draft(rig, *, username=OWNER, change=False):
    headers = await login(rig.client, username)
    rig.model.calls.clear()
    rig.model.plan = ["search_policy", "check_availability", "validate_request", "create_draft", "final"]
    if change:
        async with rig.app.state.store.connect() as db:
            baseline = dict(await (await db.execute("SELECT * FROM agent_business_bookings WHERE id=?",
                                                   (rig.seed["booking_id"],))).fetchone())
        rig.model.candidate.update(request_type="booking_change", original_booking_id=baseline["id"],
                                   expected_booking_version=baseline["version"],
                                   change_reason="测试讲师时间调整")
        rig.model.plan = ["search_policy", "get_booking_context", "check_availability", "validate_request", "create_draft"]
    else:
        rig.model.candidate.update(request_type="new_booking", original_booking_id=None,
                                   expected_booking_version=None, change_reason=None)
    run_id, _ = await start_run(rig.client, headers, question="离线审批测试：按完整条件准备草稿")
    pending = await get_run(rig.client, run_id)
    assert pending["status"] == "awaiting_confirmation", pending
    assert pending["draft"] is None
    decode_events(await confirm_run(rig.client, headers, run_id, pending["pending_confirmation"]))
    final = await get_run(rig.client, run_id)
    assert final["draft"] is not None, final
    detail = ok(await rig.client.get(f"/api/drafts/{final['draft']['draft_id']}"))
    assert detail["status"] == "draft"
    return detail, headers


async def submit_draft(rig, draft, headers):
    submitted = ok(await rig.client.post(f"/api/drafts/{draft['id']}/submit", headers=headers,
                                         json={"expected_version": draft["version"]}))
    assert submitted["status"] == "submitted"
    assert submitted["version"] == draft["version"] + 1
    return submitted


async def reviewer_detail(rig, draft_id):
    headers = await login(rig.client, REVIEWER)
    return ok(await rig.client.get(f"/admin/api/approvals/{draft_id}")), headers


async def decide(rig, detail, headers, decision="approve", comment="复核后通过"):
    return await rig.client.post(f"/admin/api/approvals/{detail['id']}/decision", headers=headers,
                                 json={"expected_version": detail["version"], "decision": decision,
                                       "comment": comment, "review_hash": detail.get("review_hash")})


async def booking_rows(app):
    async with app.state.store.connect() as db:
        return [dict(row) for row in await (await db.execute(
            "SELECT * FROM agent_business_bookings ORDER BY id")).fetchall()]


def test_employee_submits_and_scoped_operations_admin_approves_without_occupancy(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, employee_headers = await make_draft(rig)
            bookings = await booking_rows(rig.app)
            submitted = await submit_draft(rig, draft, employee_headers)
            assert [event["action"] for event in submitted["events"]] == ["submit"]
            detail, admin_headers = await reviewer_detail(rig, draft["id"])
            assert detail["owner"]["department_id"] == "sales"
            assert rig.reviewer["department_id"] == "operations"
            assert detail["assessment"]["status"] == "PASS"
            assert detail["assessment"]["conflicts"] == []
            assert detail["actions"]["can_approve"] is True
            assert detail["review_hash"]
            approved = ok(await decide(rig, detail, admin_headers))
            assert approved["status"] == "approved"
            assert approved["version"] == submitted["version"] + 1
            assert approved["reviewed_at"]
            assert [event["action"] for event in approved["events"]] == ["submit", "approve"]
            decision = approved["events"][-1]
            assert decision["actor"]["id"] == rig.reviewer["id"]
            assert decision["comment"] == "复核后通过"
            assert decision["from_status"] == "submitted" and decision["to_status"] == "approved"
            assert decision["snapshot"]["candidate"] == draft["candidate"]
            assert decision["snapshot"]["assessment"]["status"] == "PASS"
            assert decision["snapshot"]["draft_version"] == submitted["version"]
            assert approved["events"][0]["snapshot"]["draft_version"] == draft["version"]
            assert await booking_rows(rig.app) == bookings
            await login(rig.client, OWNER)
            own = ok(await rig.client.get(f"/api/drafts/{draft['id']}"))
            assert own["status"] == "approved" and own["latest_comment"] == "复核后通过"
            listing = ok(await rig.client.get("/api/drafts?status=approved"))
            assert [item["id"] for item in listing["items"]] == [draft["id"]]
    asyncio.run(scenario())


def test_booking_change_shows_original_and_candidate_diff_and_keeps_original_after_approval(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            bookings = await booking_rows(rig.app)
            draft, employee_headers = await make_draft(rig, change=True)
            await submit_draft(rig, draft, employee_headers)
            detail, admin_headers = await reviewer_detail(rig, draft["id"])
            assert detail["baseline"]["id"] == rig.seed["booking_id"]
            changes = {item["field"]: item for item in detail["changes"]}
            assert changes["start_at"]["before"] != changes["start_at"]["after"]
            assert changes["end_at"]["after"] == draft["candidate"]["end_at"]
            assert detail["candidate"]["change_reason"] == "测试讲师时间调整"
            approved = ok(await decide(rig, detail, admin_headers))
            assert approved["status"] == "approved"
            assert await booking_rows(rig.app) == bookings
    asyncio.run(scenario())


def test_return_requires_reason_and_applicant_sees_history_when_resubmitting(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, employee_headers = await make_draft(rig)
            await submit_draft(rig, draft, employee_headers)
            detail, admin_headers = await reviewer_detail(rig, draft["id"])
            denied(await decide(rig, detail, admin_headers, decision="return", comment="  "), 422, "INVALID_REQUEST")
            returned = ok(await decide(rig, detail, admin_headers, decision="return", comment="请补充场地使用说明"))
            assert returned["status"] == "returned"
            assert returned["latest_comment"] == "请补充场地使用说明"
            await login(rig.client, OWNER)
            own = ok(await rig.client.get(f"/api/drafts/{draft['id']}"))
            assert own["actions"]["can_submit"] is True
            assert [event["action"] for event in own["events"]] == ["submit", "return"]
            resubmit_headers = await login(rig.client, OWNER)
            resubmitted = await submit_draft(rig, own, resubmit_headers)
            assert [event["action"] for event in resubmitted["events"]] == ["submit", "return", "submit"]
            assert resubmitted["events"][1]["comment"] == "请补充场地使用说明"
    asyncio.run(scenario())


def test_unsubmitted_drafts_are_private_and_employee_cannot_read_or_submit_another_owners_draft(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig)
            assert ok(await rig.client.get("/api/drafts"))["items"][0]["id"] == draft["id"]
            admin_headers = await login(rig.client, REVIEWER)
            assert ok(await rig.client.get("/admin/api/approvals"))["items"] == []
            denied(await rig.client.get(f"/admin/api/approvals/{draft['id']}"), 404)
            denied(await rig.client.get(f"/api/drafts/{draft['id']}"), 404)
            denied(await rig.client.post(f"/api/drafts/{draft['id']}/submit", headers=admin_headers,
                                          json={"expected_version": draft["version"]}), 404)
            await login(rig.client, OWNER)
            still_private = ok(await rig.client.get(f"/api/drafts/{draft['id']}"))
            assert still_private["status"] == "draft" and still_private["events"] == []
    asyncio.run(scenario())


def test_admin_role_and_normal_business_grant_do_not_implicitly_grant_review(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path, reviewer_grant=False) as rig:
            draft, employee_headers = await make_draft(rig)
            await submit_draft(rig, draft, employee_headers)
            await login(rig.client, REVIEWER)
            listing = ok(await rig.client.get("/admin/api/approvals"))
            assert listing["items"] == [] and listing["reviewable_venues"] == []
            denied(await rig.client.get(f"/admin/api/approvals/{draft['id']}"), 404)
            await login(rig.client, OWNER)
            denied(await rig.client.get("/admin/api/approvals"), 403, "FORBIDDEN")
    asyncio.run(scenario())


def test_review_scope_does_not_expand_to_west_even_with_admin_role(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            await create_user(rig.app.state.store, "approval.west", TEST_PASSWORD,
                              "西区申请员工", "employee", "sales", "west")
            await bootstrap_demo(rig.app.state.store, "approval.west")
            rig.model.candidate.update(venue_id="west", resource_id="demo-room-west")
            draft, owner_headers = await make_draft(rig, username="approval.west")
            await submit_draft(rig, draft, owner_headers)
            await login(rig.client, REVIEWER)
            listing = ok(await rig.client.get("/admin/api/approvals"))
            assert listing["items"] == []
            assert {item["id"] for item in listing["reviewable_venues"]} == {"east"}
            denied(await rig.client.get(f"/admin/api/approvals/{draft['id']}"), 404)
    asyncio.run(scenario())


def test_reviewer_cannot_approve_own_submitted_draft(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig, username=REVIEWER)
            await submit_draft(rig, draft, headers)
            detail = ok(await rig.client.get(f"/admin/api/approvals/{draft['id']}"))
            assert detail["actions"]["can_approve"] is False
            denied(await decide(rig, detail, headers), 403)
            own = ok(await rig.client.get(f"/api/drafts/{draft['id']}"))
            assert own["status"] == "submitted"
            assert [event["action"] for event in own["events"]] == ["submit"]
    asyncio.run(scenario())


def test_submit_and_decision_reject_missing_csrf_and_unknown_privilege_fields(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig)
            path = f"/api/drafts/{draft['id']}/submit"
            denied(await rig.client.post(path, json={"expected_version": draft["version"]}), 403, "CSRF_INVALID")
            denied(await rig.client.post(path, headers=headers, json={"expected_version": draft["version"],
                       "owner_user_id": rig.reviewer["id"], "status": "approved"}), 422, "INVALID_REQUEST")
            await submit_draft(rig, draft, headers)
            detail, review_headers = await reviewer_detail(rig, draft["id"])
            decision_path = f"/admin/api/approvals/{draft['id']}/decision"
            body = {"expected_version": detail["version"], "decision": "approve", "review_hash": detail["review_hash"]}
            denied(await rig.client.post(decision_path, json=body), 403, "CSRF_INVALID")
            denied(await rig.client.post(decision_path, headers=review_headers,
                                          json={**body, "can_review_drafts": True}), 422, "INVALID_REQUEST")
            denied(await rig.client.post(decision_path, headers={**review_headers, "Origin": "https://other.example"},
                                          json=body), 403, "CSRF_INVALID")
            current = ok(await rig.client.get(f"/admin/api/approvals/{draft['id']}"))
            assert current["status"] == "submitted" and len(current["events"]) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["applicant_grant", "capacity"])
def test_submission_rechecks_changed_conditions_before_entering_review_queue(tmp_path, change):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig)
            async with rig.app.state.store.connect() as db:
                if change == "applicant_grant":
                    await db.execute("UPDATE agent_business_grants SET can_create_draft=0 WHERE user_id=?",
                                     (rig.owner["id"],))
                else:
                    await db.execute("UPDATE agent_business_resources SET capacity=10,version=version+1 WHERE id='demo-room-east'")
                await db.commit()
            denied(await rig.client.post(f"/api/drafts/{draft['id']}/submit", headers=headers,
                    json={"expected_version": draft["version"]}), 409, "SUBMISSION_BLOCKED")
            current = ok(await rig.client.get(f"/api/drafts/{draft['id']}"))
            assert current["status"] == "draft" and current["version"] == draft["version"]
            assert current["events"] == [] and current["assessment"]["status"] == "BLOCKED"
            await login(rig.client, REVIEWER)
            assert ok(await rig.client.get("/admin/api/approvals"))["items"] == []
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["applicant_grant", "capacity", "conflict", "baseline", "rules", "document"])
def test_approval_rechecks_live_business_constraints_and_allows_reasoned_return(tmp_path, change):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig, change=change == "baseline")
            await submit_draft(rig, draft, headers)
            async with rig.app.state.store.connect() as db:
                if change == "applicant_grant":
                    await db.execute("UPDATE agent_business_grants SET is_active=0 WHERE user_id=? AND venue_id='east'", (rig.owner["id"],))
                elif change == "capacity":
                    await db.execute("UPDATE agent_business_resources SET capacity=10,version=version+1 WHERE id='demo-room-east'")
                elif change == "conflict":
                    candidate = draft["candidate"]
                    await db.execute("""INSERT INTO agent_business_bookings
                        (id,owner_user_id,venue_id,resource_id,start_at,end_at,attendees,purpose)
                        VALUES (?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), rig.reviewer["id"], "east", "demo-room-east",
                            candidate["start_at"], candidate["end_at"], 5, "另一项已批准活动"))
                elif change == "baseline":
                    await db.execute("UPDATE agent_business_bookings SET version=version+1 WHERE id=?", (rig.seed["booking_id"],))
                elif change == "rules":
                    await db.execute("UPDATE agent_business_venues SET approved=0 WHERE id='east'")
                await db.commit()
            if change == "document":
                rig.app.state.agent.business.document_version = "different-current-policy-version"
            bookings = await booking_rows(rig.app)
            detail, admin_headers = await reviewer_detail(rig, draft["id"])
            assert detail["assessment"]["status"] == "BLOCKED", detail
            assert detail["assessment"]["issues"]
            assert detail["actions"]["can_approve"] is False and detail["actions"]["can_return"] is True
            denied(await decide(rig, detail, admin_headers), 409, "REVIEW_BLOCKED")
            returned = ok(await decide(rig, detail, admin_headers, decision="return", comment="当前复核条件已变化，请重新准备申请"))
            assert returned["status"] == "returned"
            assert await booking_rows(rig.app) == bookings
            assert [event["action"] for event in returned["events"]] == ["submit", "return"]
    asyncio.run(scenario())


def test_review_hash_rejects_stale_screen_after_resource_change(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig)
            await submit_draft(rig, draft, headers)
            old, admin_headers = await reviewer_detail(rig, draft["id"])
            async with rig.app.state.store.connect() as db:
                # A still-sufficient capacity is nevertheless different from the reviewed snapshot.
                await db.execute("UPDATE agent_business_resources SET capacity=99,version=version+1 WHERE id='demo-room-east'")
                await db.commit()
            denied(await decide(rig, old, admin_headers), 409, "REVIEW_STALE")
            refreshed = ok(await rig.client.get(f"/admin/api/approvals/{draft['id']}"))
            assert refreshed["assessment"]["status"] == "PASS"
            assert refreshed["review_hash"] != old["review_hash"]
            assert refreshed["version"] == old["version"]
            approved = ok(await decide(rig, refreshed, admin_headers))
            assert approved["status"] == "approved"
            assert len(approved["events"]) == 2
    asyncio.run(scenario())


def test_concurrent_submit_and_competing_decisions_have_one_winner_and_one_audit_event(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig)
            path = f"/api/drafts/{draft['id']}/submit"
            submitted = await asyncio.gather(*(rig.client.post(path, headers=headers,
                    json={"expected_version": draft["version"]}) for _ in range(2)))
            assert sorted(r.status_code for r in submitted) == [200, 409]
            denied(next(r for r in submitted if r.status_code == 409), 409, "DRAFT_VERSION_CONFLICT")
            detail, admin_headers = await reviewer_detail(rig, draft["id"])
            assert [event["action"] for event in detail["events"]] == ["submit"]
            decisions = await asyncio.gather(decide(rig, detail, admin_headers),
                                             decide(rig, detail, admin_headers, decision="return", comment="并发退回测试"))
            assert sorted(r.status_code for r in decisions) == [200, 409]
            denied(next(r for r in decisions if r.status_code == 409), 409, "DRAFT_VERSION_CONFLICT")
            latest = ok(await rig.client.get(f"/admin/api/approvals/{draft['id']}"))
            assert latest["status"] in {"approved", "returned"}
            assert len(latest["events"]) == 2
            assert latest["events"][-1]["action"] in {"approve", "return"}
            assert latest["version"] == draft["version"] + 2
    asyncio.run(scenario())


def test_revoked_review_scope_is_rechecked_at_decision_and_hides_detail(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            draft, headers = await make_draft(rig)
            await submit_draft(rig, draft, headers)
            detail, admin_headers = await reviewer_detail(rig, draft["id"])
            async with rig.app.state.store.connect() as db:
                await db.execute("UPDATE agent_business_grants SET can_review_drafts=0 WHERE user_id=?", (rig.reviewer["id"],))
                await db.commit()
            denied(await decide(rig, detail, admin_headers), 404)
            denied(await rig.client.get(f"/admin/api/approvals/{draft['id']}"), 404)
            assert ok(await rig.client.get("/admin/api/approvals"))["items"] == []
            await login(rig.client, OWNER)
            current = ok(await rig.client.get(f"/api/drafts/{draft['id']}"))
            assert current["status"] == "submitted" and len(current["events"]) == 1
    asyncio.run(scenario())


def test_anonymous_and_employee_review_pages_have_real_authentication_guards(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            for path in ("/api/drafts", "/admin/api/approvals"):
                denied(await rig.client.get(path), 401, "UNAUTHORIZED")
            for path in ("/drafts", "/admin/approvals"):
                response = await rig.client.get(path)
                assert response.status_code == 303 and response.headers["location"].startswith("/login")
            await login(rig.client, OWNER)
            denied(await rig.client.get("/admin/approvals"), 403, "FORBIDDEN")
            assert (await rig.client.get("/drafts")).status_code == 200
            await login(rig.client, REVIEWER)
            assert (await rig.client.get("/admin/approvals")).status_code == 200
    asyncio.run(scenario())


def test_legacy_draft_migration_preserves_candidate_version_and_default_denies_review(tmp_path):
    async def scenario():
        from dhls.core.approvals import ApprovalService
        store = Store(tmp_path / "legacy.sqlite3")
        await store.initialize()
        business = AgentBusiness(store)
        await business.initialize()
        user = await create_user(store, "legacy.employee", TEST_PASSWORD, "原有员工", "employee", "sales", "east")
        await bootstrap_demo(store, "legacy.employee")
        candidate = {"request_type": "new_booking", "venue_id": "east", "resource_id": "demo-room-east",
                     "start_at": "2030-11-20T06:00:00+00:00", "end_at": "2030-11-20T08:00:00+00:00",
                     "attendees": 20, "purpose": "迁移前的原草稿", "original_booking_id": None,
                     "expected_booking_version": None, "change_reason": None}
        identifier = str(uuid.uuid4())
        stored = json.dumps(candidate, ensure_ascii=False)
        async with store.connect() as db:
            await db.execute("""INSERT INTO agent_business_drafts
                (id,owner_user_id,run_id,validation_id,candidate_json,preview_hash,version,created_at)
                VALUES (?,?,?,?,?,?,7,?)""", (identifier, user["id"], str(uuid.uuid4()), str(uuid.uuid4()), stored,
                                             "a" * 64, "2026-11-04T00:00:00+00:00"))
            await db.commit()
        service = ApprovalService(store, business)
        await service.initialize()
        await service.initialize()
        async with store.connect() as db:
            row = await (await db.execute("SELECT * FROM agent_business_drafts WHERE id=?", (identifier,))).fetchone()
            assert row["candidate_json"] == stored and row["version"] == 7 and row["status"] == "draft"
            assert (await (await db.execute("SELECT COUNT(*) FROM agent_business_drafts")).fetchone())[0] == 1
            grant = await (await db.execute("SELECT can_review_drafts FROM agent_business_grants WHERE user_id=?", (user["id"],))).fetchone()
            assert grant[0] == 0
    asyncio.run(scenario())
