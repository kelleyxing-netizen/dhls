"""Agent演示初始化与显式审核授权；执行前停止使用同一数据目录的服务。

不保存模型密钥、不调用模型。init-demo不覆盖既有规则或授权；grant-review仅开通指定管理员的场馆审核权。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from dhls.core.agent_business import AgentBusiness, bootstrap_demo
from dhls.core.approvals import ApprovalService
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
        if args.command == "init-demo":
            result = await bootstrap_demo(store, args.username)
            print("虚构样本已初始化；重复运行不会覆盖已有业务状态或恢复已撤销授权。")
        else:
            business = AgentBusiness(store)
            await business.initialize()
            approvals = ApprovalService(store, business)
            await approvals.initialize()
            result = await approvals.grant_review(args.username, args.venue)
            print("管理员场馆审核权已显式开通；审核不代表资源占用。")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent演示初始化与管理员场馆审核授权")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("init-demo", help="为已有真实账号创建虚构资源、排期、规则和显式业务授权")
    demo.add_argument("--username", required=True)
    review = sub.add_parser("grant-review", help="为已有场馆业务授权的管理员显式开通该场馆审核权")
    review.add_argument("--username", required=True)
    review.add_argument("--venue", required=True)
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
