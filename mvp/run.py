"""从任意工作目录运行本MVP配置；先在项目根目录安装dhls包。"""
from pathlib import Path
import sys


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        from dhls.cli.mvp import main
    except ModuleNotFoundError:
        print("请先在项目根目录运行python -m pip install -e .，再执行mvp/run.py。", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main(profile_dir=Path(__file__).resolve().parent))
