"""Repository-relative paths without user-specific runtime locations."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def artifact_paths(root=ROOT):
    root = Path(root).resolve()
    config = json.loads((root / "config/default.json").read_text(encoding="utf-8"))
    paths = {}
    for key, value in config.items():
        candidate = (root / value).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"Configured path escapes repository: {key}")
        paths[key] = candidate
    return paths
