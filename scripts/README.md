# 运行与维护脚本

先从仓库根目录执行 `python -m pip install -e .`。这些脚本依赖正式安装的 `dhls` 包，不修改 `sys.path`；不提供默认密码、模型密钥、后台进程或自动生产部署。

| 入口 | 用途 |
| --- | --- |
| `python scripts/manage_users.py --help` | 交互创建账号、重置密码、启停账号；先停止同数据目录服务 |
| `python scripts/manage_agent.py init-demo --username venue.employee` | 为已有账号显式初始化虚构场馆、资源与业务授权 |
| `python scripts/validate_evaluation.py` | 只用标准库校验30条评测数据结构，不运行模型或判定业务验收 |
| `pwsh -File scripts/run.ps1 -Port 8000` | 在仓库根目录启动本机服务；跨平台可直接用 `python -m dhls` |
| `pwsh -File scripts/build.ps1 -OutDir <仓库外目录>` | 安装构建依赖后构建wheel与源码包；跨平台可用 `python -m build --outdir <仓库外目录>` |

安装后的等价命令为 `dhls`、`dhls-users`、`dhls-demo`、`dhls-rag`，也可用 `python -m dhls.cli.users` / `python -m dhls.cli.demo`。服务、账号CLI与样本CLI应使用相同的 `CHAT_DATA_DIR`。默认运行目录相对当前工作目录，部署时应显式指定数据和索引目录。

生产部署的目标平台、域名、证书、配额及发布权限尚未在仓库中配置，因此没有填入会产生真实部署副作用的 `deploy.sh`。上线步骤及回滚验收见[上线检查清单](../docs/prelaunch-checklist.md)。
