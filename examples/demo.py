"""Read-only demo: validate bundled artifacts and print their repository paths."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dhls.main import main
from dhls.utils.paths import ROOT, artifact_paths

if __name__ == "__main__":
    result = main(["check"])
    if result == 0:
        for name, path in artifact_paths().items():
            print(f"{name}: {path.relative_to(ROOT).as_posix()}")
    raise SystemExit(result)
