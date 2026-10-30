# 场馆排期与需求变更 AI 数智化软件

拟建设用于场馆档期管理、资源冲突提示和需求变更协作的 AI 数智化软件，通过来源追溯、版本管理与人工审核，辅助业务人员完成排期核对和变更交接。

项目目前处于阶段1技术原型验证：已按2026年10月30日的用户指令新增[单文件命令行原型](stage1_rag.py)，尚无Web应用、身份权限、正式审批、占档或合同费用执行能力。历史调研、流程图和软件报价保存在[docs](docs/README.md)，其中的能力和预算不代表当前原型已经实现或已经验收。

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
