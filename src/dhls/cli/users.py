"""本地账号运维入口：先停服务，再创建账号、重置密码或启停账号。

运行示例见README；密码只通过getpass交互读取，不接受命令行参数、配置文件或
默认值，避免落入命令历史。此脚本不初始化Embedding，也不调用大模型。
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import time

from dhls.core.auth import PASSWORD_HASH, clean_username, create_user, validate_password
from dhls.core.chat import ChatError, Settings
from dhls.core.store import Store


def read_new_password() -> str:
    """两次隐藏输入，确认一致才继续；不在日志或异常中拼接口令。"""
    password = getpass.getpass("请输入新密码（12至128个字符）：")
    confirmation = getpass.getpass("请再次输入新密码：")
    if password != confirmation:
        raise ChatError("PASSWORD_MISMATCH", "两次密码不一致，请重新运行命令。", 422)
    validate_password(password)
    return password


async def run(args):
    # 复用服务的OS实例锁。禁止运维命令在服务生成回答时执行启动恢复或账号迁移。
    from dhls.utils.locks import InstanceLock

    settings = Settings.from_env()
    settings.validate()
    lock = InstanceLock(settings.data_dir / "server.lock")
    lock.acquire()
    try:
        store = Store(settings.data_dir / "chat.sqlite3")
        await store.initialize()
        if args.command == "create-user":
            user = await create_user(store, args.username, read_new_password(), args.display_name,
                                     args.role, args.department, args.venue)
            print(f"账号已创建：{args.username}；角色：{user['role']}。请启动服务后登录。")
            return
        username = clean_username(args.username)
        password_hash = None
        if args.command == "reset-password":
            password_hash = await asyncio.to_thread(PASSWORD_HASH.hash, read_new_password())
        async with store.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            user = await (await db.execute("SELECT id,password_hash FROM users WHERE login_subject=?",
                                          (username,))).fetchone()
            # 不给旧演示账号补发密码；旧记录继续独立保留，避免转授历史隐私数据。
            if not user or not user["password_hash"]:
                raise ChatError("ACCOUNT_NOT_FOUND", "真实账号不存在，请使用create-user新建。", 404)
            if args.command == "reset-password":
                await db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user["id"]))
            else:
                await db.execute("UPDATE users SET is_active=? WHERE id=?", (int(args.enabled), user["id"]))
            # 重置/启停都撤销已有登录，恢复启用也不会让旧JWT复活。
            await db.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                             (int(time.time()), user["id"]))
            await db.commit()
        print("密码已重置，旧登录已撤销。" if password_hash else "账号状态已更新，旧登录已撤销。")
    finally:
        lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="场馆助手本地账号管理（执行前请停止聊天服务）")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="创建真实员工或管理员，无默认密码")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", choices=("employee", "admin"), required=True)
    create.add_argument("--department", required=True, help="当前账号所属部门标识")
    create.add_argument("--venue", required=True, help="当前账号所属场馆标识")
    reset = commands.add_parser("reset-password", help="交互重置密码并撤销该账号全部登录")
    reset.add_argument("--username", required=True)
    active = commands.add_parser("set-active", help="启停真实账号并撤销该账号全部登录")
    active.add_argument("--username", required=True)
    group = active.add_mutually_exclusive_group(required=True)
    group.add_argument("--enabled", action="store_true")
    group.add_argument("--disabled", dest="enabled", action="store_false")
    try:
        asyncio.run(run(parser.parse_args()))
        return 0
    except ChatError as error:
        print(f"{error.code}：{error.message}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("操作已取消。", file=sys.stderr)
    except RuntimeError:
        print("操作未完成：请先停止使用同一数据目录的聊天服务，再执行账号命令。", file=sys.stderr)
    except Exception:
        print("操作未完成：请检查数据目录权限、配置及依赖。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
