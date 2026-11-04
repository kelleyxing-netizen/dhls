# API索引

更新于2026年11月4日。Python入口为 `dhls.main:app`；推荐从已安装包运行 `python -m dhls`。默认仅本机、单worker，关闭公开Swagger页面。本轮新增员工草稿提交与场馆授权管理员审批，既有聊天与Agent SSE协议保持不变。

## 统一约定

JSON及SSE `data`信封均有 `code`、中文 `message`、`request_id`、`data`。响应开始前按HTTP状态码返回JSON错误，SSE开始后通过error事件报告失败。生成端点的done/error终态规则见各接口文档，不只凭HTTP 200判断业务成功。

登录使用HttpOnly JWT Cookie；写接口携带 `X-CSRF-Token`，可由 `/auth/me` 获取。服务端每次重读账号启用状态与角色，资源按所有权/显式业务授权校验。管理员角色不自动授予其他员工会话或场馆业务权限。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/login`、`/`、`/agent` | 登录、员工问答与Agent工作台 |
| POST | `/auth/login` | JSON账号密码，设置JWT Cookie；没有默认账号密码 |
| GET | `/auth/me` | 当前身份及CSRF令牌 |
| POST | `/auth/logout` | 撤销当前登录 |
| GET | `/config` | 当前用户、知识库及模型就绪状态，不返回模型密钥 |
| POST / GET | `/sessions` | 新建 / 列出本人的会话 |
| GET | `/sessions/{session_id}/messages` | 本人会话消息分页 |
| POST | `/chat` | `{session_id, question}`，Idempotency-Key UUID，POST SSE |
| POST | `/agent/runs` | `{question}`，Idempotency-Key UUID，最多5轮决策，POST SSE |
| GET | `/agent/runs/{run_id}` | 本人运行状态、预览、日志与实际草稿 |
| POST | `/agent/runs/{run_id}/confirm` | `{confirmation_id, preview_hash, approved}`，严格布尔，可信确认 / 拒绝后POST SSE |
| GET | `/drafts`、`/admin/approvals` | 本人草稿与管理员排期审批页面 |
| GET | `/api/drafts`、`/api/drafts/{draft_id}` | 本人草稿列表与详情、原排期差异、复核和审批记录 |
| POST | `/api/drafts/{draft_id}/submit` | `{expected_version}`，本人主动提交，事务复核及版本比较 |
| GET | `/admin/api/approvals`、`/admin/api/approvals/{draft_id}` | 管理员仅查看有显式审核授权场馆的已提交/已处理申请 |
| POST | `/admin/api/approvals/{draft_id}/decision` | `{expected_version,decision,comment,review_hash}`；decision为approve或return，通过需最新复核摘要，退回需原因 |
| GET | `/admin`、`/admin/knowledge`、`/admin/prompts`、`/admin/conversations` | 管理员SSR骨架与导航；尚未开放完整管理写接口 |
| GET | `/admin/api/status` | 管理功能批次状态，管理员权限 |

## 详细契约

- [员工问答](员工问答接口说明.md)：请求/响应、错误码、最近6轮、引用、幂等、断线及数据表。
- [账号与管理后台第1批](管理后台第1批说明.md)：JWT、CSRF、角色、账号迁移与运维CLI。
- [Agent循环](Agent执行循环说明.md)：工具参数、顺序、逐轮日志、SSE事件、预览确认及恢复。
- [草稿与审批](草稿与审批说明.md)：提交、场馆审核授权、变更差异、实时复核、通过/退回及不可变审批记录。

普通聊天的sources来自实际检索结果。Agent的preview_start/preview_delta/confirmation_required来自服务端保存的规范预览；只有持久等待状态才允许员工确认。确认不是模型工具，不能用聊天文本“确认”代替；已成功创建的草稿以状态接口和数据库事实为准，不依赖模型总结。

审批页面和接口不属于模型工具。管理员角色不自动扩大场馆或草稿访问范围；草稿提交、审批通过均不执行资源占档或修改原排期。
