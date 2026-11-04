"""员工草稿提交及管理员审核HTTP边界；不开放模型审批工具。"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from dhls.core.auth import current_user, public_user, require_admin


class SubmitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(gt=0)


class DecisionInput(SubmitInput):
    decision: Literal["approve", "return"]
    comment: str = Field(default="", max_length=2000)
    review_hash: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")

    @field_validator("comment")
    @classmethod
    def trim_comment(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def decision_shape(self):
        if self.decision == "return" and not self.comment:
            raise ValueError("退回时必须填写原因")
        if self.decision == "approve" and not self.review_hash:
            raise ValueError("批准时须携带所见复核摘要")
        return self


def install_approval_routes(app, templates, envelope):
    @app.get("/drafts")
    async def draft_page(request: Request, user=Depends(current_user)):
        return templates.TemplateResponse(request=request, name="drafts.html", context={
            "user": public_user(user), "csrf_token": user["csrf_token"]})

    @app.get("/admin/approvals")
    async def approval_page(request: Request, user=Depends(require_admin)):
        return templates.TemplateResponse(request=request, name="approvals.html", context={
            "user": public_user(user), "csrf_token": user["csrf_token"], "active_page": "approvals"})

    @app.get("/api/drafts")
    async def drafts(request: Request, status: Literal["draft", "submitted", "approved", "returned"] | None = None,
                     limit: int = Query(default=50, ge=1, le=100), user=Depends(current_user)):
        return envelope(data=await app.state.approvals.list_drafts(user, status=status, limit=limit), request_id=request.state.request_id)

    @app.get("/api/drafts/{draft_id}")
    async def draft_detail(draft_id: uuid.UUID, request: Request, user=Depends(current_user)):
        return envelope(data=await app.state.approvals.detail(user, str(draft_id)), request_id=request.state.request_id)

    @app.post("/api/drafts/{draft_id}/submit")
    async def submit(draft_id: uuid.UUID, body: SubmitInput, request: Request, user=Depends(current_user)):
        result = await app.state.approvals.submit(user, str(draft_id), body.expected_version)
        return envelope(message="申请已提交，等待管理员审核。", data=result, request_id=request.state.request_id)

    @app.get("/admin/api/approvals")
    async def approvals(request: Request, status: Literal["draft", "submitted", "approved", "returned"] | None = None,
                        limit: int = Query(default=50, ge=1, le=100), user=Depends(require_admin)):
        return envelope(data=await app.state.approvals.list_drafts(user, status=status, limit=limit, reviewer=True), request_id=request.state.request_id)

    @app.get("/admin/api/approvals/{draft_id}")
    async def approval_detail(draft_id: uuid.UUID, request: Request, user=Depends(require_admin)):
        return envelope(data=await app.state.approvals.detail(user, str(draft_id), reviewer=True), request_id=request.state.request_id)

    @app.post("/admin/api/approvals/{draft_id}/decision")
    async def decision(draft_id: uuid.UUID, body: DecisionInput, request: Request, user=Depends(require_admin)):
        result = await app.state.approvals.decide(user, str(draft_id), body.expected_version,
                body.decision, body.comment, body.review_hash)
        message = "审核通过；尚未占档或修改原排期。" if body.decision == "approve" else "申请已退回，原因已记录。"
        return envelope(message=message, data=result, request_id=request.state.request_id)
