"""显式初始化Agent虚构业务样本；执行前停止使用同一数据目录的服务。

本命令不保存模型密钥、不调用模型，也不会创建账号或修改已存在的规则与授权。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from dhls.core.agent_business import bootstrap_demo
from dhls.core.chat import ChatError, Settings
from dhls.core.store import Store


async def run(args):
    from dhls.utils.locks import InstanceLock

    settings = Settings.from_env()
    settings.validate()
    lock = InstanceLock(settings.data_dir / "server.lock")
    lock.acquire()
    try:
        store = Store(settings.data_dir / "chat.sqlite3")
        await store.initialize()
        result = await bootstrap_demo(store, args.username)
        print("虚构样本已初始化；重复运行不会覆盖已有业务状态或恢复已撤销授权。")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent虚构业务样本初始化，非企业正式配置")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("init-demo", help="为已有真实账号创建虚构资源、排期、规则和显式业务授权")
    demo.add_argument("--username", required=True)
    try:
        asyncio.run(run(parser.parse_args()))
        return 0
    except ChatError as error:
        print(f"{error.code}：{error.message}", file=sys.stderr)
    except RuntimeError:
        print("操作未完成：请先停止同一数据目录的聊天服务。", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("操作已取消。", file=sys.stderr)
    except Exception:
        print("初始化失败，请检查依赖、目录权限及数据库状态。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
