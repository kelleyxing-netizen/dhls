"""运行完整制度问答CLI，也支持--init-only离线创建虚构制度。

先在仓库根目录执行 python -m pip install -e .，再运行：
python examples/demo.py --init-only --work-dir <本地临时目录>
python examples/demo.py --retrieve-only --question "已批准的档期如何变更？"
不设置模型密钥时可使用仅初始化/仅检索模式，示例不会绕过登录或员工确认。
"""
from dhls.core.rag import cli

if __name__ == "__main__":
    raise SystemExit(cli())
