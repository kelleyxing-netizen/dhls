# 配置参考

[default.toml](default.toml) 记录当前原型的非机密默认值，供核对和部署方案评审。**程序当前不加载此文件，也没有 TOML 覆盖环境变量的行为。** 修改它不会改变运行中的服务；运行配置仍由 `Settings.from_env()`、供应商配置装配及显式依赖注入决定。

Python 3.11+ 可用标准库 `tomllib` 读取该文件，但读取结果只是说明数据，不会自动应用到应用设置。目录整理后 `Settings` 位于 `src/dhls/core/chat.py`；模型默认值继续由阶段1供应商适配器提供。模型和 JWT 密钥不属于可提交配置，禁止放入 TOML、README、前端或仓库中的其他文件。

## Settings 字段映射

| TOML `settings` 键 | 当前运行环境变量 | 默认值与解释 |
| --- | --- | --- |
| `data_dir` | `CHAT_DATA_DIR` | 进程工作目录下 `.chat_data`；保存 SQLite、断点、签名密钥及实例锁 |
| `rag_dir` | `RAG_WORK_DIR` | 进程工作目录下 `.stage1_rag`；保存演示制度、本地模型和索引缓存 |
| 省略 `document` | `RAG_DOCUMENT` | 默认 `None`；未指定时由现有逻辑建立 / 读取 `rag_dir/policy.md`。TOML 没有 `null`，空字符串不等于 `None` |
| `embedding_model` | `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` |
| `offline` | `EMBEDDING_OFFLINE` | 默认 `false`；环境变量仅值为字符串 `1` 时启用。只禁止 Embedding 下载，生成模型仍可调用外部 API |
| 省略 `jwt_secret` | `AUTH_JWT_SECRET` | 默认 `None`；未配置时由现有本机身份服务在数据目录生成并保留独立签名密钥，不可复用模型密钥或提交生成文件 |
| `auth_cookie_secure` | `AUTH_COOKIE_SECURE` | 默认 `false`，对应环境变量 `0`；字符串 `1` 才启用 Secure Cookie。当前默认针对本机 HTTP，企业 HTTPS 入口应按上线单指定 |
| `auth_ttl_minutes` | `AUTH_TTL_MINUTES` | `60` 分钟；有效范围5至1440 |
| `timeout` | `LLM_TIMEOUT` | `60.0` 秒；上游模型网络超时，必须为有限正数 |
| `generation_timeout` | `CHAT_GENERATION_TIMEOUT` | `180.0` 秒；单次流式执行的总超时，必须为有限正数 |
| `heartbeat_seconds` | 无 | `10.0` 秒；当前只支持显式构造 `Settings` 注入，环境变量不会覆盖它，必须为有限正数 |
| 省略 `llm_config` | 见下表 | 默认 `None`；显式注入 `LLMConfig` 时优先使用注入对象，否则按环境装配 |

TOML 中的目录字符串表示默认进程工作目录下的位置，不表示从 `config/` 目录计算。从项目根目录启动时，数据位于根目录 `.chat_data/` 和 `.stage1_rag/`；安装 wheel 后从其他工作目录运行时，默认落在该工作目录，不写入 `site-packages`。环境变量传入相对路径时也按进程当前工作目录解析；需要固定位置时使用绝对路径。数据目录和模型缓存目录不进入版本控制。

## 模型和启动参数

| TOML 键 | 当前运行环境变量 | 默认值与映射 |
| --- | --- | --- |
| `model.provider` | `LLM_PROVIDER` | `deepseek`；兼容模式使用 `compatible` |
| `model.base_url` | `LLM_BASE_URL` | DeepSeek 默认 `https://api.deepseek.com`；兼容模式必须明确配置可用地址 |
| `model.name` | `LLM_MODEL` | DeepSeek 默认 `deepseek-flash`；兼容模式必须明确配置模型名 |
| `model.api_key_env` | `LLM_API_KEY_ENV` | 记录密钥环境变量的**名称**：DeepSeek 默认 `DEEPSEEK_API_KEY`，兼容模式默认 `LLM_API_KEY`；此 TOML 只展示默认 DeepSeek 名称 |
| 不记录密钥值 | 上一行所指向的环境变量 | 由进程环境提供；不保存在 TOML 或源代码中 |
| `server.host` | 无 | 启动入口固定 `127.0.0.1`，当前无环境变量覆盖 |
| `server.port` | `CHAT_PORT` | `8000`，由启动入口读取；不属于 `Settings` |
| `server.workers` | 无 | 启动入口固定 `1`，当前无环境变量覆盖 |
| `server.proxy_headers` | 无 | 启动入口固定 `false`，当前无环境变量覆盖 |

供应商地址要求无凭据、无查询串的 HTTPS；仅 loopback 兼容模型服务允许 HTTP。现有本地数据目录、单 worker 与实例锁约束不因新增参考文件而改变。

上线前应另填[上线检查清单](../docs/prelaunch-checklist.md)的并发、额度、超时、日志期限与恢复目标，不能把这些原型默认值当成生产验收标准。本文不新增可运行的配置加载器，不为尚未接入的生产能力设置假默认值。
