"""图表统计契约：授权、全量统计、北京时间边界和重复操作口径。

聚合样本直接写入隔离测试库，不将其当作经过业务校验的真实审批。
"""
import asyncio
import json
import uuid

from test_approvals import OWNER, REVIEWER, approval_rig, denied, ok
from test_agent import login, start_run


async def aggregate_draft(rig, *, status="submitted", venue="east"):
    identifier = str(uuid.uuid4())
    candidate = {**rig.model.candidate, "venue_id": venue}
    async with rig.app.state.store.connect() as db:
        await db.execute("""INSERT INTO agent_business_drafts
            (id,owner_user_id,run_id,validation_id,candidate_json,preview_hash,version,
             created_at,status,submitted_at,approval_venue_id)
            VALUES (?,?,?,?,?,?,1,?,?,?,?)""",
            (identifier, rig.owner["id"], str(uuid.uuid4()), str(uuid.uuid4()), json.dumps(candidate),
             "aggregate-fixture", "2026-11-01T00:00:00+00:00", status,
             "2026-11-01T00:00:00+00:00" if status != "draft" else None, venue))
        await db.commit()
    return identifier


async def aggregate_event(rig, identifier, action, version, stamp):
    async with rig.app.state.store.connect() as db:
        await db.execute("""INSERT INTO agent_approval_events
            (id,draft_id,actor_user_id,actor_name,actor_role,action,from_status,to_status,
             draft_version,comment,snapshot_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,'','{}',?)""",
            (str(uuid.uuid4()), identifier, rig.owner["id"], "离线统计样本", "employee", action,
             "draft" if action == "submit" else "submitted",
             {"submit": "submitted", "approve": "approved", "return": "returned"}[action], version, stamp))
        await db.commit()


def test_summary_requires_admin_and_only_7_or_30_days(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            denied(await rig.client.get("/admin/api/approvals/summary"), 401)
            await login(rig.client, OWNER)
            denied(await rig.client.get("/admin/api/approvals/summary"), 403)
            await login(rig.client, REVIEWER)
            for value in ("8", "31", "abc", "0"):
                denied(await rig.client.get(f"/admin/api/approvals/summary?days={value}"), 422)
            for value in (7, 30):
                data = ok(await rig.client.get(f"/admin/api/approvals/summary?days={value}"))
                assert data["days"] == value and len(data["trend"]) == value
    asyncio.run(scenario())


def test_summary_aggregates_beyond_list_limit_and_excludes_unsubmitted_and_other_venues(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            for _ in range(53):
                await aggregate_draft(rig)
            await aggregate_draft(rig, status="approved")
            await aggregate_draft(rig, status="returned")
            await aggregate_draft(rig, status="draft")
            await aggregate_draft(rig, venue="west")
            await login(rig.client, REVIEWER)
            data = ok(await rig.client.get("/admin/api/approvals/summary"))
            assert data["counts"] == {"submitted": 53, "approved": 1, "returned": 1}
            assert data["total"] == 55
            listing = ok(await rig.client.get("/admin/api/approvals"))
            assert len(listing["items"]) == 50
            assert [venue["id"] for venue in data["reviewable_venues"]] == ["east"]
            assert all(row["submitted"] == row["processed"] == 0 for row in data["trend"])
    asyncio.run(scenario())


def test_summary_uses_beijing_dates_and_event_counts_including_resubmission(tmp_path, monkeypatch):
    monkeypatch.setattr("dhls.core.approvals.now", lambda: "2026-11-04T05:00:00.000000+00:00")
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            identifier = await aggregate_draft(rig, status="approved")
            for version, (action, stamp) in enumerate([
                ("submit", "2026-11-03T15:59:59+00:00"),
                ("return", "2026-11-03T16:00:00+00:00"),
                ("submit", "2026-11-04T00:00:00+00:00"),
                ("approve", "2026-11-04T01:00:00+00:00"),
                ("submit", "2026-10-20T00:00:00+00:00"),
                ("submit", "2026-11-04T06:00:00+00:00"),  # 晚于统计快照，不能计入。
            ], 1):
                await aggregate_event(rig, identifier, action, version, stamp)
            hidden = await aggregate_draft(rig, venue="west")
            await aggregate_event(rig, hidden, "submit", 1, "2026-11-04T01:00:00+00:00")
            await login(rig.client, REVIEWER)
            data = ok(await rig.client.get("/admin/api/approvals/summary?days=7"))
            rows = {row["date"]: row for row in data["trend"]}
            assert data["date_from"] == "2026-10-29" and data["date_to"] == "2026-11-04"
            assert rows["2026-11-03"] == {"date": "2026-11-03", "submitted": 1, "processed": 0}
            assert rows["2026-11-04"] == {"date": "2026-11-04", "submitted": 1, "processed": 2}
            assert sum(row["submitted"] for row in rows.values()) == 2
            assert data["counts"] == {"submitted": 0, "approved": 1, "returned": 0}
            month = ok(await rig.client.get("/admin/api/approvals/summary?days=30"))
            assert sum(row["submitted"] for row in month["trend"]) == 3
            assert month["timezone"] == "Asia/Shanghai"
    asyncio.run(scenario())


def test_summary_honors_current_review_grant_and_department(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            await aggregate_draft(rig)
            await login(rig.client, REVIEWER)
            assert ok(await rig.client.get("/admin/api/approvals/summary"))["total"] == 1
            async with rig.app.state.store.connect() as db:
                await db.execute("UPDATE agent_business_grants SET can_review_drafts=0 WHERE user_id=?", (rig.reviewer["id"],))
                await db.commit()
            data = ok(await rig.client.get("/admin/api/approvals/summary"))
            assert data["total"] == 0 and data["reviewable_venues"] == []
            async with rig.app.state.store.connect() as db:
                await db.execute("UPDATE agent_business_grants SET can_review_drafts=1,department_id='unmatched' WHERE user_id=?", (rig.reviewer["id"],))
                await db.commit()
            assert ok(await rig.client.get("/admin/api/approvals/summary"))["total"] == 0
    asyncio.run(scenario())


def test_summary_read_does_not_write_drafts_events_or_bookings(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            await aggregate_draft(rig)
            async def snapshot():
                async with rig.app.state.store.connect() as db:
                    return {table: [tuple(row) for row in await (await db.execute(f"SELECT * FROM {table} ORDER BY id")).fetchall()]
                            for table in ("agent_business_drafts", "agent_approval_events", "agent_business_bookings")}
            before = await snapshot()
            await login(rig.client, REVIEWER)
            ok(await rig.client.get("/admin/api/approvals/summary?days=30"))
            assert await snapshot() == before
    asyncio.run(scenario())


def test_tool_result_reports_business_outcome_for_actual_process_display(tmp_path):
    async def scenario():
        async with approval_rig(tmp_path) as rig:
            headers = await login(rig.client, OWNER)
            _, events = await start_run(rig.client, headers)
            outcomes = {item["data"]["tool_name"]: item["data"].get("result_status")
                        for event, item in events if event == "tool_result"}
            assert outcomes["check_availability"] == "AVAILABLE"
            assert outcomes["validate_request"] == "PASS"
    asyncio.run(scenario())
