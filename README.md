# 场馆排期与需求变更 AI 数智化软件

拟建设用于场馆档期管理、资源冲突提示和需求变更协作的 AI 数智化软件，通过来源追溯、版本管理与人工审核，辅助业务人员完成排期核对和变更交接。

项目目前处于阶段2员工制度问答原型验证：2026年10月30日，用户在阶段1命令行原型基础上进一步确认实现FastAPI、SQLite及原生HTML/JavaScript聊天页面。现在可以在本机逐段接收模型回答、查看真实检索引用、保存和恢复会话；阶段1的[单文件命令行入口](stage1_rag.py)继续保留。历史调研、流程图和软件报价保存在[docs](docs/README.md)，其中的拟建业务能力和预算不代表已经实现或通过生产验收。

## 阶段2：本机员工聊天

需要Python 3.11+。在项目根目录运行以下PowerShell命令；密钥只设置在当前终端的进程环境中，不写入文件或网页：

```powershell
python -m pip install -r requirements.txt
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '请输入API密钥' -AsSecureString)).Password
python chat_app.py
```

打开[本机聊天页面](http://127.0.0.1:8000)。首次启动需要下载本地Embedding模型并建立制度索引；可以复用阶段1的`.stage1_rag/`缓存。未指定制度文件时，沿用阶段1生成的虚构演示制度，已有文件不会被覆盖。

- [chat_app.py](chat_app.py)提供FastAPI接口和静态页面；[chat_core.py](chat_core.py)复用阶段1检索并解析真实上游流；[chat_store.py](chat_store.py)负责SQLite持久化；[static](static/)为原生聊天页面。
- 浏览器用`fetch`读取POST SSE，可停止生成、展开引用、切换会话并在刷新后恢复记录。成功的回答先校验引用、保存正文和来源，再发送`sources`及`done`；失败或中断的残文保留状态，不进入最近6个成功完整问答对的上下文。
- `Idempotency-Key`防止重试产生重复提问：成功同参请求重放结果，失败同键重试复用原消息ID；进行中、异参复用和同会话并发返回明确冲突。启动时恢复上次未完成的回答。
- 用户、会话、消息实际保存在`.chat_data/chat.sqlite3`；模型、索引仍在`.stage1_rag/`。这些运行数据、缓存、依赖目录、临时检查结果和密钥不提交仓库。

当前只开放`127.0.0.1:8000`的单进程本机演示。所有本机页面使用同一个演示员工身份；后端已按当前用户检查会话所有权，但尚未接入真实员工SSO和制度访问权限。生成回答来自配置的外部模型，Embedding在本地运行；本轮没有实现生产审批、占档、支付或外部业务执行能力。

常用配置通过后端环境变量设置；完整接口、错误码和边界见[员工问答接口说明](docs/员工问答接口说明.md)。

| 环境变量 | 默认值或用途 |
| --- | --- |
| `CHAT_DATA_DIR` | `.chat_data`，SQLite及实例锁目录 |
| `RAG_WORK_DIR` | `.stage1_rag`，制度示例、Embedding模型和索引缓存 |
| `RAG_DOCUMENT` | 可选的UTF-8制度Markdown文件路径 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` |
| `EMBEDDING_OFFLINE=1` | 仅禁止Embedding下载，需要已有模型缓存；仍会调用外部生成模型 |
| `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL` | 默认DeepSeek；`compatible`模式须配置兼容接口地址及模型 |
| `LLM_API_KEY_ENV` | 自定义保存密钥的环境变量名；默认DeepSeek用`DEEPSEEK_API_KEY`，兼容模式用`LLM_API_KEY` |
| `LLM_TIMEOUT` | 上游模型网络超时，默认60秒 |
| `CHAT_GENERATION_TIMEOUT` | 单次生成总超时，默认180秒 |

服务限定单worker，并用操作系统实例锁阻止同一数据目录启动第二个实例，避免启动恢复误伤进行中的回答。制度和模型缓存使用启动时快照；修改制度后重启服务。

离线检查需另装`pytest`，不需要`pytest-asyncio`。临时数据库放在仓库外：

```powershell
python -m pip install pytest
python -m pytest tests/test_chat.py -q -p no:cacheprovider --basetemp ..\stage2-runtime\pytest
```

本轮15项离线测试通过，覆盖流式输出、幂等与并发、所有权、完整历史、断线、重启、引用、保存失败及超时。另已在浏览器完成真实DeepSeek多轮流式问答、展开来源、刷新恢复、停止生成及同键重试验证。这些结果仅针对演示样本，不代表真实企业制度准确率、安全性或生产验收通过。

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
