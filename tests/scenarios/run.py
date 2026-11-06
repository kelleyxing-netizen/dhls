"""执行25个量化场景并收集脱敏证据，输出必须在仓库外。

复用已有真实测试，不修改业务判断。只记录状态、事件名、计数与结果，不记录
口令、Cookie、JWT、模型密钥、完整问题或模型正文。模型与检索仍为原测试替身。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
TABLES = ("agent_business_drafts", "agent_business_bookings", "agent_approval_events", "agent_runs", "messages")
STATES = {"draft", "submitted", "approved", "returned", "queued", "running", "awaiting_confirmation",
          "completed", "rejected", "round_limit", "interrupted", "failed", "PASS", "BLOCKED", "NEEDS_REVIEW",
          "submit", "approve", "return", "cancelled", "error"}


def source_state(cases):
    """记录执行工作区的文件摘要，不能把既有未提交差异说成纯提交快照。"""
    paths = {case["nodeid"].split("::", 1)[0] for case in cases}
    paths.update({"tests/scenarios/run.py", "tests/scenarios/cases.json"})
    paths.update(str(path.relative_to(ROOT)).replace("\\", "/")
                 for path in (ROOT / "src/dhls").rglob("*.py"))
    if (ROOT / ".git").exists():
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        changed = subprocess.run(["git", "-C", str(ROOT), "diff", "HEAD", "--name-only"],
                                 capture_output=True, text=True, check=True).stdout.splitlines()
        mode = "working tree based on source_commit"
    elif (ROOT / "MVP_RELEASE.json").exists():
        manifest = json.loads((ROOT / "MVP_RELEASE.json").read_text(encoding="utf-8"))
        commit, changed = manifest["source_commit"], []
        mode = "source package based on MVP_RELEASE.json; verify release hashes separately"
    else:
        commit, changed = None, []
        mode = "unversioned source folder; no commit provenance available"
    return {"source_commit": commit, "source_mode": mode, "tracked_local_changes": changed,
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(paths)}}


def summarize(method, path, status, raw):
    """对结果用白名单摘要，绝不复制任意响应字段或请求头。"""
    result = {"method": method, "path": re.sub(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "{id}", path),
              "http_status": status, "events": [], "codes": [], "states": [], "metrics": {}}
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "code" and isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,64}", item):
                    result["codes"].append(item)
                if key in {"status", "action", "decision"} and isinstance(item, str) and item in STATES:
                    result["states"].append(item)
                if key in {"round_count", "version", "total", "days"} and type(item) is int:
                    result["metrics"].setdefault(key, []).append(item)
                if key == "items" and isinstance(item, list):
                    result["metrics"].setdefault("items_count", []).append(len(item))
                if key == "counts" and isinstance(item, dict):
                    result["metrics"]["counts"] = {k: v for k, v in item.items() if k in STATES and type(v) is int}
                # 任意字符串、正文、令牌及来源内容均不处理，递归也只提取上述白名单字段。
                if isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    try:
        text = raw.decode("utf-8")
        if text.lstrip().startswith("{"):
            walk(json.loads(text))
        else:
            for frame in text.replace("\r\n", "\n").split("\n\n"):
                name = "message"
                parts = []
                for line in frame.splitlines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        parts.append(line[5:].lstrip())
                if parts:
                    result["events"].append(name)
                    walk(json.loads("\n".join(parts)))
    except (ValueError, UnicodeError):
        result["parse_status"] = "not_json_or_complete_sse"
    result["codes"] = list(dict.fromkeys(result["codes"]))
    result["states"] = list(dict.fromkeys(result["states"]))
    return result


def snapshots(tmp, base):
    if tmp is None or not Path(tmp).resolve().is_relative_to(base):
        return []
    result = []
    for path in sorted(Path(tmp).rglob("*.sqlite3")):
        if "checkpoint" in path.name:
            continue
        record = {"database": str(path.relative_to(tmp)).replace("\\", "/"), "counts": {}}
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
                existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in TABLES:
                    if table in existing:
                        record["counts"][table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if "agent_runs" in existing:
                    record["max_round_count"] = db.execute("SELECT max(round_count) FROM agent_runs").fetchone()[0]
                    record["run_statuses"] = dict(db.execute("SELECT status,count(*) FROM agent_runs GROUP BY status"))
                if "agent_business_drafts" in existing:
                    record["draft_statuses"] = dict(db.execute("SELECT status,count(*) FROM agent_business_drafts GROUP BY status"))
            record["snapshot_status"] = "read_only"
        except sqlite3.Error:
            record["snapshot_status"] = "unavailable"
        result.append(record)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    output = args.output_dir.expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        parser.error("输出须为仓库外尚不存在的本次专用目录，避免覆盖已有数据。")
    output.mkdir(parents=True)
    import pytest
    import httpx

    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 25 and len({case["nodeid"] for case in cases}) == 25
    start = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    class Evidence:
        def __init__(self):
            self.current = None
            self.http = {}
            self.asgi = {}
            self.results = {}
            self.original = httpx.AsyncClient.send
            self.rig_class = None
            self.rig_request = None

        def pytest_configure(self, config):
            original = self.original
            collector = self
            async def send(client, request, *a, **kw):
                response = await original(client, request, *a, **kw)
                if request.url.host == "testserver" and collector.current:
                    raw = response.content if response.is_stream_consumed else b""
                    collector.http.setdefault(collector.current, []).append(
                        summarize(request.method, request.url.path, response.status_code, raw))
                return response
            httpx.AsyncClient.send = send

        def pytest_collection_finish(self, session):
            # 原聊天fixture逐帧驱动ASGI；只保存响应对象引用，不修改其时序、状态或消息。
            module = sys.modules.get("test_chat")
            if module is not None:
                self.rig_class = module.Rig
                self.rig_request = module.Rig.request
                collector = self
                original = self.rig_request
                def request(rig, *a, **kw):
                    response = original(rig, *a, **kw)
                    collector.asgi.setdefault(collector.current, []).append(response)
                    return response
                module.Rig.request = request

        def pytest_runtest_setup(self, item):
            self.current = item.nodeid

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            report = outcome.get_result()
            if report.when == "call" or (report.when == "setup" and report.failed):
                records = list(self.http.get(item.nodeid, []))
                for response in self.asgi.get(item.nodeid, []):
                    records.append(summarize(response.method, response.path, response.status, response.raw))
                self.results[item.nodeid] = {"outcome": report.outcome, "duration_seconds": round(report.duration, 4),
                    "requests": records, "database_snapshots": snapshots(item.funcargs.get("tmp_path"), output)}

        def pytest_unconfigure(self, config):
            httpx.AsyncClient.send = self.original
            if self.rig_class:
                self.rig_class.request = self.rig_request

    plugin = Evidence()
    # 使用绝对测试选择器，不依赖启动工作目录；pytest仍从项目根读取同一配置。
    selectors = [str(ROOT / case["nodeid"].split("::", 1)[0]) + "::" + case["nodeid"].split("::", 1)[1] for case in cases]
    code = pytest.main([*selectors, "-q", "-p", "no:cacheprovider", "--basetemp", str(output / "runtime"),
                        "--junitxml", str(output / "junit.xml")], plugins=[plugin])
    records = []
    for case in cases:
        result = plugin.results.get(case["nodeid"], {"outcome": "not_executed", "duration_seconds": 0,
                                                   "requests": [], "database_snapshots": []})
        records.append({**case, **result})
    result = {"schema_version": 1, "started_at_utc": start,
              "finished_at_utc": datetime.now(timezone.utc).isoformat(),
              "elapsed_seconds": round(time.perf_counter() - started, 3), "pytest_exit_code": int(code),
              "model_and_retrieval": "offline test doubles; no real model effect evaluation",
              "identity_scope": "JWT/CSRF for Agent/approval/auth; chat fixture uses fixed identity",
              "sensitive_content": "headers, credentials, question/answer bodies and source excerpts excluded",
              "cases": records}
    result.update(source_state(cases))
    file = output / "results.json"
    file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence written: {file}; SHA256={hashlib.sha256(file.read_bytes()).hexdigest()}")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
