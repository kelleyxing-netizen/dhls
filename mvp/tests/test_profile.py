"""MVP入口的路径隔离、只读doctor与真实服务启动契约。"""
import json
import os
from pathlib import Path
import shutil

import pytest

from dhls.cli import mvp


PROFILE = Path(__file__).resolve().parents[1]


def copy_profile(tmp_path):
    target = tmp_path / "profile"
    shutil.copytree(PROFILE, target, ignore=shutil.ignore_patterns(".runtime", "__pycache__"))
    return target


def test_profile_is_independent_of_cwd_and_parent_business_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAT_DATA_DIR", str(tmp_path / "existing-business"))
    monkeypatch.setenv("RAG_WORK_DIR", str(tmp_path / "existing-index"))
    monkeypatch.setenv("RAG_DOCUMENT", str(tmp_path / "other-private-policy.md"))
    profile = mvp.load_profile(PROFILE)
    settings = mvp.profile_settings(profile)
    assert settings.data_dir == PROFILE / ".runtime" / "chat"
    assert settings.rag_dir == PROFILE / ".runtime" / "rag"
    assert settings.document == PROFILE / "knowledge_base" / "policy.md"
    assert os.environ["CHAT_DATA_DIR"] == str(tmp_path / "existing-business")
    assert not (tmp_path / "existing-business").exists()


def test_doctor_does_not_connect_create_runtime_or_expose_keys(tmp_path, monkeypatch, capsys):
    directory = copy_profile(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("LLM_MODEL", "offline-profile-check")
    monkeypatch.setenv("LLM_API_KEY_ENV", "MVP_TEST_MODEL_KEY")
    secret = "test-sentinel-must-not-appear-in-doctor-output"
    monkeypatch.setenv("MVP_TEST_MODEL_KEY", secret)
    monkeypatch.setenv("AUTH_JWT_SECRET", secret)

    def forbidden(*args, **kwargs):
        pytest.fail("doctor不得联网或初始化本地Embedding")

    import httpx
    from dhls.core.chat import KnowledgeBase
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(KnowledgeBase, "__init__", forbidden)
    assert mvp.main(["--profile-dir", str(directory), "doctor", "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["ok"] and report["model_settings_valid"]
    assert report["human_checkpoints"] == ["H1", "H2", "H3"]
    assert report["network_requested"] is False and report["model_connection_tested"] is False
    assert secret not in output
    assert not (directory / ".runtime").exists()


@pytest.mark.parametrize("asset", ["knowledge", "tools"])
def test_doctor_rejects_default_policy_or_tool_contract_drift_before_writes(tmp_path, asset):
    directory = copy_profile(tmp_path)
    if asset == "knowledge":
        with (directory / "knowledge_base/policy.md").open("a", encoding="utf-8") as f:
            f.write("\n这不是已经签认的制度变化。\n")
    else:
        file = directory / "agent/contract.json"
        content = json.loads(file.read_text(encoding="utf-8"))
        content["tools"].append("execute_sql")
        file.write_text(json.dumps(content), encoding="utf-8")
    assert mvp.main(["--profile-dir", str(directory), "doctor"]) == 1
    assert not (directory / ".runtime").exists()


def test_explicit_custom_policy_and_runtime_are_shared_with_account_cli(tmp_path, monkeypatch):
    from dhls.cli import users
    custom = tmp_path / "authorized-policy.md"
    custom.write_text("# 另一份获授权的测试制度\n规则仍须人工适配。", encoding="utf-8")
    previous = tmp_path / "old-data"
    runtime = tmp_path / "isolated-runtime"
    monkeypatch.setenv("CHAT_DATA_DIR", str(previous))
    seen = {}

    def delegate(argv):
        from dhls.core.chat import Settings
        seen.update(argv=argv, settings=Settings.from_env())
        return 17

    monkeypatch.setattr(users, "main", delegate)
    assert mvp.main(["--profile-dir", str(PROFILE), "--runtime-dir", str(runtime),
                     "--document", str(custom), "users", "reset-password", "--username", "example"]) == 17
    assert seen["argv"] == ["reset-password", "--username", "example"]
    assert seen["settings"].data_dir == runtime / "chat"
    assert seen["settings"].document == custom
    assert os.environ["CHAT_DATA_DIR"] == str(previous)
    assert not runtime.exists()
    profile = mvp.load_profile(PROFILE, runtime_dir=runtime, document=custom)
    assert mvp._assets(profile)["knowledge_is_demo"] is False


def test_default_policy_has_same_version_after_windows_crlf_and_bom_conversion(tmp_path):
    directory = copy_profile(tmp_path)
    policy = directory / "knowledge_base/policy.md"
    text = policy.read_text(encoding="utf-8")
    policy.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    asset = mvp._assets(mvp.load_profile(directory))
    source = json.loads((directory / "knowledge_base/sources.json").read_text(encoding="utf-8"))
    assert asset["knowledge_is_demo"] is True
    assert asset["knowledge_sha256"] == source["sha256"]


def test_serve_uses_same_profile_and_cannot_inherit_public_host_or_many_workers(tmp_path, monkeypatch):
    import uvicorn

    directory = copy_profile(tmp_path)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("WEB_CONCURRENCY", "8")
    captured = {}

    def run(app, **kwargs):
        captured.update(app=app, **kwargs)

    monkeypatch.setattr(uvicorn, "run", run)
    assert mvp.main(["--profile-dir", str(directory), "serve", "--port", "8123"]) == 0
    assert captured["host"] == "127.0.0.1" and captured["workers"] == 1
    assert captured["port"] == 8123 and captured["proxy_headers"] is False
    assert captured["app"].state.settings.data_dir == directory / ".runtime/chat"
    assert captured["app"].state.settings.document == directory / "knowledge_base/policy.md"
    assert not (directory / ".runtime").exists()
