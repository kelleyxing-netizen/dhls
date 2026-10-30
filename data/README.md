# 样例与评测数据

- [evaluation_cases.json](evaluation_cases.json)：对话中整理的30条评测用例，制度10条（含3条无答案陷阱）、工具10条、违规意图5条、多轮上下文5条；只包含4个约定字段。
- [sample_policy.md](sample_policy.md)：与检索核心内置文本一致的虚构制度，供审阅或通过 `RAG_DOCUMENT` 显式加载。

全部样例为虚构材料，不含真实客户、账号、报销数据或密钥。运行数据库、模型、FAISS索引与测试结果不要放在本目录。评测结构校验和45项离线回归不等于30题已完成真实模型评测，执行与评分口径见[评测说明](../docs/evaluation.md)。
