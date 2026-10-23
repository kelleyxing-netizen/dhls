"""Run the retained research builders from the repository root."""

import runpy
from dhls.utils.paths import ROOT


def build_report():
    (ROOT / ".research/work").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs/reports").mkdir(parents=True, exist_ok=True)
    runpy.run_path(str(ROOT / "scripts/build_report.py"), run_name="__main__")
    runpy.run_path(str(ROOT / "scripts/optimize_report.py"), run_name="__main__")


def build_flow():
    (ROOT / ".research").mkdir(exist_ok=True)
    (ROOT / "docs/assets").mkdir(parents=True, exist_ok=True)
    runpy.run_path(str(ROOT / "scripts/draw_interview_flow.py"), run_name="__main__")
