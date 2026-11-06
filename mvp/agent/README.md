# MVP Agent框架

实际框架为LangGraph Harness与SQLite checkpoint，源码见 [agent_loop.py](../../src/dhls/core/agent_loop.py)。模型在每轮提出至多一个完整工具调用；执行权属于服务端。没有Shell、SQL、任意HTTP或模型确认工具。

[contract.json](contract.json)为交付契约，`doctor`核对其中六个工具和五轮预算是否与真实schema一致。它不替代Python、数据库及HTTP权限约束。

| 工具 | 前置和结果 |
| --- | --- |
| search_policy | 获授权场馆的制度证据及资源目录 |
| get_booking_context | 变更时读取本人原排期和版本 |
| check_availability | 规范候选的容量及冲突，返回带版本凭据 |
| validate_request | 检索、资源和原排期读取满足后确定性校验；仅PASS给validation_id |
| create_draft | 仅接收服务端validation_id，先中断预览，H1确认后才创建 |
| get_workflow_status | 只读本人当前run，不接受任意人员或run编号 |

新申请顺序为检索、查资源、校验、预览；变更还需读取本人原排期。每次实际模型请求先持久化扣一轮，失败和重启不退，最多5次；确认不消耗额外轮次。规范预览绑定摘要、所有权和有效期，恢复后写事务再次复核当前条件，同run与validation各至多一份草稿。

H2提交与H3审核由页面和独立API完成，均不属于模型工具。审核需要管理员角色与显式场馆审核权，禁止自审。通过不占档；退回修改内容需新建修正草稿。详见 [人工验收步骤](../scenarios/acceptance.md)。

测试中的脚本模型只存在于 `mvp/tests/`，不由 `serve`加载。真实服务只使用后端配置的生成模型，不把离线测试结果包装成真实模型效果。
