# DHLS MVP可运行源码包

本包包含已提交版本的运行源码、MVP独立配置、演示知识库、自动化测试和当前操作说明。它不是完整仓库备份，不包含历史调研、客户资料、账号、数据库、签名密钥、依赖环境、模型或缓存。

解压后进入本文件所在目录，使用Python 3.11及以上：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e '.[test,build]'
python mvp/run.py doctor --json
```

账号、显式业务授权、后台模型密钥、三处人工确认、服务启动和独立数据路径见 [MVP使用说明](../mvp/README.md)。实际工程代码位于 [src/dhls](../src/dhls)，不是占位目录。`MVP_RELEASE.json`记录来源提交、逐文件摘要及来源路径，可用脚本核验。

```powershell
python scripts/build_mvp.py --verify .
python -m pytest tests mvp/tests -q -p no:cacheprovider --basetemp "$env:TEMP\dhls-mvp-verify"
```

测试是离线契约验证，不访问真实模型或现有业务数据。正式制度ACL、正式占档、新旧排期切换和外部通知仍未实现。生成模型和Embedding首次下载需另行配置网络与合法账号；包本身不包含供应商凭据。
