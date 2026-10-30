"""已安装dhls包的账号运维入口；实现集中在src/dhls/cli/users.py。"""
import sys

from dhls.cli.users import main

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
