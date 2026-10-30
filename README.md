# 场馆排期与需求变更 AI 数智化软件

拟建设用于场馆档期管理、资源冲突提示和需求变更协作的 AI 数智化软件，通过来源追溯、版本管理与人工审核，辅助业务人员完成排期核对和变更交接。

项目已实现员工制度问答与管理后台第1批：本地账号登录、JWT、员工/管理员角色校验和服务端渲染的后台骨架。可以逐段接收模型回答、查看检索引用、保存和恢复各自的会话。知识库上传、数据库提示词和对话审查将按后续批次接入；阶段1的[单文件命令行入口](stage1_rag.py)继续保留。历史调研、流程图和软件报价保存在[docs](docs/README.md)，其中的拟建能力和预算不代表已经实现或通过生产验收。

## 阶段2：本机员工聊天

需要Python 3.11+。首次运行先安装依赖并创建真实账号。下面的账号名称仅作命令示例，没有预设密码；每次创建会要求两次隐藏输入12至128字符的密码。部门和场馆参数为当前账号所属范围标识。

```powershell
python -m pip install -r requirements.txt
python manage_users.py create-user --username venue.admin --display-name '场馆管理员' --role admin --department operations --venue east
python manage_users.py create-user --username venue.employee --display-name '员工' --role employee --department sales --venue east
```

然后配置模型并启动。API密钥只设置在当前终端进程内，不写文件或网页：

```powershell
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '请输入API密钥' -AsSecureString)).Password
python chat_app.py
```

打开[登录页](http://127.0.0.1:8000/login)。员工登录后进入聊天，管理员进入[管理后台](http://127.0.0.1:8000/admin)，也可返回聊天。首次启动需要下载本地Embedding模型并建立制度索引，可以复用阶段1缓存；未指定制度文件时沿用虚构演示制度，已有文件不会被覆盖。

- [chat_app.py](chat_app.py)提供FastAPI接口和静态页面；[chat_core.py](chat_core.py)复用阶段1检索并解析真实上游流；[chat_store.py](chat_store.py)负责SQLite持久化；[static](static/)为原生聊天页面。
- [chat_auth.py](chat_auth.py)提供Argon2、JWT、登录撤销、CSRF与角色依赖；[manage_users.py](manage_users.py)提供本地账号运维；[templates](templates/)为登录和后台的Jinja2页面。中文注释标出可复用核心。
- 浏览器用`fetch`读取POST SSE，可停止生成、展开引用、切换会话并在刷新后恢复记录。成功的回答先校验引用、保存正文和来源，再发送`sources`及`done`；失败或中断的残文保留状态，不进入最近6个成功完整问答对的上下文。
- `Idempotency-Key`防止重试产生重复提问：成功同参请求重放结果，失败同键重试复用原消息ID；进行中、异参复用和同会话并发返回明确冲突。启动时恢复上次未完成的回答。
- 用户、会话、消息实际保存在`.chat_data/chat.sqlite3`；模型、索引仍在`.stage1_rag/`。这些运行数据、缓存、依赖目录、临时检查结果和密钥不提交仓库。

当前仍只监听`127.0.0.1:8000`、单进程。每个账号只能读写自己的聊天；管理员角色不能通过普通聊天接口冒用员工会话。旧演示账号迁移后停用，历史数据保留原所有者。JWT保存在HttpOnly Cookie中，写请求需要CSRF令牌；每次接口请求重新核对数据库里的角色和账号状态。

当前制度仍是全部已登录账号共用的单份Markdown快照，部门/场馆字段尚未用于制度ACL；尚未接入企业SSO、多范围授权或生产部署。后续知识库批次将实现文档权限和索引管理。生成回答来自外部模型，Embedding在本地运行；本轮没有实现生产审批、占档、支付或外部业务执行能力。

忘记密码或需要停用账号时，先停止服务，在相同`CHAT_DATA_DIR`下执行：

```powershell
python manage_users.py reset-password --username venue.employee
python manage_users.py set-active --username venue.employee --disabled
python manage_users.py set-active --username venue.employee --enabled
```

这些命令会撤销该账号的旧登录。账号命令与服务共享实例锁，服务运行时拒绝执行，避免误改正在生成的消息状态；没有自助注册和默认管理员密码。

常用配置通过后端环境变量设置；完整接口、错误码和边界见[员工问答接口说明](docs/员工问答接口说明.md)。

| 环境变量 | 默认值或用途 |
| --- | --- |
| `CHAT_DATA_DIR` | `.chat_data`，SQLite及实例锁目录 |
| `AUTH_JWT_SECRET` | 可选；独立随机签名密钥，至少32字节，不能复用模型密钥；本机未配置时在运行目录生成并保留`jwt-signing.key` |
| `AUTH_TTL_MINUTES` | 登录有效期，默认60分钟，范围5至1440；到期需重新登录 |
| `AUTH_COOKIE_SECURE` | 本机HTTP为`0`；HTTPS部署须设`1`并保护签名密钥/数据目录权限 |
| `RAG_WORK_DIR` | `.stage1_rag`，制度示例、Embedding模型和索引缓存 |
| `RAG_DOCUMENT` | 可选的UTF-8制度Markdown文件路径 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` |
| `EMBEDDING_OFFLINE=1` | 仅禁止Embedding下载，需要已有模型缓存；仍会调用外部生成模型 |
| `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL` | 默认DeepSeek；`compatible`模式须配置兼容接口地址及模型 |
| `LLM_API_KEY_ENV` | 自定义保存密钥的环境变量名；默认DeepSeek用`DEEPSEEK_API_KEY`，兼容模式用`LLM_API_KEY` |
| `LLM_TIMEOUT` | 上游模型网络超时，默认60秒 |
| `CHAT_GENERATION_TIMEOUT` | 单次生成总超时，默认180秒 |

服务限定单worker，并用操作系统实例锁阻止同一数据目录启动第二个实例，避免启动恢复误伤进行中的回答。制度和模型缓存使用启动时快照；修改制度后重启服务。

登录接口、Cookie/CSRF、迁移和批次边界见[管理后台第1批说明](docs/管理后台第1批说明.md)。JWT签名密钥与供应商API密钥用途不同；更换JWT密钥会使旧登录失效。当前登录限流在单进程内生效，重启后重置；不能直接当作企业私有云部署方案。

离线检查需另装`pytest`，不需要`pytest-asyncio`。临时数据库放在仓库外：

```powershell
python -m pip install pytest
python -m pytest tests -q -p no:cacheprovider --basetemp "$env:TEMP\dhls-pytest"
```

测试覆盖JWT、角色、CSRF、旧库迁移，以及现有流式输出、幂等与并发、所有权、完整历史、断线、重启、引用、保存失败及超时。阶段2曾在浏览器完成真实DeepSeek多轮问答及恢复；本批验证记录见[更新记录](docs/CHANGELOG.md)。这些结果仅针对测试和演示数据，不代表生产验收。

## 阶段1：单脚本制度问答

- Python 3.11+；一个Python脚本，不搭建Web服务。
- 首次运行在本地`.stage1_rag/policy.md`创建虚构演示制度，已有文件不会被覆盖。
- 按500个Unicode字符切片，本地中文Embedding向量化后存入FAISS，附带JSON来源与缓存校验信息。
- 问题经BM25和向量两路召回，以RRF融合、去重并取top3，拼入提示词后调用LLM；回答标注片段来源。
- 默认生成模型为DeepSeek `deepseek-flash`，默认本地Embedding为FastEmbed的`BAAI/bge-small-zh-v1.5`。首次使用需下载模型，之后可加`--offline`仅使用本地模型缓存。
- 代码以中文注释标出切片、Embedding、FAISS存储、混合检索、脱敏出口、提示词及独立异步LLM调用等可复用核心模块。

安装和运行（PowerShell）：

```powershell
python -m pip install numpy faiss-cpu fastembed rank-bm25 httpx
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '请输入API密钥' -AsSecureString)).Password
python stage1_rag.py
```

单次问答、仅检索和自定义制度：

```powershell
python stage1_rag.py --question "已批准的档期如何变更？"
python stage1_rag.py --retrieve-only --question "审批期间版本变化怎么办？"
python stage1_rag.py --document 'D:\本地制度\场馆制度.md'
```

兼容供应商通过`--provider compatible --base-url https://your-gateway.example/v1 --model your-model`配置，密钥默认读取`LLM_API_KEY`。也支持`LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL`及`--api-key-env`；兼容范围为`chat/completions`协议，不宣称支持所有供应商的专有接口。

运行数据可用`--work-dir`移到其他本地目录。编辑制度后重启脚本，将根据文档内容自动选择或重建索引；运行期间使用启动时的制度快照。原型仅用于受信任的本地演示数据；内置正则脱敏不保证识别姓名、合同细节及组合身份信息，真实企业数据接入前须完善授权与脱敏。`.stage1_rag/`、模型、索引和密钥不进入公开仓库。
