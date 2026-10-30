# 贡献指南

欢迎通过Issue描述缺陷或功能需求，通过Pull Request提交改动。请先阅读[项目说明](README.md)、[设计](docs/design.md)及[评测说明](docs/evaluation.md)。项目沿用[MIT许可证](LICENSE)，第三方材料仍按原权利声明处理。

## 开发环境

使用Python 3.11+，从仓库根目录创建虚拟环境并安装：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e '.[test,build]'
```

Linux/macOS使用 `source .venv/bin/activate`。账号由 `python scripts/manage_users.py` 交互创建，没有默认管理员密码；真实模型密钥仅通过进程环境提供。日常回归不需要模型密钥或Embedding下载。

## 提交前检查

```powershell
python scripts/validate_evaluation.py
python -m pytest tests -q -p no:cacheprovider --basetemp "$env:TEMP\dhls-pytest"
python -m build --outdir "$env:TEMP\dhls-dist"
git diff --check
```

POSIX终端用 `${TMPDIR:-/tmp}/dhls-pytest` 和 `${TMPDIR:-/tmp}/dhls-dist` 作为临时目录。每次测试应使用本次独有、可清理的目录；不要让pytest清理已有重要数据的路径。

源码位于 `src/dhls/`，测试按 `tests/api/`、`tests/core/` 分组。依赖维护在 `requirements.txt`，`pyproject.toml`从同一文件读取运行依赖；不要额外复制一套依赖版本。

涉及资源模板或包路径时，除editable安装外还应安装wheel并从仓库外目录验证登录页、静态资源、模块入口及账号CLI。涉及规则或写工具时保留服务端权限、五轮预算、员工确认、版本复核和幂等，不得只用提示词替代。

## Pull Request

使用 `codex/` 或用途明确的分支名，提交范围清晰的PR，按模板说明问题、结果和验证证据。更新根目录[CHANGELOG](CHANGELOG.md)及受影响的文档。历史报告保留原日期和事实边界；不要把虚构样例改写成已核实业务事实。

公开Issue/PR不要包含密钥、JWT、密码、数据库、真实合同或报销材料。发现安全问题时先提供不含敏感信息的最小描述，避免公开可直接利用的真实账号或数据。当前CI仅运行离线检查与构建，没有自动生产部署权限。
