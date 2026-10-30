"""Agent循环与人工确认的离线回归：真实LangGraph、SQLite、JWT，模型决策使用替身。

测试通过真实HTTP接口验证“模型提议并不等于业务执行”。草稿写入、暂停恢复、
轮数与并发约束均使用真实服务代码，不在测试中另写一套审批或业务状态机。
"""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import httpx
import pytest

from dhls.main import create_app
from dhls.core.auth import create_user
from dhls.core.chat import Settings
from dhls.core.rag import Chunk, LLMConfig, SearchHit


TEST_SECRET = "offline-agent-regression-signing-material-" + "q" * 40
TEST_PASSWORD = "只用于Agent离线验证的长口令-2026"
PRIVATE_REASONING = "这段模拟的模型私有推理不得进入业务决策日志"


def offline_search(question):
    """只替换昂贵的检索依赖；权限、规则、凭据及草稿仍走真实实现。"""
    return [SearchHit(Chunk("S1", "演示制度.md", 0, 20, 1, 1,
                            "演示制度：申请须检查时间与容量，确认后只生成草稿。"), 0.03, 1.0, 0.8)]


def decode_events(response):
    """按SSE协议取事件，避免仅凭HTTP 200就认为业务已经成功。"""
    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    events = []
    for frame in response.text.replace("\r\n", "\n").split("\n\n"):
        name, parts = "message", []
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                parts.append(line[5:].lstrip())
        if parts:
            item = json.loads("\n".join(parts))
            assert item["request_id"] and item["message"]
            events.append((name, item))
    return events


def last_event(events, name):
    values = [item["data"] for event, item in events if event == name]
    assert values, f"缺少SSE事件{name}：{[event for event, _ in events]}"
    return values[-1]


def find_value(messages, field):
    """模拟模型读取上轮工具JSON，只复制服务端真正返回的凭据编号，不捏造通过结果。"""
    def visit(value):
        if isinstance(value, dict):
            if field in value:
                return value[field]
            for child in value.values():
                result = visit(child)
                if result is not None:
                    return result
        if isinstance(value, list):
            for child in value:
                result = visit(child)
                if result is not None:
                    return result
        return None

    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        try:
            result = visit(json.loads(message["content"]))
        except (ValueError, TypeError):
            continue
        if result is not None:
            return result
    raise AssertionError(f"前序工具没有返回{field}")


class ScriptedDecision:
    """只替代LLM函数，按步骤产生真实Function Calling消息，保留每次调用供断言。"""
    def __init__(self, plan=None):
        self.plan = list(plan or ["search_policy", "check_availability", "validate_request", "create_draft", "final"])
        self.calls = []
        self.candidate = None

    async def __call__(self, messages, tools, config, client):
        self.calls.append(copy.deepcopy(messages))
        number = len(self.calls)
        step = self.plan[min(number - 1, len(self.plan) - 1)]
        if step == "final":
            return {"role": "assistant", "content": "草稿处理已结束，请以工具返回的状态为准。"}
        if step == "search_policy":
            args = {"query": "申请如何校验？", "venue_id": self.candidate["venue_id"]}
        elif step == "check_availability":
            args = {"candidate": self.candidate}
        elif step == "validate_request":
            args = {"candidate": self.candidate,
                    "availability_check_id": find_value(messages, "availability_check_id"),
                    "policy_evidence_id": find_value(messages, "policy_evidence_id")}
        elif step == "create_draft":
            args = {"validation_id": find_value(messages, "validation_id")}
        elif step == "skip_validation":
            step, args = "create_draft", {"validation_id": "invented-not-validated"}
        elif step == "invalid_arguments":
            step, args = "check_availability", {"candidate": self.candidate, "passed": True}
        elif step == "unknown_tool":
            step, args = "execute_sql", {"sql": "DROP TABLE users"}
        else:
            raise AssertionError(f"未知测试计划：{step}")
        return {"role": "assistant", "content": None,
                "reasoning_content": PRIVATE_REASONING,
                "tool_calls": [{"id": f"offline-call-{number}", "type": "function",
                                "function": {"name": step, "arguments": json.dumps(args, ensure_ascii=False)}}]}


@asynccontextmanager
async def agent_rig(tmp_path: Path, *, model=None, seed=True):
    """真实身份和演示业务授权；没有给测试客户端提供任何绕过确认的API。"""
    from dhls.core.agent_business import bootstrap_demo

    model = model or ScriptedDecision()
    settings = Settings(data_dir=tmp_path / "data", rag_dir=tmp_path / "rag", jwt_secret=TEST_SECRET,
                        llm_config=LLMConfig("compatible", "http://localhost:1234/v1", "offline-agent", "offline"))
    app = create_app(settings, retriever=offline_search, agent_decide=model)
    async with app.router.lifespan_context(app):
        # fake检索明确代表演示制度；生产入口仍默认关闭未经批准的真实规则执行。
        app.state.agent.business.knowledge_is_demo = True
        app.state.agent.business.document_version = "offline-demo-v1"
        if seed:
            await create_user(app.state.store, "agent.employee", TEST_PASSWORD, "智能体测试员工",
                              "employee", "sales", "east")
            await create_user(app.state.store, "agent.other", TEST_PASSWORD, "无业务授权的管理员",
                              "admin", "sales", "east")
            seeded = await bootstrap_demo(app.state.store, "agent.employee")
        else:
            async with app.state.store.connect() as db:
                user = await (await db.execute("SELECT * FROM users WHERE login_subject='agent.employee'")).fetchone()
            seeded = {"venue_id": user["venue_id"], "resource_id": f"demo-room-{user['venue_id']}",
                      "booking_id": f"demo-booking-{user['id']}"}
        start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)
        model.candidate = {"request_type": "new_booking", "venue_id": seeded["venue_id"],
                           "resource_id": seeded["resource_id"], "start_at": start.isoformat(),
                           "end_at": (start + timedelta(hours=2)).isoformat(), "attendees": 20,
                           "purpose": "离线回归演示活动", "original_booking_id": None,
                           "expected_booking_version": None, "change_reason": None}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            yield app, client, model, seeded


async def login(client, username="agent.employee"):
    response = await client.post("/auth/login", json={"username": username, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}


async def start_run(client, headers, *, key=None, question="请按已给条件准备申请草稿"):
    response = await client.post("/agent/runs", json={"question": question},
                                 headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())})
    events = decode_events(response)
    return last_event(events, "meta")["run_id"], events


async def get_run(client, identifier):
    response = await client.get(f"/agent/runs/{identifier}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def confirm_run(client, headers, identifier, pending, *, approved=True, preview_hash=None):
    return await client.post(f"/agent/runs/{identifier}/confirm", headers=headers,
        json={"confirmation_id": pending["id"], "preview_hash": preview_hash or pending["preview_hash"],
              "approved": approved})


async def draft_count(app):
    async with app.state.store.connect() as db:
        return (await (await db.execute("SELECT COUNT(*) FROM agent_business_drafts")).fetchone())[0]


def test_validated_draft_is_streamed_but_not_written_until_employee_confirms(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            identifier, events = await start_run(client, headers)
            names = [name for name, _ in events]
            assert names.index("preview_start") < names.index("preview_delta") < names.index("confirmation_required")
            assert last_event(events, "done")["status"] == "awaiting_confirmation"
            streamed = "".join(item["data"]["text"] for name, item in events if name == "preview_delta")
            assert model.candidate["purpose"] in streamed and model.candidate["resource_id"] in streamed
            before = await get_run(client, identifier)
            assert before["status"] == "awaiting_confirmation" and before["round_count"] == 4
            assert before["draft"] is None and await draft_count(app) == 0
            assert before["pending_confirmation"]["preview"]["candidate"] == model.candidate
            answer = await confirm_run(client, headers, identifier, before["pending_confirmation"])
            final_events = decode_events(answer)
            assert last_event(final_events, "done")["status"] == "completed"
            after = await get_run(client, identifier)
            assert after["draft"]["candidate"] == model.candidate
            assert await draft_count(app) == 1 and len(model.calls) == 5
            assert after["pending_confirmation"] is None
            # 结果真的作为tool消息回填，最后一次模型决策能读取真实草稿编号。
            assert find_value(model.calls[-1], "draft_id") == after["draft"]["draft_id"]

    asyncio.run(scenario())


def test_every_decision_and_tool_is_logged_with_parameters_duration_and_summary(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            identifier, _ = await start_run(client, headers)
            state = await get_run(client, identifier)
            await confirm_run(client, headers, identifier, state["pending_confirmation"])
            async with app.state.store.connect() as db:
                rows = await (await db.execute("SELECT * FROM agent_decision_logs WHERE run_id=? ORDER BY round_no,started_at",
                                               (identifier,))).fetchall()
            decisions = [row for row in rows if row["event_type"] == "decision"]
            assert [row["round_no"] for row in decisions] == [1, 2, 3, 4, 5]
            assert {row["tool_name"] for row in rows} >= {"search_policy", "check_availability", "validate_request", "create_draft"}
            for row in rows:
                assert row["duration_ms"] >= 0
                assert row["result_summary"]
                assert row["started_at"] and row["finished_at"]
                assert isinstance(json.loads(row["parameters_json"]), dict)
                assert PRIVATE_REASONING not in row["parameters_json"] + row["result_summary"]
            assert len((await get_run(client, identifier))["logs"]) == len(rows)

    asyncio.run(scenario())


def test_model_loop_stops_after_five_decisions_even_if_model_keeps_calling_tools(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path, model=ScriptedDecision(["search_policy"])) as (app, client, model, seed):
            headers = await login(client)
            identifier, events = await start_run(client, headers)
            assert len(model.calls) == 5
            state = await get_run(client, identifier)
            assert state["round_count"] == 5 and state["status"] == "round_limit"
            assert last_event(events, "done")["status"] == "round_limit"
            assert await draft_count(app) == 0

    asyncio.run(scenario())


def test_creation_at_fifth_decision_can_resume_but_cannot_trigger_a_sixth_decision(tmp_path):
    async def scenario():
        plan = ["search_policy", "search_policy", "check_availability", "validate_request", "create_draft"]
        async with agent_rig(tmp_path, model=ScriptedDecision(plan)) as (app, client, model, seed):
            headers = await login(client)
            identifier, _ = await start_run(client, headers)
            state = await get_run(client, identifier)
            assert state["round_count"] == 5 and state["status"] == "awaiting_confirmation"
            decode_events(await confirm_run(client, headers, identifier, state["pending_confirmation"]))
            assert await draft_count(app) == 1 and len(model.calls) == 5
            assert (await get_run(client, identifier))["round_count"] == 5

    asyncio.run(scenario())


def test_rejecting_preview_never_creates_a_draft(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            identifier, _ = await start_run(client, headers)
            state = await get_run(client, identifier)
            events = decode_events(await confirm_run(client, headers, identifier, state["pending_confirmation"], approved=False))
            assert last_event(events, "done")["status"] == "rejected"
            assert await draft_count(app) == 0 and len(model.calls) == 4

    asyncio.run(scenario())


def test_pending_interrupt_survives_restart_and_round_count_does_not_reset(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, first_model, seed):
            headers = await login(client)
            identifier, _ = await start_run(client, headers)
            first = await get_run(client, identifier)
            pending = first["pending_confirmation"]
            assert len(first_model.calls) == 4
        async with agent_rig(tmp_path, seed=False, model=ScriptedDecision(["final"])) as (app, client, resumed_model, seed):
            headers = await login(client)
            restored = await get_run(client, identifier)
            assert restored["pending_confirmation"] == pending
            decode_events(await confirm_run(client, headers, identifier, pending))
            after = await get_run(client, identifier)
            assert await draft_count(app) == 1
            assert after["round_count"] == 5 and len(resumed_model.calls) == 1
            assert find_value(resumed_model.calls[0], "draft_id") == after["draft"]["draft_id"]

    asyncio.run(scenario())


def test_duplicate_requests_and_concurrent_confirmations_create_exactly_one_draft(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            key = str(uuid.uuid4())
            identifier, _ = await start_run(client, headers, key=key)
            replayed, _ = await start_run(client, headers, key=key)
            assert replayed == identifier and len(model.calls) == 4
            conflict = await client.post("/agent/runs", headers={**headers, "Idempotency-Key": key},
                                         json={"question": "与原请求不同的新内容"})
            assert conflict.status_code == 409
            state = await get_run(client, identifier)
            responses = await asyncio.gather(*(confirm_run(client, headers, identifier, state["pending_confirmation"]) for _ in range(2)))
            assert any(response.status_code == 200 for response in responses)
            assert all(response.status_code in {200, 409} for response in responses)
            assert await draft_count(app) == 1 and len(model.calls) == 5

    asyncio.run(scenario())


def test_restart_after_confirmation_is_saved_can_resume_without_second_draft(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            identifier, _ = await start_run(client, headers)
            first = await get_run(client, identifier)
            pending = first["pending_confirmation"]
            # 精确模拟确认事务提交后、HTTP处理器尚未来得及恢复图时进程退出。
            # 不伪造approved列，直接使用真实确认接口所调用的同一持久层方法。
            user = (await client.get("/auth/me")).json()["data"]
            await app.state.agent.state.confirm(user["id"], identifier, pending["id"], pending["preview_hash"], True)
            assert await draft_count(app) == 0
        async with agent_rig(tmp_path, seed=False, model=ScriptedDecision(["final"])) as (app, client, resumed_model, seed):
            headers = await login(client)
            restored = await get_run(client, identifier)
            assert restored["round_count"] == 4
            # 复用同一次确认请求；可信决定已存库，不发新单、不重置五轮预算。
            events = decode_events(await confirm_run(client, headers, identifier, pending))
            assert last_event(events, "done")["status"] == "completed"
            assert await draft_count(app) == 1 and len(resumed_model.calls) == 1
            assert (await get_run(client, identifier))["round_count"] == 5

    asyncio.run(scenario())


def test_delayed_old_stream_cleanup_cannot_overwrite_new_confirmation_execution(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            identifier, _ = await start_run(client, headers)
            state = await get_run(client, identifier)
            user = (await client.get("/auth/me")).json()["data"]
            persistence = app.state.agent.state
            previous = await persistence.owned(user["id"], identifier)
            await persistence.confirm(user["id"], identifier, state["pending_confirmation"]["id"],
                                      state["pending_confirmation"]["preview_hash"], True)
            current = await persistence.owned(user["id"], identifier)
            assert current["execution_id"] != previous["execution_id"]
            await persistence.log_start(identifier, 5, "decision")
            # 流A已结束但清理回调迟到，此时流B已经开始确认恢复，必须保留B及其运行日志。
            await persistence.restore_waiting(identifier, previous["execution_id"])
            await persistence.interrupt_execution(identifier, previous["execution_id"])
            unchanged = await persistence.owned(user["id"], identifier)
            assert unchanged["status"] == "running" and unchanged["execution_id"] == current["execution_id"]
            async with app.state.store.connect() as db:
                log = await (await db.execute("SELECT status FROM agent_decision_logs WHERE run_id=? AND round_no=5",
                                              (identifier,))).fetchone()
            assert log["status"] == "running"
            # 只有B自身的清理能中断B；旧A也不能把B的中断状态擅自恢复成待确认。
            await persistence.interrupt_execution(identifier, current["execution_id"])
            await persistence.restore_waiting(identifier, previous["execution_id"])
            assert (await persistence.owned(user["id"], identifier))["status"] == "interrupted"
            await persistence.restore_waiting(identifier, current["execution_id"])
            assert (await persistence.owned(user["id"], identifier))["status"] == "awaiting_confirmation"
            assert await draft_count(app) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["venue_revision", "expiry", "grant"])
def test_confirmation_rechecks_stale_validation_and_current_business_permission(tmp_path, change):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            identifier, _ = await start_run(client, headers)
            state = await get_run(client, identifier)
            async with app.state.store.connect() as db:
                if change == "venue_revision":
                    await db.execute("UPDATE agent_business_venues SET revision=revision+1 WHERE id=?", (seed["venue_id"],))
                elif change == "expiry":
                    await db.execute("UPDATE agent_business_validations SET expires_at=0")
                else:
                    await db.execute("UPDATE agent_business_grants SET is_active=0")
                await db.commit()
            response = await confirm_run(client, headers, identifier, state["pending_confirmation"])
            # 可以在开流前拒绝，也可以将失败写为SSE终态；两者都不能产生草稿。
            if response.status_code == 200:
                events = decode_events(response)
                assert any(item["code"] != "OK" for _, item in events) or last_event(events, "done")["status"] == "failed"
            else:
                assert response.status_code in {403, 409, 410}
            assert await draft_count(app) == 0

    asyncio.run(scenario())


def test_owner_csrf_and_preview_hash_cannot_be_forged(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path) as (app, client, model, seed):
            headers = await login(client)
            assert (await client.post("/agent/runs", json={"question": "准备草稿"},
                    headers={"Idempotency-Key": str(uuid.uuid4())})).status_code == 403
            identifier, _ = await start_run(client, headers)
            state = await get_run(client, identifier)
            missing_csrf = await confirm_run(client, {}, identifier, state["pending_confirmation"])
            assert missing_csrf.status_code == 403
            bad_preview = await confirm_run(client, headers, identifier, state["pending_confirmation"], preview_hash="f" * 64)
            assert bad_preview.status_code in {403, 409}
            other_headers = await login(client, "agent.other")
            assert (await client.get(f"/agent/runs/{identifier}")).status_code == 404
            assert (await confirm_run(client, other_headers, identifier, state["pending_confirmation"])).status_code == 404
            assert await draft_count(app) == 0 and len(model.calls) == 4

    asyncio.run(scenario())


def test_system_admin_role_does_not_grant_business_tool_access(tmp_path):
    async def scenario():
        async with agent_rig(tmp_path, model=ScriptedDecision(["search_policy", "final"])) as (app, client, model, seed):
            headers = await login(client, "agent.other")
            # 账号同属east场馆、拥有系统管理员角色，仍须显式业务授权才能读取业务目录。
            identifier, events = await start_run(client, headers)
            result = json.loads([message for message in model.calls[-1] if message.get("role") == "tool"][-1]["content"])
            assert result["code"] == "BUSINESS_FORBIDDEN"
            assert await draft_count(app) == 0
            assert (await get_run(client, identifier))["pending_confirmation"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("attack", ["skip_validation", "invalid_arguments", "unknown_tool"])
def test_model_tool_attacks_are_returned_as_errors_and_never_write_drafts(tmp_path, attack):
    async def scenario():
        async with agent_rig(tmp_path, model=ScriptedDecision([attack, "final"])) as (app, client, model, seed):
            headers = await login(client)
            identifier, events = await start_run(client, headers)
            assert "confirmation_required" not in [name for name, _ in events]
            assert await draft_count(app) == 0
            assert len(model.calls) == 2
            tool_messages = [message for message in model.calls[-1] if message.get("role") == "tool"]
            assert tool_messages and json.loads(tool_messages[-1]["content"])["code"] != "OK"
            assert (await get_run(client, identifier))["pending_confirmation"] is None

    asyncio.run(scenario())
