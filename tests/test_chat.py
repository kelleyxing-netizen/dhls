"""阶段 2 的离线契约测试：假检索、假模型、临时 SQLite，无网络或模型下载。

运行时把 pytest 的 --basetemp 指到仓库外的 stage2-runtime，避免留下业务数据。
测试直接驱动 ASGI 以观察逐帧输出和断开连接，不依赖会缓冲完整响应的客户端。
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from stage1_rag import Chunk, SearchHit


class FakeRetriever:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.hits = [SearchHit(
            Chunk("S1", "fixture-policy.md", 0, 19, 1, 2, "已批准排期变更需要重新提交申请。"),
            0.03, 1.0, 0.8,
        )]

    def __call__(self, question):
        self.calls.append(question)
        if self.failure:
            raise self.failure
        return self.hits


class BlockedAnswer:
    def __init__(self):
        self.release = asyncio.Event()
        self.closed = asyncio.Event()


class FakeModel:
    def __init__(self, plans=None):
        self.calls = []
        self.plans = list(plans or [])

    async def __call__(self, messages, *args, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        plan = self.plans.pop(0) if self.plans else ["需要重新申请", "。[S1]"]
        if isinstance(plan, BlockedAnswer):
            try:
                yield "尚未完成的片段"
                await plan.release.wait()
                yield "，需要重新申请。[S1]"
            finally:
                plan.closed.set()
            return
        if isinstance(plan, Exception):
            raise plan
        for part in plan:
            if isinstance(part, Exception):
                raise part
            yield part


@dataclass
class Event:
    name: str
    data: Any


class ASGIRequest:
    """可以在生成期间观察响应并主动断开的单个本地 HTTP 请求。"""

    def __init__(self, app, method="POST", path="/chat", body=None, headers=None):
        self.app = app
        self.method = method
        self.path = path
        self.body = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.status = None
        self.response_headers = {}
        self.parts = []
        self.changed = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.sent_body = False
        self.task = None

    async def receive(self):
        if not self.sent_body:
            self.sent_body = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.response_headers = {
                key.decode(): value.decode() for key, value in message["headers"]
            }
        elif message["type"] == "http.response.body":
            self.parts.append(message.get("body", b""))
        self.changed.set()

    def start(self):
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "scheme": "http", "method": self.method,
            "path": self.path, "raw_path": self.path.encode(), "query_string": b"",
            "root_path": "", "server": ("test", 80), "client": ("127.0.0.1", 12345),
            "headers": [(key.lower().encode(), str(value).encode())
                        for key, value in self.headers.items()],
        }
        self.task = asyncio.create_task(self.app(scope, self.receive, self.send))
        return self

    @property
    def raw(self):
        return b"".join(self.parts)

    def json(self):
        return json.loads(self.raw)

    @property
    def events(self):
        events = []
        for frame in self.raw.decode().replace("\r\n", "\n").split("\n\n"):
            if not frame.strip():
                continue
            name = "message"
            data = []
            for line in frame.splitlines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
            if data:
                events.append(Event(name, json.loads("\n".join(data))))
        return events

    async def until(self, predicate):
        async with asyncio.timeout(5):
            while True:
                self.changed.clear()
                if predicate(self):
                    return self
                if self.task.done():
                    await self.task
                    raise AssertionError("请求已结束，但没有观察到预期事件")
                await self.changed.wait()

    async def finish(self):
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 5)
        except Exception:
            # Starlette 在按约定返回 500 后仍会向 ASGI server 重新抛出异常。
            if self.status != 500:
                raise
        return self

    async def disconnect(self):
        self.disconnected.set()
        await self.finish()

    async def close(self):
        if self.task and not self.task.done():
            self.disconnected.set()
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task


def event_names(response):
    return [event.name for event in response.events]


def assert_error(response, *, status=None, code=None):
    if status is not None:
        assert response.status == status
        payload = response.json()
    else:
        names = event_names(response)
        assert names.count("error") == 1
        assert "done" not in names
        assert "sources" not in names
        assert names[-1] == "error"
        payload = response.events[-1].data
    if "error" in payload:
        payload = payload["error"]
    assert isinstance(payload["request_id"], str) and payload["request_id"]
    assert isinstance(payload["code"], str)
    assert isinstance(payload["message"], str)
    assert any("\u4e00" <= char <= "\u9fff" for char in payload["message"])
    if code is not None:
        assert payload["code"] == code
    return payload


USER_A = {
    "id": "11000000-0000-4000-8000-000000000001", "login_subject": "test-owner-a",
    "display_name": "测试员工甲", "department_id": "sales", "venue_id": "east",
}
USER_B = {
    "id": "22000000-0000-4000-8000-000000000002", "login_subject": "test-owner-b",
    "display_name": "测试员工乙", "department_id": "operations", "venue_id": "west",
}


def event_data(response, name):
    return [event.data["data"] for event in response.events if event.name == name]


def delta_text(response):
    return "".join(payload["text"] for payload in event_data(response, "delta"))


class Rig:
    def __init__(self, app, retriever, model):
        self.app, self.retriever, self.model = app, retriever, model
        self.requests = []

    def request(self, method="POST", path="/chat", body=None, headers=None):
        request = ASGIRequest(self.app, method, path, body, headers).start()
        self.requests.append(request)
        return request

    async def session(self):
        response = await self.request("POST", "/sessions", {"title": "测试会话"}).finish()
        assert response.status == 201, response.raw
        return response.json()["data"]["session_id"]

    def chat(self, session, question="如何变更？", key=None):
        return self.request(body={"session_id": session, "question": question},
                            headers={"Idempotency-Key": key or str(uuid.uuid4())})

    async def history(self, session, owner=USER_A):
        return await self.app.state.store.history(owner["id"], session)

    async def messages(self, session):
        response = await self.request("GET", f"/sessions/{session}/messages").finish()
        assert response.status == 200, response.raw
        return response.json()["data"]["items"]


@contextlib.asynccontextmanager
async def rig(tmp_path: Path, *, model=None, retriever=None):
    from chat_app import Settings, create_app, current_user
    from stage1_rag import LLMConfig

    model = model or FakeModel()
    retriever = retriever or FakeRetriever()
    settings = Settings(
        data_dir=tmp_path / "data", rag_dir=tmp_path / "rag",
        llm_config=LLMConfig("compatible", "http://localhost:1234/v1", "fake", "offline-test"),
    )
    app = create_app(settings, retriever=retriever, llm_stream=model)
    app.dependency_overrides[current_user] = lambda: USER_A.copy()
    test_rig = Rig(app, retriever, model)
    async with app.router.lifespan_context(app):
        try:
            yield test_rig
        finally:
            for request in test_rig.requests:
                await request.close()


def test_success_streams_before_completion_and_replays_without_duplicate_messages(tmp_path):
    async def scenario():
        blocked = BlockedAnswer()
        async with rig(tmp_path, model=FakeModel([blocked])) as test:
            session = await test.session()
            key = str(uuid.uuid4())
            response = test.chat(session, key=key)
            await response.until(lambda value: "delta" in event_names(value))
            assert response.status == 200
            assert "text/event-stream" in response.response_headers["content-type"]
            assert event_names(response)[0] == "meta"
            assert "status" in event_names(response)
            assert "done" not in event_names(response)
            assert "sources" not in event_names(response)
            assert not response.task.done()
            assert await test.history(session) == []

            blocked.release.set()
            await response.finish()
            names = event_names(response)
            assert names[-2:] == ["sources", "done"]
            assert names.count("done") == 1 and "error" not in names
            assert len({event.data["request_id"] for event in response.events}) == 1
            assert all(event.data["code"] == "OK" for event in response.events)
            assert event_data(response, "done")[0]["status"] == "completed"
            sources = event_data(response, "sources")[0]["items"]
            assert len(sources) == 1
            assert sources[0]["chunk_id"] == "S1"
            assert sources[0]["filename"] == "fixture-policy.md"
            assert sources[0]["excerpt"] == test.retriever.hits[0].chunk.text
            assert sources[0]["document_version"]
            history = await test.history(session)
            assert [message["role"] for message in history] == ["user", "assistant"]
            assert history[-1]["content"].endswith("[S1]")
            before = await test.messages(session)

            replay = await test.chat(session, key=key).finish()
            assert replay.status == 200
            assert event_names(replay)[-2:] == ["sources", "done"]
            assert delta_text(replay) == delta_text(response)
            assert event_data(replay, "sources") == event_data(response, "sources")
            assert len(test.model.calls) == 1 and len(test.retriever.calls) == 1
            assert await test.messages(session) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("parts, code", [
    (["错误来源。[S99]"], "CITATION_INVALID"),
    (["尚未完成的半段", RuntimeError("private-upstream-response")], "INTERNAL_ERROR"),
    (["   "], "LLM_EMPTY"),
])
def test_invalid_or_incomplete_answers_have_one_error_and_no_success_history(tmp_path, parts, code):
    async def scenario():
        async with rig(tmp_path, model=FakeModel([parts])) as test:
            session = await test.session()
            response = await test.chat(session).finish()
            assert response.status == 200
            assert_error(response, code=code)
            assert b"private-upstream-response" not in response.raw
            assert await test.history(session) == []
            messages = await test.messages(session)
            assert messages
            assert all(message["status"] != "completed" for message in messages
                       if message["role"] == "assistant")

    asyncio.run(scenario())


def test_context_contains_only_latest_six_complete_pairs(tmp_path):
    async def scenario():
        async with rig(tmp_path) as test:
            session = await test.session()
            for number in range(1, 9):
                test.model.plans.append([f"第{number}次回答。[S1]"])
                response = await test.chat(session, f"第{number}次问题").finish()
                assert "done" in event_names(response)
                if number == 4:
                    test.model.plans.append(["失败回答。[S99]"])
                    failed = await test.chat(session, "失败的问题").finish()
                    assert_error(failed)
            await test.chat(session, "当前问题").finish()
            sent = test.model.calls[-1]
            assert sent[0]["role"] == "system"
            assert sent[-1]["role"] == "user"
            context = sent[1:-1]
            assert len(context) == 12
            assert [message["role"] for message in context] == ["user", "assistant"] * 6
            assert [message["content"] for message in context[::2]] == [
                f"第{number}次问题" for number in range(3, 9)
            ]
            assert [message["content"] for message in context[1::2]] == [
                f"第{number}次回答。" for number in range(3, 9)
            ]
            assert "失败的问题" not in json.dumps(sent, ensure_ascii=False)

    asyncio.run(scenario())


def test_inflight_idempotency_and_session_lock_do_not_block_other_sessions(tmp_path):
    async def scenario():
        blocked = BlockedAnswer()
        async with rig(tmp_path, model=FakeModel([blocked])) as test:
            session = await test.session()
            other = await test.session()
            key = str(uuid.uuid4())
            active = test.chat(session, key=key)
            await active.until(lambda value: "delta" in event_names(value))
            for question, request_key, code in [
                ("如何变更？", key, "REQUEST_IN_PROGRESS"),
                ("不同的问题", key, "IDEMPOTENCY_CONFLICT"),
                ("如何变更？", str(uuid.uuid4()), "SESSION_BUSY"),
            ]:
                rejected = await test.chat(session, question, request_key).finish()
                assert_error(rejected, status=409, code=code)
            independent = await test.chat(other).finish()
            assert "done" in event_names(independent)
            assert len(test.model.calls) == 2

            blocked.release.set()
            await active.finish()
            changed = await test.chat(session, "不同的问题", key).finish()
            assert_error(changed, status=409, code="IDEMPOTENCY_CONFLICT")
            assert len(test.model.calls) == 2

    asyncio.run(scenario())


def test_disconnect_cancels_model_and_the_turn_never_enters_context(tmp_path):
    async def scenario():
        blocked = BlockedAnswer()
        async with rig(tmp_path, model=FakeModel([blocked])) as test:
            session = await test.session()
            response = test.chat(session, "随后断开的问题")
            await response.until(lambda value: "delta" in event_names(value))
            await response.disconnect()
            await asyncio.wait_for(blocked.closed.wait(), 2)
            assert "done" not in event_names(response)
            assert "sources" not in event_names(response)
            assert await test.history(session) == []
            messages = await test.messages(session)
            assert messages and all(message["status"] == "interrupted" for message in messages
                                    if message["role"] == "assistant")

            next_response = await test.chat(session, "连接恢复后的问题").finish()
            assert "done" in event_names(next_response)
            assert len(test.model.calls[-1]) == 2
            assert "随后断开的问题" not in json.dumps(test.model.calls[-1], ensure_ascii=False)

    asyncio.run(scenario())


def test_generation_deadline_cancels_upstream_across_sse_yields(tmp_path):
    async def scenario():
        blocked = BlockedAnswer()
        async with rig(tmp_path, model=FakeModel([blocked])) as test:
            session = await test.session()
            test.app.state.settings.generation_timeout = 0.5
            response = test.chat(session)
            await response.until(lambda value: "delta" in event_names(value))
            await response.finish()
            assert_error(response, code="LLM_TIMEOUT")
            assert blocked.closed.is_set()
            assert await test.history(session) == []

    asyncio.run(scenario())


def test_startup_recovers_pending_turn_and_same_key_retry_reuses_the_pair(tmp_path):
    async def scenario():
        key = str(uuid.uuid4())
        question = "服务重启前的问题"
        async with rig(tmp_path) as first:
            session = await first.session()
            pending = await first.app.state.store.prepare_turn(USER_A["id"], session, question, key)
            assert await first.history(session) == []
            assert not first.model.calls

        async with rig(tmp_path) as restarted:
            recovered = await restarted.messages(session)
            assistant = next(message for message in recovered if message["role"] == "assistant")
            assert assistant["status"] == "interrupted"
            assert assistant["error_code"] == "STREAM_INTERRUPTED"
            assert await restarted.history(session) == []

            retried = await restarted.chat(session, question, key).finish()
            assert "done" in event_names(retried)
            messages = await restarted.messages(session)
            assert len(messages) == 2
            assert {message["id"] for message in messages} == {
                pending["user_message_id"], pending["assistant_message_id"],
            }
            assert len(restarted.model.calls[-1]) == 2

    asyncio.run(scenario())


def test_sources_are_not_published_when_persistence_fails(tmp_path, monkeypatch):
    async def scenario():
        async with rig(tmp_path) as test:
            session = await test.session()
            original = test.app.state.store.finish_turn

            async def fail_completed(turn_id, owner_id, status, content, sources=None,
                                     error_code=None, error_message=None):
                if status == "completed":
                    raise sqlite3.OperationalError("private-database-path")
                return await original(turn_id, owner_id, status, content, sources,
                                      error_code, error_message)

            monkeypatch.setattr(test.app.state.store, "finish_turn", fail_completed)
            response = await test.chat(session).finish()
            assert_error(response, code="STORAGE_ERROR")
            assert b"private-database-path" not in response.raw
            assert await test.history(session) == []

    asyncio.run(scenario())


def test_ownership_and_http_errors_use_chinese_envelopes(tmp_path):
    async def scenario():
        from chat_app import current_user

        async with rig(tmp_path) as test:
            session = await test.session()
            test.app.dependency_overrides[current_user] = lambda: USER_B.copy()
            missing = str(uuid.uuid4())
            foreign = await test.request("GET", f"/sessions/{session}/messages").finish()
            absent = await test.request("GET", f"/sessions/{missing}/messages").finish()
            foreign_error = assert_error(foreign, status=404, code="SESSION_NOT_FOUND")
            absent_error = assert_error(absent, status=404, code="SESSION_NOT_FOUND")
            assert foreign_error["code"] == absent_error["code"]
            assert foreign_error["message"] == absent_error["message"]
            denied = await test.chat(session).finish()
            assert_error(denied, status=404, code="SESSION_NOT_FOUND")
            assert not test.model.calls and not test.retriever.calls

            for method, path, body, status, code in [
                ("POST", "/chat", {"session_id": "bad-id", "question": "问题"}, 422, "INVALID_REQUEST"),
                ("POST", "/chat", {"session_id": missing, "question": "   "}, 422, "INVALID_REQUEST"),
                ("GET", "/does-not-exist", None, 404, "NOT_FOUND"),
                ("DELETE", "/chat", None, 405, "METHOD_NOT_ALLOWED"),
            ]:
                response = await test.request(method, path, body).finish()
                assert_error(response, status=status, code=code)

            @test.app.get("/test-unexpected-error")
            async def unexpected_error():
                raise RuntimeError("private-error-details")

            response = await test.request("GET", "/test-unexpected-error").finish()
            assert_error(response, status=500, code="INTERNAL_ERROR")
            assert b"private-error-details" not in response.raw

    asyncio.run(scenario())


def upstream_frame(content=None, *, finish=None, reasoning=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta,
                                              "finish_reason": finish}]}, ensure_ascii=False) + "\n\n"


@pytest.mark.parametrize("ending, error_code", [
    (upstream_frame(finish="stop") + "data: [DONE]\n\n", None),
    (upstream_frame(finish="stop"), "LLM_INCOMPLETE"),
    (upstream_frame(finish="length") + "data: [DONE]\n\n", "LLM_INCOMPLETE"),
    ("data: {broken-json}\n\n", "LLM_PROTOCOL_ERROR"),
])
def test_real_upstream_parser_handles_utf8_fragments_and_rejects_incomplete_streams(ending, error_code):
    async def scenario():
        import httpx
        from chat_core import ChatError, stream_llm
        from stage1_rag import LLMConfig

        wire = (": heartbeat\n\n" + upstream_frame(reasoning="不应转发的推理")
                + upstream_frame("中文回答。[S1]") + ending).encode()

        class WireStream(httpx.AsyncByteStream):
            def __init__(self):
                self.closed = False

            async def __aiter__(self):
                # 刻意从UTF-8字符中间拆包，模拟真实上游的网络分片。
                for offset in range(0, len(wire), 3):
                    yield wire[offset:offset + 3]

            async def aclose(self):
                self.closed = True

        source = WireStream()
        requests = []

        async def handler(request):
            requests.append(request)
            payload = json.loads(request.content)
            assert payload["stream"] is True
            assert request.url.path == "/v1/chat/completions"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=source)

        pieces = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config = LLMConfig("compatible", "http://localhost:1234/v1", "fake", "offline-test")
            try:
                async for piece in stream_llm([{"role": "user", "content": "测试"}], config, client):
                    pieces.append(piece)
            except ChatError as error:
                assert error_code is not None
                assert error.code == error_code
                assert "broken-json" not in error.message
            else:
                assert error_code is None
        assert "".join(pieces) == "中文回答。[S1]"
        assert len(requests) == 1
        assert source.closed

    asyncio.run(scenario())
