"""Explicitly validate, commit and fast-forward push the current project work."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args, capture=False):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, text=True,
                          encoding="utf-8", capture_output=capture)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    branch = git("branch", "--show-current", capture=True).stdout.strip()
    if branch != "main":
        raise SystemExit("Expected main; inspect the current branch before syncing.")
    remote = git("remote", "get-url", "origin", capture=True).stdout.strip()
    if remote != "https://github.com/kelleyxing-netizen/dhls.git":
        raise SystemExit("Unexpected origin; inspect before publishing.")
    subprocess.run([sys.executable, "scripts/build.py", "check"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, check=True)
    # Fetch, but never merge, resolve conflicts, force-push or rewrite history here.
    git("fetch", "origin")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=ROOT)
    if ancestor.returncode != 0:
        raise SystemExit("Remote is ahead or diverged; inspect and reconcile before syncing.")
    status = git("status", "--short", capture=True).stdout
    print(status or "No working-tree changes.")
    if status:
        git("add", "--", "README.md", "LICENSE", "AGENTS.md", ".gitignore", ".gitattributes",
            "CHANGELOG.md", "CONTRIBUTING.md", "requirements.txt", "src", "docs", "tests",
            "examples", "scripts", "config", "data", ".github")
        git("diff", "--cached", "--stat")
        staged = git("diff", "--cached", "--name-only", capture=True).stdout
        if staged:
            git("commit", "-m", args.message)
    # Also push any existing local commit left by an earlier failed network attempt.
    git("push", "origin", "main")
    local = git("rev-parse", "HEAD", capture=True).stdout.strip()
    remote_head = git("ls-remote", "origin", "refs/heads/main", capture=True).stdout.split()[0]
    if local != remote_head:
        raise SystemExit("Push returned, but remote head differs. Inspect before reporting success.")
    print(f"Synced: {local}")


if __name__ == "__main__":
    main()
