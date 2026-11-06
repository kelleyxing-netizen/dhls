"""发布包必须来自提交，不能把工作区修改或运行数据混入可运行交付。"""
import importlib.util
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest


def test_source_package_uses_commit_snapshot_and_detects_tampering(tmp_path):
    spec = importlib.util.spec_from_file_location("mvp_package_builder",
        Path(__file__).resolve().parents[2] / "scripts/build_mvp.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    repo = tmp_path / "source"
    repo.mkdir()
    for name in builder.REQUIRED:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("committed-synthetic-test-file\n", encoding="utf-8")
    # 即使误跟踪了假运行数据或字节码目录，白名单也必须排除，不能仅依赖gitignore。
    for name in ["mvp/.runtime/chat/private.md", "mvp/knowledge_base/model.key",
                 "src/dhls/__pycache__/private.py"]:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-sentinel-not-a-real-secret", encoding="utf-8")
    empty_hooks = tmp_path / "empty-hooks"
    empty_hooks.mkdir()
    commands = [["init", "-q"], ["add", "."],
                ["-c", "user.name=Offline MVP Test", "-c", "user.email=offline@example.invalid",
                 "-c", "core.hooksPath=" + str(empty_hooks), "commit", "--no-gpg-sign", "-qm", "test snapshot"]]
    for args in commands:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    # 修改已提交文件，且另有未跟踪.py，两者都不能进入提交快照。
    (repo / "src/dhls/main.py").write_text("uncommitted-change", encoding="utf-8")
    (repo / "src/dhls/untracked.py").write_text("untracked-change", encoding="utf-8")
    package = tmp_path / "mvp.zip"
    manifest = builder.build(repo, package)
    assert manifest["working_tree_changes_included"] is False
    with ZipFile(package) as archive:
        names = archive.namelist()
        assert archive.read("dhls-mvp/src/dhls/main.py") == b"committed-synthetic-test-file\n"
        assert not any(".runtime" in name or "__pycache__" in name or name.endswith(".key") for name in names)
        assert "dhls-mvp/src/dhls/untracked.py" not in names
        archive.extractall(tmp_path / "extracted")
    folder = tmp_path / "extracted/dhls-mvp"
    assert builder.verify(folder) == len(manifest["files"])
    with pytest.raises(FileExistsError):
        builder.build(repo, package)
    (folder / "src/dhls/main.py").write_text("changed-after-extraction", encoding="utf-8")
    with pytest.raises(ValueError, match="内容"):
        builder.verify(folder)
