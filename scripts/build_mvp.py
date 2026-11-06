"""从已提交HEAD导出可运行MVP源码包，或离线核验解压文件的SHA256。

只读Git对象，不复制工作区、数据库或模型；不联网，不部署。所有临时输出放仓库外。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED


ROOT_FILES = {"LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "pyproject.toml", "requirements.txt",
              "MANIFEST.in", ".gitignore"}
DOC_FILES = {"design.md", "api.md", "Agent执行循环说明.md", "草稿与审批说明.md",
             "管理后台第1批说明.md", "员工问答接口说明.md", "evaluation.md", "prelaunch-checklist.md"}
BLOCKED = {".git", ".runtime", ".chat_data", ".stage1_rag", ".venv", "venv", "env", "__pycache__",
           "node_modules", ".pytest_cache", "dist", "build"}
REQUIRED = {"pyproject.toml", "requirements.txt", "LICENSE", "src/dhls/__init__.py",
            "mvp/run.py", "mvp/profile.toml", "mvp/README.md", "mvp/architecture.md",
            "mvp/agent/contract.json", "mvp/knowledge_base/policy.md", "mvp/knowledge_base/sources.json",
            "mvp/scenarios/acceptance.md", "mvp/tests/test_profile.py", "mvp/tests/test_human_workflow.py",
            "src/dhls/cli/mvp.py", "src/dhls/main.py", "scripts/build_mvp.py", "mvp/package-readme.md"}


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (bool(name) and not path.is_absolute() and "\\" not in name and ":" not in name
            and all(part not in {".", ".."} for part in name.split("/")))


def allowed(name: str) -> bool:
    if not safe_name(name):
        return False
    path = PurePosixPath(name)
    if any(part.lower() in BLOCKED or part.lower().startswith(".env") for part in path.parts):
        return False
    if name in ROOT_FILES or name == "mvp/.gitignore":
        return True
    if path.parts[0] == "src" and name.startswith("src/dhls/"):
        return path.suffix in {".py", ".css", ".js", ".html"}
    if path.parts[0] == "mvp":
        return path.suffix in {".py", ".md", ".toml", ".json"}
    if path.parts[0] in {"scripts", "examples", "tests"}:
        return path.suffix in {".py", ".ps1", ".md"}
    if path.parts[0] == "config":
        return path.suffix in {".toml", ".md"}
    if path.parts[0] == "data":
        return path.name in {"sample_policy.md", "evaluation_cases.json", "README.md"}
    return len(path.parts) == 2 and path.parts[0] == "docs" and path.name in DOC_FILES


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("无法读取本地Git已提交对象，请确认Git可用且项目已有提交。")
    return result.stdout


def build(repo: Path, output: Path) -> dict:
    commit = git(repo, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    entries = {}
    for item in git(repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not item:
            continue
        header, encoded_name = item.split(b"\t", 1)
        name = encoded_name.decode("utf-8")
        if not allowed(name):
            continue
        mode, kind, oid = header.decode("ascii").split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("MVP包不接收符号链接或子模块，请使用已提交普通文件。")
        entries[name] = oid
    if REQUIRED - entries.keys():
        raise ValueError("当前HEAD缺少MVP交付文件，请先提交本次MVP源码和配置，再构建。")
    payload = {}
    provenance = {}
    for name, oid in sorted(entries.items()):
        payload[name] = git(repo, "cat-file", "blob", oid)
        provenance[name] = name
    payload["README.md"] = payload["mvp/package-readme.md"].replace(b"](../", b"](")
    provenance["README.md"] = "mvp/package-readme.md（仅调整相对链接）"
    # 精简包的文档索引只指向实际随包提供的当前说明，不携带历史报告与旧工具归档。
    doc_index = "# MVP运行文档\n\n本包仅保留当前运行说明，历史研究见原GitHub仓库。\n\n"
    for name in sorted(DOC_FILES):
        doc_index += f"- [{name}]({name})\n"
    payload["docs/README.md"] = doc_index.encode("utf-8")
    provenance["docs/README.md"] = "scripts/build_mvp.py生成的精简索引"
    manifest = {"schema_version": 1, "source_commit": commit, "source": "committed HEAD",
                "working_tree_changes_included": False,
                "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(),
                           "bytes": len(data), "source_path": provenance[name]}
                          for name, data in sorted(payload.items())]}
    payload["MVP_RELEASE.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # x模式拒绝覆盖已有包，包括检查与创建之间的竞争。
    with ZipFile(output, "x", ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            archive.writestr("dhls-mvp/" + name, data)
    return manifest


def verify(directory: Path) -> int:
    directory = directory.expanduser().resolve()
    manifest = json.loads((directory / "MVP_RELEASE.json").read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("working_tree_changes_included") is not False
            or not isinstance(manifest.get("files"), list) or not manifest["files"]):
        raise ValueError("交付包来源清单格式不正确。")
    names = set()
    for record in manifest["files"]:
        name = record["path"]
        if not safe_name(name) or name in names:
            raise ValueError("交付包清单存在重复或越界路径。")
        names.add(name)
        target = (directory / name).resolve()
        if not target.is_relative_to(directory):
            raise ValueError("交付文件路径越界。")
        content = target.read_bytes()
        if len(content) != record["bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError("交付文件内容与来源清单不一致。")
    if REQUIRED - names:
        raise ValueError("交付包缺少必要MVP文件。")
    return len(names)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="构建已提交MVP源码包或核验解压目录，不部署、不联网")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path, help="新的ZIP输出路径，不覆盖已有文件")
    group.add_argument("--verify", type=Path, help="MVP_RELEASE.json所在的解压根目录")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            count = verify(args.verify)
            print(f"交付内容核验通过：{count}个文件；未检查依赖安装、模型效果或生产验收。")
        else:
            result = build(Path(__file__).resolve().parents[1], args.output)
            print(f"MVP源码包已生成；来源提交{result['source_commit']}，共{len(result['files'])}个文件。")
        return 0
    except FileExistsError:
        print("输出文件已存在，请选择新的包路径；原包未覆盖。", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print("MVP包构建或核验失败，请检查已提交文件、路径、清单和Git环境。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
