# 场馆排期与需求变更 MVP

本目录是现有工程的独立运行配置与交付入口，覆盖制度问答、申请与变更工具编排、员工确认、主动提交、场馆授权审核。实际后端、Agent与页面均复用 [src/dhls](../src/dhls)，不维护第二套业务代码。

默认运行数据放在本目录已忽略的 `.runtime/`，不会接入仓库根目录现有 `.chat_data/`。启动时不创建默认账号或自动办理任何审批。当前是本机、单worker原型；企业制度ACL、正式资源占档、新旧排期切换及外部通知未实现。

## 文件与组件

```text
mvp/
├── run.py                       # 统一入口，调用已安装dhls包
├── profile.toml                 # 实际读取的非机密配置
├── architecture.md              # 工程架构、源码和接口映射
├── agent/
│   ├── README.md                # LangGraph与业务工具约束
│   └── contract.json            # 六工具、五轮预算、H1/H2/H3
├── knowledge_base/
│   ├── policy.md                # 可运行的虚构制度
│   ├── sources.json             # 来源、版本、SHA256与可见范围
│   └── README.md                # 混合检索与制度接入说明
├── scenarios/
│   └── acceptance.md            # 三处人工点及正常/拒绝/退回验收
└── tests/
    ├── test_profile.py          # 配置、隔离目录、只读检查和启动边界
    └── test_human_workflow.py   # 真实HTTP/图/数据库的端到端离线验收
```

## 安装与只读检查

从项目根目录执行，Python 3.11及以上。Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e '.[test,build]'
python mvp/run.py doctor --json
```

Linux/macOS使用 `source .venv/bin/activate`，其余Python命令相同。`doctor`核对依赖元数据、知识库摘要、工具契约和模型配置格式，不下载模型、不联网、不建数据库。模型配置有效不等于连通性或真实回答质量通过；没有模型密钥时会提示后端尚需配置。

## 创建账号与显式演示授权

先停止同数据目录服务。密码由维护命令两次隐藏输入，12至128字符，不提供默认密码。以下账号仅为演示建议名称，可自行更换。

```powershell
python mvp/run.py users create-user --username mvp.employee --display-name 'MVP员工' --role employee --department sales --venue east
python mvp/run.py users create-user --username mvp.admin --display-name 'MVP审核员' --role admin --department operations --venue east
python mvp/run.py demo init-demo --username mvp.employee
python mvp/run.py demo init-demo --username mvp.admin
python mvp/run.py demo grant-review --username mvp.admin --venue east
```

记录 `init-demo` 返回的实际场馆、资源和本人原排期编号。样例资源100人、独占、0分钟缓冲、最长72小时均为虚构规则。管理员身份不代替场馆业务权和审核权；禁止审核本人申请。重复初始化不覆盖状态或恢复已撤销授权。

## 启动与使用

仅在后端进程环境设置生成模型密钥，再启动；不写入文件或前端。

```powershell
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '模型密钥' -AsSecureString)).Password
python mvp/run.py serve --port 8000
```

打开 [本机登录页](http://127.0.0.1:8000/login)。员工从 `/agent` 准备申请，在 `/drafts` 提交并查看结果；管理员在 `/admin/approvals` 审核。首次启动检索会下载中文Embedding并建立索引，耗时由机器和网络决定。兼容模型需支持Function Calling；模型和超时配置见 [配置说明](../config/README.md)。

| 人工节点 | 明确操作 | 服务端结果 |
| --- | --- | --- |
| H1 | 员工核对完整预览后确认创建 | 确认前0份草稿，确认后同run至多1份 |
| H2 | 本人在“我的草稿”点击提交审核 | draft变为submitted；未提交对审核人不可见 |
| H3 | 获场馆审核权的其他管理员复核后通过或退回 | approved或returned，保存意见与事件；不占档 |

自然语言“确认”、模型调用或HTTP 200不能替代人工操作与实际持久状态。输入缺项、冲突、旧版本或真实规则未适配时不能默认通过。详细演示与负例见 [验收步骤](scenarios/acceptance.md)。

## 目录与配置优先级

`profile.toml`里的路径相对配置文件所在目录，即使从其他工作目录调用，也保持相同位置。MVP入口实际读取本文件，根目录 `config/default.toml`仍只是普通入口的参考。

MVP不沿用终端中的 `CHAT_DATA_DIR`、`RAG_WORK_DIR`和`RAG_DOCUMENT`，以免接错根工程数据；这三个值仅在当前委托操作内临时设置并在返回后还原。模型、JWT与超时环境配置继续复用。需要改目录或制度时必须在子命令**之前**显式指定，账号、样本与服务命令全部使用相同参数：

```powershell
python mvp/run.py --runtime-dir 'D:\dhls-mvp-runtime' doctor --json
python mvp/run.py --runtime-dir 'D:\dhls-mvp-runtime' users --help
python mvp/run.py --runtime-dir 'D:\dhls-mvp-runtime' serve --port 8001
```

`--document`允许指定另一份Markdown；真实规则未适配仍进入人工核实，不因更换文档就能自动PASS。显式自定义制度不再要求与演示清单的摘要一致。备份应在停服后完整复制 `.runtime/chat/`（包括业务库、checkpoint与签名材料），恢复匹配的制度与索引版本；不能仅拷一个运行中的SQLite文件。

## 验证与交付包

```powershell
python -m pytest mvp/tests -q -p no:cacheprovider --basetemp "$env:TEMP\dhls-mvp-tests"
python -m pytest tests mvp/tests -q -p no:cacheprovider --basetemp "$env:TEMP\dhls-all-tests"
python scripts/build_mvp.py --output "$env:TEMP\dhls-mvp-source.zip"
```

临时目录须选仓库外本次专用路径；POSIX可用 `${TMPDIR:-/tmp}`。测试使用替身模型和检索，执行真实鉴权、LangGraph、业务校验、确认、提交和审核；不调用真实模型，不使用本目录运行库。它们是自动化契约证据，不替代有截图的场景过程Word或生产验收。

`build_mvp.py`从当前**已提交HEAD**按白名单导出可安装源码包，附来源提交及逐文件SHA256，不打包工作区未提交改动、运行数据或模型。未提交的新MVP文件会导致构建拒绝，先提交后再构建。输出文件须不存在，不会覆盖既有包。完整仓库研究文档不属于该精简源码包。

## 范围对应

工程架构见 [architecture.md](architecture.md)，Agent与知识库分别见 [agent/README.md](agent/README.md) 和 [knowledge_base/README.md](knowledge_base/README.md)。本目录补齐清单中的MVP工程交付，不等于“问答助手单独归档”或五类测试过程Word已完成。
