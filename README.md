# 场馆排期与需求变更 AI 数智化软件

本项目提供员工制度问答、场馆申请工具编排和创建草稿前的人工确认。使用 Python 3.11+、异步 FastAPI、原生 HTML/JavaScript、SQLite、BM25 + FAISS 混合检索，以及 LangGraph 实现的 Harness 执行循环。

已实现本地账号/JWT/角色校验、SSE问答、最近6轮完整历史、最多5轮Agent决策、逐轮日志与持久化确认。管理员界面目前为第1批骨架；文档上传/ACL、数据库提示词、对话审查和正式审批/占档仍按后续批次接入。代码可运行与生产上线验收分开记录，现有虚构制度不代表企业正式规则。

## 项目结构

```text
dhls/
├── README.md
├── LICENSE                       # 沿用项目历史MIT及第三方材料范围声明
├── .gitignore
├── CHANGELOG.md
├── CONTRIBUTING.md
├── requirements.txt              # 运行依赖唯一清单
├── pyproject.toml                # src包安装、入口及资源打包
├── src/dhls/
│   ├── __init__.py
│   ├── __main__.py
│   ├── main.py                   # FastAPI应用与启动入口
│   ├── api/agent.py              # Agent HTTP/SSE及确认接口
│   ├── core/                     # RAG、身份、会话、工具、循环与持久化
│   ├── utils/locks.py            # 操作系统实例锁
│   ├── cli/                      # 账号维护和虚构业务初始化
│   ├── static/                   # 打包随应用发布的前端资源
│   └── templates/                # Jinja2服务端模板
├── docs/
│   ├── design.md
│   ├── api.md
│   ├── evaluation.md
│   ├── prelaunch-checklist.md
│   └── ...                       # 分阶段说明及原有研究资料
├── tests/
│   ├── api/                      # 问答与Agent契约回归
│   └── core/                     # 身份与持久化相关回归
├── examples/demo.py
├── scripts/                      # 账号/演示初始化、运行、构建、评测结构校验
├── config/default.toml           # 非机密默认值参考；运行仍由环境变量配置
├── data/
│   ├── evaluation_cases.json     # 30条待执行评测
│   └── sample_policy.md          # 内置虚构制度的可读副本
└── .github/                      # 离线CI、Issue与PR模板
```

示例框架中的包名替换为 `dhls`；配置参考采用 Python 3.11 原生可读的 TOML。目录整理不迁移或覆盖本地运行数据。

## 安装与运行

以下命令从仓库根目录执行。Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python scripts/manage_users.py create-user --username venue.admin --display-name '场馆管理员' --role admin --department operations --venue east
python scripts/manage_users.py create-user --username venue.employee --display-name '员工' --role employee --department sales --venue east
```

Linux/macOS用 `source .venv/bin/activate` 激活环境，其余Python命令相同。账号不存在默认密码；创建时两次隐藏输入12至128字符口令。已有账号可跳过创建；账号操作前停止同一数据目录服务。

仅在后端当前进程配置模型密钥，再启动：

```powershell
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '请输入API密钥' -AsSecureString)).Password
python -m dhls
```

打开[登录页](http://127.0.0.1:8000/login)，员工进入制度问答，管理员进入管理页；[Agent工作台](http://127.0.0.1:8000/agent)可从聊天导航进入。默认只监听 `127.0.0.1:8000`、单worker；可用 `python -m dhls --port 8001` 更换本机端口。

首次问答启动会下载本地中文Embedding并建立索引，之后可复用缓存。默认生成模型为 `deepseek-flash`；兼容供应商通过 `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL` 和密钥环境变量配置。完整映射见[配置说明](config/README.md)，程序不自动加载 `config/default.toml`。

默认 `.chat_data/` 与 `.stage1_rag/` 相对进程当前工作目录，**不写入包安装目录**。从根目录启动可继续使用整理前的根目录数据；服务和维护CLI必须指向相同 `CHAT_DATA_DIR`。部署或从其他目录运行时，显式设置 `CHAT_DATA_DIR`、`RAG_WORK_DIR` 的绝对路径。数据库、签名密钥、模型、缓存和评测结果均不提交。

## Agent演示

停止服务后，为已有员工显式初始化虚构资源和业务授权，再启动：

```powershell
python scripts/manage_agent.py init-demo --username venue.employee
python -m dhls
```

命令打印本次场馆、资源和原排期编号。新申请顺序为 `search_policy → check_availability → validate_request(PASS) → create_draft`；变更另需读取本人原排期。`create_draft`先流式展示规范预览并持久中断，员工按钮确认后才创建唯一草稿；提交时再次检查当前权限、有效期和版本。

每次实际模型请求消耗一轮，失败和重启不返还，最多5轮；确认本身不增加模型轮数。草稿不提交审批、不占档。只有显式演示规则与内置虚构制度匹配时可PASS，真实规则未适配时返回待人工核实。详见[Agent说明](docs/Agent执行循环说明.md)。

## 命令行示例

安装后可用模块、脚本或console入口，不需要手动设置 `PYTHONPATH`：

```powershell
python examples/demo.py --init-only --work-dir "$env:TEMP\dhls-example"
python -m dhls.core.rag --retrieve-only --question '已批准的档期如何变更？'
python -m dhls.core.rag --question '审批期间版本变化怎么办？'
python scripts/manage_users.py --help
```

`--init-only`只创建虚构制度，不需要模型密钥或下载；`--retrieve-only`需要本地Embedding，未缓存时会下载，不调用生成模型。自定义制度使用 `--document` 或Web配置 `RAG_DOCUMENT`；已有制度不覆盖。安装后的等价入口为 `dhls`、`dhls-rag`、`dhls-users`、`dhls-demo`。

## 评测与检查

- [30条评测JSON](data/evaluation_cases.json)：制度10条（3条无答案陷阱）、工具10条、违规5条、多轮5条；[执行与评分说明](docs/evaluation.md)。
- [上线前25项检查清单](docs/prelaunch-checklist.md)：五个维度各5项，含负责人、验证方法、通过标准、状态和证据栏，初始均为未验证。
- [设计说明](docs/design.md)、[API索引](docs/api.md)、[全部文档](docs/README.md)与[版本记录](CHANGELOG.md)。

```powershell
python -m pip install -e '.[test,build]'
python scripts/validate_evaluation.py
python -m pytest tests -q -p no:cacheprovider --basetemp "$env:TEMP\dhls-pytest"
python -m build --outdir "$env:TEMP\dhls-dist"
```

临时目录请使用仓库外的独立路径；POSIX可用 `${TMPDIR:-/tmp}`。结构校验仅验证JSON契约，不代表模型通过30题。现有离线回归使用替身模型/检索，无需真实密钥或模型下载；真实模型效果与上线清单须另行留证验收。GitHub Actions配置在Windows/Linux的Python 3.11上执行离线回归和构建，实际状态见仓库Actions。

## 贡献与许可

项目原创代码与文档沿用[MIT许可证](LICENSE)，第三方输入和引用材料保留原权利声明。贡献流程见[CONTRIBUTING](CONTRIBUTING.md)。请使用虚构或脱敏材料提交Issue与PR，保留服务端权限、校验、人工确认和幂等约束。
