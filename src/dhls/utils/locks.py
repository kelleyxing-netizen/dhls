"""【可复用核心】同一运行数据目录的操作系统实例锁。"""
from __future__ import annotations

import os
from pathlib import Path


class InstanceLock:
    """SQLite原型限定单个服务进程，防止另一实例的启动恢复误伤正在生成的消息。

    使用操作系统文件锁，进程崩溃会自动释放，不依赖永久残留的PID文件。
    """
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("该数据目录已有聊天服务运行，请关闭重复实例；本原型仅支持单进程。") from None

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
