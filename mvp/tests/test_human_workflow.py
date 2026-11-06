"""从MVP配置进入真实服务，验证三处人工点；仅模型与检索使用离线替身。"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid

import httpx
import pytest

from dhls.cli.mvp import load_profile, profile_settings
from dhls.core.auth import create_user
from dhls.core.agent_business import bootstrap_demo
from dhls.core.rag import Chunk, LLMConfig, SearchHit
from dhls.main import create_app


def payload(response, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == "OK" and body["request_id"]
    return body["data"]


def events(response):
    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    result = []
    for frame in response.text.replace("\r\n", "\n").split("\n\n"):
        name = "message"
        parts = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                parts.append(line[5:].lstrip())
        if parts:
            record = json.loads("\n".join(parts))
            assert record["code"] == "OK", record
            result.append((name, record["data"]))
    return result


class WorkflowModel:
    """只选择工具并读取前序真实凭据，不伪造PASS、确认决定或草稿。"""
    def __init__(self):
        self.candidate = None
        self.calls = 0

    async def __call__(self, messages, tools, config, client):
        self.calls += 1
        if self.calls == 5:
            return {"role": "assistant", "content": "草稿已创建，待员工主动提交。"}
        available = {}
        for message in messages:
            if message.get("role") == "tool":
                record = json.loads(message["content"])

                def visit(item):
                    if isinstance(item, dict):
                        available.update(item)
                        for value in item.values():
                            visit(value)
                    elif isinstance(item, list):
                        for value in item:
                            visit(value)

                visit(record)
        if self.calls == 1:
            name, args = "search_policy", {"query": "申请如何校验？", "venue_id": self.candidate["venue_id"]}
        elif self.calls == 2:
            name, args = "check_availability", {"candidate": self.candidate}
        elif self.calls == 3:
            name, args = "validate_request", {"candidate": self.candidate,
                "availability_check_id": available["availability_check_id"],
                "policy_evidence_id": available["policy_evidence_id"]}
        else:
            name, args = "create_draft", {"validation_id": available["validation_id"]}
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"mvp-test-{self.calls}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}


@pytest.mark.parametrize("outcome", ["reject_preview", "approve", "return"])
def test_mvp_human_gates_control_actual_state_and_preserve_bookings(tmp_path, outcome):
    async def scenario():
        profile = load_profile(Path(__file__).resolve().parents[1], runtime_dir=tmp_path / "runtime")
        settings = profile_settings(profile)
        settings.jwt_secret = "only-for-isolated-mvp-tests-" + "x" * 40
        settings.llm_config = LLMConfig("compatible", "http://localhost:1234/v1", "offline-mvp", "offline")
        model = WorkflowModel()
        text = profile.document.read_text(encoding="utf-8")

        def search(question):
            return [SearchHit(Chunk("S1", profile.document.name, 0, len(text), 1, text.count("\n") + 1,
                                    text), .03, 1.0, .8)]

        app = create_app(settings, retriever=search, agent_decide=model)
        password = "仅用于独立MVP回归的长口令-2026"
        async with app.router.lifespan_context(app):
            app.state.agent.business.knowledge_is_demo = True
            app.state.agent.business.document_version = hashlib.sha256(text.encode()).hexdigest()
            await create_user(app.state.store, "mvp.employee", password, "测试员工", "employee", "sales", "east")
            await create_user(app.state.store, "mvp.admin", password, "测试审核员", "admin", "operations", "east")
            seed = await bootstrap_demo(app.state.store, "mvp.employee")
            await bootstrap_demo(app.state.store, "mvp.admin")
            await app.state.approvals.grant_review("mvp.admin", seed["venue_id"])
            start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)
            model.candidate = {"request_type": "new_booking", "venue_id": seed["venue_id"],
                "resource_id": seed["resource_id"], "start_at": start.isoformat(),
                "end_at": (start + timedelta(hours=2)).isoformat(), "attendees": 20,
                "purpose": "MVP离线验收活动", "original_booking_id": None,
                "expected_booking_version": None, "change_reason": None}

            async def rows(table):
                assert table in {"agent_business_drafts", "agent_business_bookings", "agent_approval_events"}
                async with app.state.store.connect() as db:
                    return [dict(row) for row in await (await db.execute(f"SELECT * FROM {table} ORDER BY id")).fetchall()]

            bookings = await rows("agent_business_bookings")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                async def login(username):
                    data = payload(await client.post("/auth/login", json={"username": username, "password": password}))
                    return {"X-CSRF-Token": data["csrf_token"]}

                employee = await login("mvp.employee")
                initial = events(await client.post("/agent/runs", json={"question": "准备完整申请，等待我确认"},
                    headers={**employee, "Idempotency-Key": str(uuid.uuid4())}))
                run_id = next(item["run_id"] for name, item in initial if name == "meta")
                names = [name for name, _ in initial]
                assert names.index("preview_start") < names.index("preview_delta") < names.index("confirmation_required")
                pending = payload(await client.get(f"/agent/runs/{run_id}"))
                assert pending["status"] == "awaiting_confirmation" and pending["round_count"] == 4
                assert pending["draft"] is None and await rows("agent_business_drafts") == []
                assert await rows("agent_approval_events") == []
                confirmation = pending["pending_confirmation"]
                events(await client.post(f"/agent/runs/{run_id}/confirm", headers=employee, json={
                    "confirmation_id": confirmation["id"], "preview_hash": confirmation["preview_hash"],
                    "approved": outcome != "reject_preview"}))
                completed = payload(await client.get(f"/agent/runs/{run_id}"))
                if outcome == "reject_preview":
                    assert completed["status"] == "rejected" and model.calls == 4
                    assert await rows("agent_business_drafts") == []
                    assert await rows("agent_approval_events") == []
                else:
                    assert len(await rows("agent_business_drafts")) == 1 and model.calls == 5
                    draft_id = completed["draft"]["draft_id"]
                    draft = payload(await client.get(f"/api/drafts/{draft_id}"))
                    assert draft["status"] == "draft" and draft["events"] == []
                    # H1之后、H2之前：审核人既看不到列表项，也不能直接读取草稿。
                    await login("mvp.admin")
                    assert payload(await client.get("/admin/api/approvals"))["items"] == []
                    assert (await client.get(f"/admin/api/approvals/{draft_id}")).status_code == 404
                    employee = await login("mvp.employee")
                    submitted = payload(await client.post(f"/api/drafts/{draft_id}/submit", headers=employee,
                                                          json={"expected_version": draft["version"]}))
                    assert submitted["status"] == "submitted"
                    assert [event["action"] for event in submitted["events"]] == ["submit"]
                    # H2不等于H3：提交后仍未通过，只有独立审核请求才能改变处理状态。
                    reviewer = await login("mvp.admin")
                    detail = payload(await client.get(f"/admin/api/approvals/{draft_id}"))
                    assert detail["status"] == "submitted" and detail["assessment"]["status"] == "PASS"
                    comment = "核对当前版本后通过" if outcome == "approve" else "退回补充用途"
                    decided = payload(await client.post(f"/admin/api/approvals/{draft_id}/decision", headers=reviewer,
                        json={"expected_version": detail["version"], "decision": outcome, "comment": comment,
                              "review_hash": detail["review_hash"]}))
                    assert decided["status"] == ("approved" if outcome == "approve" else "returned")
                    assert [event["action"] for event in decided["events"]] == ["submit", outcome]
                    await login("mvp.employee")
                    own = payload(await client.get(f"/api/drafts/{draft_id}"))
                    assert own["status"] == decided["status"] and own["latest_comment"] == comment
                assert await rows("agent_business_bookings") == bookings
                assert model.calls <= 5

    asyncio.run(scenario())
