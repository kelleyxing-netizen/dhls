"""Agent HTTP/SSE边界：JWT/CSRF、工作流所有权、幂等及员工显式确认。

确认接口属于员工页面，绝不加入模型工具列表；浏览器只能确认服务端已保存的
原始预览摘要，不能顺便修改候选内容。修改需求应发起新的查询与校验流程。
"""
from __future__ import annotations

import uuid

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from chat_auth import current_user, public_user
from chat_core import ChatError


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, value):
        return value.strip() if isinstance(value, str) else value


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_id: uuid.UUID
    preview_hash: str = Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")
    approved: StrictBool


def install_agent_routes(app, templates, response_class, heartbeat_stream, sse):
    @app.get("/agent")
    async def agent_page(request: Request, user=Depends(current_user)):
        return templates.TemplateResponse(request=request, name="agent.html", context={
            "user": public_user(user), "csrf_token": user["csrf_token"]})

    def response(request, user, run_id, iterator, execution_id=None):
        async def cleanup():
            if not execution_id:
                return
            harness = app.state.agent
            row = await harness.state.owned(user["id"], run_id)
            # 旧预览流关闭不能取消后来新发起的确认流；只清理本次执行令牌。
            if row["status"] == "running" and row["execution_id"] == execution_id:
                await harness.state.interrupt_execution(run_id, execution_id)
                await harness.recover(user["id"], run_id)

        async def frames():
            async for item in iterator:
                yield sse(item["event"], request.state.request_id, item.get("data"),
                          item.get("message", "正在处理"), item.get("code", "OK"))
        return response_class(heartbeat_stream(frames(), app.state.settings.heartbeat_seconds), cleanup)

    async def replay(harness, user, run_id):
        row = await harness.state.owned(user["id"], run_id)
        yield {"event": "meta", "data": {"run_id": run_id, "round_count": row["round_count"], "max_rounds": 5}}
        if row["status"] == "awaiting_confirmation":
            async for item in harness.preview_events(user["id"], run_id):
                yield item
        else:
            if row["answer"]:
                yield {"event": "delta", "data": {"text": row["answer"]}}
            yield {"event": "done", "data": {"status": row["status"]}}

    @app.post("/agent/runs")
    async def start_agent(body: AgentInput, request: Request, user=Depends(current_user)):
        try:
            key = str(uuid.UUID(request.headers.get("Idempotency-Key", "")))
        except ValueError:
            raise ChatError("INVALID_REQUEST", "缺少有效的请求编号，请重新发起。", 422) from None
        harness = app.state.agent
        app.state.settings.model_config()
        row = await harness.state.prepare(user["id"], body.question, key)
        row = await harness.recover(user["id"], row["id"])
        if row["status"] == "running":
            raise ChatError("RUN_BUSY", "本次需求正在执行，请查询状态。", 409)
        if row["status"] not in {"queued", "interrupted"}:
            return response(request, user, row["id"], replay(harness, user, row["id"]))
        token = await harness.state.claim(user["id"], row["id"])
        return response(request, user, row["id"], harness.stream(user, row["id"]), token)

    @app.get("/agent/runs/{run_id}")
    async def agent_status(run_id: uuid.UUID, request: Request, user=Depends(current_user)):
        harness = app.state.agent
        await harness.recover(user["id"], str(run_id))
        data = await harness.state.public(user["id"], str(run_id))
        return {"code": "OK", "message": "执行状态已读取", "request_id": request.state.request_id, "data": data}

    @app.post("/agent/runs/{run_id}/confirm")
    async def confirm_agent(run_id: uuid.UUID, body: ConfirmationInput, request: Request, user=Depends(current_user)):
        harness, identifier = app.state.agent, str(run_id)
        await harness.recover(user["id"], identifier)
        execute = await harness.state.confirm(user["id"], identifier, str(body.confirmation_id), body.preview_hash, body.approved)
        row = await harness.state.owned(user["id"], identifier)
        if not execute:
            return response(request, user, identifier, replay(harness, user, identifier))
        # 员工决定独立留痕；日志不依赖模型是否会在随后总结中准确复述。
        await harness.state.log_start(identifier, row["round_count"], "confirmation", row["confirmation_id"], "create_draft",
                                     {"approved": body.approved, "preview_hash": body.preview_hash})
        await harness.state.log_finish(identifier, row["round_count"], "confirmation", 0,
            "员工确认创建草稿。" if body.approved else "员工拒绝创建草稿。", call_id=row["confirmation_id"])
        return response(request, user, identifier, harness.stream(user, identifier, resume=True), row["execution_id"])
