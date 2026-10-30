"""已安装dhls包的虚构样本初始化入口，不提供默认账号或模型密钥。"""
import sys

from dhls.cli.demo import main

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
