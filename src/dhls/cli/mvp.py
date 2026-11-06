"""MVP交付入口：使用显式配置定位独立数据，复用现有服务、账号和业务工具。

不创建默认账号、审批授权或后台进程。doctor不联网、不初始化模型或数据库。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
import tomllib


@dataclass(frozen=True)
class Profile:
    directory: Path
    name: str
    runtime_dir: Path
    document: Path
    custom_document: bool = False

    @property
    def data_dir(self) -> Path:
        return self.runtime_dir / "chat"

    @property
    def rag_dir(self) -> Path:
        return self.runtime_dir / "rag"


def load_profile(directory: Path, *, runtime_dir: Path | None = None,
                 document: Path | None = None) -> Profile:
    """配置内路径相对profile所在目录；显式CLI路径相对调用方当前目录。

    CHAT_DATA_DIR/RAG_WORK_DIR/RAG_DOCUMENT不作为此入口的目录默认值，防止从
    原工程终端进入MVP时误接旧业务数据。模型、身份与超时环境配置仍复用原实现。
    """
    directory = directory.expanduser().resolve()
    try:
        content = tomllib.loads((directory / "profile.toml").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError("无法读取MVP的profile.toml，请检查目录、编码与TOML格式。") from error
    expected = {"schema_version", "name", "runtime_dir", "knowledge_document"}
    if (set(content) != expected or type(content["schema_version"]) is not int
            or content["schema_version"] != 1
            or any(not isinstance(content[key], str) or not content[key].strip()
                   for key in expected - {"schema_version"})):
        raise ValueError("MVP配置须为版本1，且仅包含name、runtime_dir和knowledge_document。")
    runtime = (runtime_dir.expanduser() if runtime_dir is not None
               else directory / content["runtime_dir"]).resolve()
    policy = (document.expanduser() if document is not None
              else directory / content["knowledge_document"]).resolve()
    try:
        text = policy.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ValueError("MVP制度文件不存在或不可读，请检查knowledge_document或--document。") from error
    if policy.suffix.lower() != ".md" or not text.strip():
        raise ValueError("MVP知识库须指定非空的UTF-8 Markdown制度。")
    return Profile(directory, content["name"], runtime, policy, document is not None)


@contextmanager
def profile_environment(profile: Profile):
    """同一进程内的服务、账号CLI与业务CLI使用完全相同的目录，结束后还原环境。"""
    values = {"CHAT_DATA_DIR": str(profile.data_dir), "RAG_WORK_DIR": str(profile.rag_dir),
              "RAG_DOCUMENT": str(profile.document)}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def profile_settings(profile: Profile):
    from dhls.core.chat import Settings

    with profile_environment(profile):
        settings = Settings.from_env()
    settings.validate()
    return settings


def _assets(profile: Profile) -> dict:
    """把交付契约与真实工具schema/轮次常量核对，而非仅检查目录名。"""
    from dhls.core.agent_business import AgentBusiness
    from dhls.core.agent_loop import AgentHarness
    from dhls.core.agent_state import MAX_ROUNDS
    from dhls.core.rag import CHUNK_SIZE, TOP_K, SAMPLE_POLICY

    contract = json.loads((profile.directory / "agent" / "contract.json").read_text(encoding="utf-8"))
    # schemas只读白名单定义，不需要实例化数据库、模型或LangGraph。
    class SchemaSource:
        business = AgentBusiness

    names = sorted(item["function"]["name"] for item in AgentHarness.schemas(SchemaSource()))
    if (contract.get("schema_version") != 1 or sorted(contract.get("tools", [])) != names
            or contract.get("max_model_rounds") != MAX_ROUNDS
            or [point.get("id") for point in contract.get("human_checkpoints", [])] != ["H1", "H2", "H3"]
            or contract.get("approval_creates_occupancy") is not False):
        raise ValueError("MVP Agent契约与当前服务不一致，请同步工具、轮次及人工节点说明。")
    policy = profile.document.read_text(encoding="utf-8-sig")
    # 与KnowledgeBase.document_version一致：按UTF-8文本规范化换行，避免Windows
    # checkout的CRLF或UTF-8 BOM被误判成制度内容变化。
    digest = hashlib.sha256(policy.encode("utf-8")).hexdigest()
    if not profile.custom_document:
        manifest = json.loads((profile.directory / "knowledge_base" / "sources.json").read_text(encoding="utf-8"))
        if (manifest.get("schema_version") != 1 or manifest.get("sha256") != digest
                or policy.strip() != SAMPLE_POLICY.strip()):
            raise ValueError("默认演示制度与来源清单或原型样例不一致，正式制度请显式使用--document。")
    return {"tools": names, "max_model_rounds": MAX_ROUNDS,
            "human_checkpoints": ["H1", "H2", "H3"], "chunk_size": CHUNK_SIZE, "top_k": TOP_K,
            "knowledge_sha256": digest, "knowledge_is_demo": policy.strip() == SAMPLE_POLICY.strip()}


def doctor(profile: Profile) -> dict:
    """无副作用的本地检查，模型配置有效不代表供应商可达或效果通过。"""
    packages = {}
    try:
        packages["dhls"] = metadata.version("dhls")
        for requirement in metadata.requires("dhls") or []:
            if "extra ==" in requirement:
                continue
            name = re.split(r"[\s<>=!~;\[(]", requirement, maxsplit=1)[0]
            try:
                packages[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                packages[name] = None
    except metadata.PackageNotFoundError:
        packages["dhls"] = None
    result = {"ok": False, "profile_dir": str(profile.directory), "runtime_dir": str(profile.runtime_dir),
              "data_dir": str(profile.data_dir), "rag_dir": str(profile.rag_dir),
              "knowledge_document": str(profile.document), "packages": packages,
              "network_requested": False, "model_connection_tested": False}
    if any(version is None for version in packages.values()):
        result["message"] = "运行依赖不完整，请从项目根目录安装python -m pip install -e .。"
        return result
    result.update(_assets(profile))
    settings = profile_settings(profile)
    try:
        settings.model_config()
        result["model_settings_valid"] = True
    except Exception:
        result["model_settings_valid"] = False
    result["ok"] = True
    result["message"] = ("本地配置与交付契约检查通过；尚未测试模型连接。" if result["model_settings_valid"]
                         else "本地配置与交付契约检查通过；生成模型尚需在后端配置。")
    return result


def main(argv: list[str] | None = None, *, profile_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="MVP独立运行入口：serve、doctor、users、demo")
    parser.add_argument("--profile-dir", type=Path, default=profile_dir or Path.cwd() / "mvp")
    parser.add_argument("--runtime-dir", type=Path, help="显式运行目录；其下分别存放chat和rag")
    parser.add_argument("--document", type=Path, help="显式指定另一份UTF-8制度；真实规则仍需人工适配")
    parser.add_argument("command", nargs="?", default="serve", choices=("serve", "doctor", "users", "demo"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    # 子命令帮助不需要读写目录或配置模型，也不提前消费用户口令。
    subparser = None
    if args.command == "doctor":
        subparser = argparse.ArgumentParser(prog="mvp doctor", description="只读检查，不联网或建数据库")
        subparser.add_argument("--json", action="store_true")
    elif args.command == "serve":
        subparser = argparse.ArgumentParser(prog="mvp serve", description="启动本机单worker服务")
        subparser.add_argument("--port", type=int, default=os.getenv("CHAT_PORT", "8000"))
    if subparser:
        command_args = subparser.parse_args(args.arguments)
        if args.command == "serve" and not 1 <= command_args.port <= 65535:
            subparser.error("端口必须在1至65535之间")
    try:
        if args.command in {"users", "demo"} and args.arguments == ["--help"]:
            if args.command == "users":
                from dhls.cli.users import main as delegate
            else:
                from dhls.cli.demo import main as delegate
            return delegate(args.arguments)
        profile = load_profile(args.profile_dir, runtime_dir=args.runtime_dir, document=args.document)
        if args.command == "doctor":
            report = doctor(profile)
            print(json.dumps(report, ensure_ascii=False, indent=2) if command_args.json else
                  f"{report['message']}\n数据：{profile.data_dir}\n检索缓存：{profile.rag_dir}\n制度：{profile.document}")
            return 0 if report["ok"] else 1
        if args.command in {"users", "demo"}:
            if args.command == "users":
                from dhls.cli.users import main as delegate
            else:
                from dhls.cli.demo import main as delegate
            with profile_environment(profile):
                return delegate(args.arguments)
        _assets(profile)
        settings = profile_settings(profile)
        from dhls.main import create_app
        import uvicorn

        print(f"MVP本机地址：http://127.0.0.1:{command_args.port}/login\n数据目录：{profile.data_dir}")
        uvicorn.run(create_app(settings), host="127.0.0.1", port=command_args.port,
                    workers=1, proxy_headers=False)
        return 0
    except (ValueError, OSError, json.JSONDecodeError):
        print("MVP配置或交付文件校验失败，请检查profile、制度、工具契约与来源清单。", file=sys.stderr)
    except ImportError:
        print("MVP依赖不可用，请从项目根目录安装python -m pip install -e .。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
