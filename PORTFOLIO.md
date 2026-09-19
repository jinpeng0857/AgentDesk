# AgentDesk 项目表达与证据清单

> 本文件用于管理简历素材和面试叙事，不是系统设计文档。
>
> 文档版本：v2.0
> 当前结论：Phase 0 契约基线已落地并通过本地验收；完整项目尚未完成
> 开发约束：[`AGENTS.md`](AGENTS.md) · 设计：[`docs/architecture.md`](docs/architecture.md)

---

## 1. 表达原则

简历只陈述仓库和运行产物能证明的事实。

- 设计门槛不是实测成绩。
- 计划采用某项技术，不等于已经实现。
- 空测试文件不等于测试覆盖。
- Golden Dataset 的目标规模不等于已有样本数。
- 不保留百分比、数值或待办式占位符，避免被误复制成简历成果。
- 任何性能、准确率、成功率和“零绕过”表述都必须链接到 eval artifact。
- 简历 bullet 使用完成时之前，必须通过 §3 的证据门禁。

---

## 2. 当前可验证状态

截至 Phase 1 只读纵切完成（**Phase 1 已完成验收，Phase 2 待开始**）：

### 已完成

- 明确项目定位：作品集级生产化，而非企业级 SaaS 或一次性 Demo。
- 完成四角色权限边界、确定性 Dispatcher、高风险审批和独立 Worker 的系统设计。
- 确定 OpenAI-compatible 模型接口、PostgreSQL 持久化、Redis/ARQ 队列和 Chroma 检索方案。
- 定义 Tool Governance、状态所有权、失败模式、Evaluation 与 CI 门禁。
- 建立 Pydantic v2 领域契约、八个 Tool Schema、封闭 Registry 和按角色白名单。
- 填写四份 Agent contract 与 Prompt，并提供契约校验脚本。
- 从 FastAPI 代码导出 OpenAPI，再生成前端 TypeScript API 类型。
- 建立 20 条人工确认标签的冒烟评估数据，覆盖五类 intent 和八个 Tool。
- 锁定 Python 与前端依赖；PostgreSQL、Redis Compose 服务已通过健康启动检查。
- 初始化 Git，并以独立功能分支保留可审查的提交历史。
- Alembic 从空 PostgreSQL 建出业务表与 LangGraph checkpoint 表，应用启动时不隐式建表。
- 实现 Argon2id + JWT 认证，强制校验 issuer、audience 与有效期。
- `POST /api/tickets` 在单事务内写入工单与 outbox 后返回 HTTP 202；API 不运行 Agent 图。
- ARQ 使用严格 JSON 载荷与确定性 `start:{ticket_id}` 作业 ID，并测试了重复投递与 Redis 故障路径。
- 实现 OpenAI-compatible 模型适配层与确定性 Fake，区分可重试与不可重试失败。
- 实现四个 Agent 角色、四个只读 Tool、可注入的只读执行网关、四个 SQL Adapter 与本地 Chroma 检索。
- 只读执行网关对四个写工具确定性拒绝且零副作用，每次尝试都写入脱敏审计。
- Worker 独占运行 LangGraph 图：基于 PostgreSQL checkpointer 实现确定性断点恢复，故障注入测试验证重启后 Supervisor 分类不重复且零写副作用。
- 实现工单查询与脱敏 Trace API（`GET /api/tickets`、`GET /api/tickets/{ticket_id}`、`GET /api/tickets/{ticket_id}/trace`），严格保障请求人数据隔离与越权防探测（404）。
- 交付 Vue 3 最小前端：纯内存认证、提交表单立即清空密码输入、2 秒自动轮询、后台页面休眠暂停、终态停止以及 5 字段知识库卡片展示。
- 交付三个 CI 工作流（Backend CI、Frontend CI、Evaluation）与自动化 `phase1_smoke.py` 脚本。
- 冻结真实 live 模型前后对比产物：`eval/artifacts/phase1-before.json` 与 `eval/artifacts/phase1-after.json`，在干净工作区基于同一模型（`deepseek-chat`）产出并严格比对元数据。

### 尚未完成（Phase 2/3 范围）

- 四个业务写 Tool 的真实实现，以及幂等账本、Policy 风险矩阵、审批与 interrupt/resume；
- 完整 80 条 Golden Dataset、长期 baseline、Eval 看板与可发布的完整实测指标；
- 审批台（ApprovalView）与 Trace 可视化看板（TraceViewer）；
- 跨主机部署与多节点高可用。

因此当前可以展示为“已完成契约基线与可运行的只读纵切（包含独立 Worker、PostgreSQL checkpoint 恢复、只读工具治理、最小前端与 live 模型对比产物），正在开发写操作治理与 HITL 的作品集级生产化项目”。

---

## 3. 简历证据门禁

| 候选能力 | 写入简历前必须具备的证据 | 当前状态 |
|---|---|---|
| 多 Agent 编排 | 可运行图、路由测试、真实 Trace | 部分满足：四个 Agent 角色、Dispatcher 确定性边界、只读 LangGraph 图与恢复测试已通过；写操作与 HITL 属 Phase 2 |
| 独立 Worker | HTTP 202、队列记录、Worker 重启恢复测试 | 满足：HTTP 202、JSON ARQ 载荷、确定性 job ID、outbox 投递与 Worker 崩溃恢复断点续跑测试已通过 |
| Tool Governance | Registry、白名单、Policy、幂等账本、审计测试 | 部分满足：Registry、白名单、Schema、只读执行网关与脱敏审计测试已有；幂等账本与写路径属 Phase 2 |
| Human-in-the-loop | interrupt、审批记录、resume、重复恢复测试 | 未满足：属 Phase 2 交付范围 |
| RAG | 可回查引用、检索评估产物 | 满足：Chroma 返回五字段可回查引用，持久化至 projection，并在详情 API 与前端卡片完整渲染 |
| Evaluation | 人工审核数据集、baseline、带元数据的 Live Eval | 部分满足：20 条人工审核样本、live model baseline 与 candidate 对比 artifact 已冻结（`phase1-before.json` vs `phase1-after.json`）；完整 80+ 条评测集待 Phase 3 |
| 前端 | 可访问页面、OpenAPI 生成类型、构建记录 | 部分满足：Vue 3 最小切片、内存 JWT、2 秒轮询、终态停止、5 字段引用展示已实现并经 Vitest 与构建验证；审批页待 Phase 2 |
| 部署 | Compose 构建、健康检查、备份恢复演练 | 部分满足：PostgreSQL/Redis 健康检查已验证；API/Worker 容器与 Dockerfile 已配置 |
| 量化指标 | 可下载 eval artifact + Git SHA | 部分满足：已产出 `phase1-before.json` 与 `phase1-after.json`，带完整 Git SHA 与元数据对比（仅供阶段回归，不作最终发布） |

当某行证据全部存在后，才能新增对应完成时 bullet。不能先写 bullet 再等待实现追上文案。

---

## 4. 完成后的项目叙事结构

本节只规定叙事结构，不提供带占位符的简历句子。

### 4.1 第一条：Agent Orchestration

回答：

- 为什么需要多 Agent，而不是单 Agent；
- 四个角色如何按权限拆分；
- Dispatcher 如何校验路由；
- checkpoint 和 Worker 如何提供恢复能力；
- 对应的路由或恢复证据在哪里。

### 4.2 第二条：Tool Governance

回答：

- Tool Schema 如何生成和校验；
- 白名单、Policy、审批和执行网关的顺序；
- PostgreSQL 幂等账本与 Redis 锁分别解决什么；
- 敏感结果如何加密、脱敏和审计；
- 失败注入证明了什么。

### 4.3 第三条：Human-in-the-loop

回答：

- Action 为什么分 propose 和 execute；
- Approval 如何绑定 `operation_id + args_hash`；
- interrupt 后如何从持久化 checkpoint 恢复；
- 重复审批和重复 resume 如何被阻止；
- 高风险操作未绕过审批的实测证据。

### 4.4 第四条：Evaluation

回答：

- Golden labels 为什么人工审核；
- Handoff Accuracy 如何避免 Agent 序列歧义；
- Offline Eval 与 Live Eval 的职责区别；
- baseline 为什么不能由普通 CI 自动更新；
- 指标、数据集和模型版本如何追溯。

### 4.5 第五条：Full-stack Delivery

回答：

- FastAPI API 与 ARQ Worker 为什么分离；
- PostgreSQL、Redis、Chroma 各自的职责；
- Vue 页面如何消费 OpenAPI 生成类型；
- Docker Compose、migration、健康检查和 CI 如何组成可复现交付。

---

## 5. 面试问答

以下答案描述已批准的设计。实现完成前，应明确说“设计为”，不要说“已经在生产验证”。

### Q：为什么不用单 Agent？

工单分类、知识检索、业务执行和安全授权具有不同权限边界。让一个 Agent 同时拥有这些能力，会让 Prompt 注入或错误规划直接获得写权限，也难以区分路由、工具和策略错误。AgentDesk 把它们拆为 Supervisor、Knowledge、Action 和 Policy，并在代码层分别限制状态写入和 Tool 权限。

### Q：四个角色都需要 LLM 吗？

不需要。Supervisor、Knowledge 和 Action 需要语言理解；Policy 的最终 allow/deny、风险等级和审批要求由纯函数计算。使用多 Agent 不等于所有决策都交给模型，安全边界必须确定性和可测试。

### Q：为什么用 LangGraph？

核心需求包含显式条件图、节点级 checkpoint、人工 interrupt 和跨请求 resume。LangGraph 与这些需求直接匹配。选择依据是持久化工作流能力，不是单纯因为它是 Agent 框架。

### Q：为什么 API 和 Worker 分开？

工单运行可能包含多次模型调用、工具超时和长时间审批。API 只创建记录并返回 HTTP 202，Worker 执行图，可以独立重启和恢复。这样 HTTP 生命周期不会承载长任务，也不会把进程内后台任务误当成可靠队列。

### Q：为什么从 SQLite 改为 PostgreSQL？

API、Worker、审批和 checkpoint 会跨进程并发访问状态。PostgreSQL 提供事务、条件更新、唯一约束和 migration，适合作为工单、审批、幂等账本和 checkpoint 的事实源。SQLite 适合本地原型，但不符合本项目选择的进程边界和可靠性目标。

### Q：如何避免重复退款或重复重置密码？

每个逻辑写操作拥有稳定 `operation_id`，已验证参数产生 `args_hash`，两者与 Tool 名共同形成幂等键。PostgreSQL 操作账本以该键做唯一约束，Redis 只负责短期执行锁。即使 Worker Job 被重新投递，执行器也会读取历史状态，而不是再次产生副作用。

### Q：为什么 Redis 不是幂等事实源？

Redis 锁可能过期或被清空，只适合并发协调。最终状态和加密结果必须落在 PostgreSQL 账本中，才能在 Redis 丢失后判断操作是否已经成功。

### Q：审批如何保证“批的是这一笔，执行的也是这一笔”？

ApprovalRecord 绑定 `operation_id` 和 `args_hash`。恢复执行前重新计算参数哈希并核对审批记录。MVP 不允许审批时修改参数；如果参数改变，旧操作作废并重新发起审批。

### Q：如何防止 Prompt injection？

输入检测只用于告警和保守路由。真正的安全边界是 Tool Registry、Agent 白名单、参数 Schema、确定性 Policy、审批凭证和执行网关。Dispatcher 负责图转移，不负责 Tool 授权。

### Q：为什么生产 Registry 不保留 `delete_user`？

一个永远不允许执行的危险函数没有必要出现在生产可执行面。权限测试可以请求未知或不在白名单中的 Tool，并验证网关拒绝、审计和零副作用。这样测试安全边界时不需要注册真实危险能力。

### Q：RAG 为什么不直接放在 Knowledge Agent 里访问 Chroma？

Knowledge 必须通过 `search_knowledge` Tool，才能统一经过 Schema、白名单和审计。领域层只依赖 `KnowledgeStore` Protocol，Chroma Adapter 位于服务层并通过依赖注入提供，避免 `tools/` 反向 import `backend/`。

### Q：Offline Eval 和 Live Eval 有什么区别？

Offline Eval 使用 Fake 或 Recorded Model，适合稳定验证图、Schema 和失败路径；它不能证明 Prompt 质量。Live Eval 使用真实 OpenAI-compatible 模型，负责衡量分类、路由、工具选择和回复质量，并记录模型、Prompt、数据集和 Git 版本。

### Q：为什么不默认自托管 Langfuse？

本项目需要 Agent Trace，但不需要为了 Trace 默认引入一整套观测平台依赖。结构化日志和 PostgreSQL 审计是基础能力，Langfuse 作为可选外部导出端。这样保留可观测价值，同时控制单机部署范围。

---

## 6. 实测结果登记规则

完成 Live Eval 后，可在本节新增结果，但每条必须包含：

```text
run_id
artifact 路径或 CI 链接
Git SHA
数据集版本
Prompt 版本
模型名
运行时间
样本数量
指标
```

如果缺少其中任一项，不得把结果转写为简历成绩。

当前没有可登记的实测结果。

---

## 7. 项目完成定义

只有同时满足以下条件，才能把 AgentDesk 作为“已完成项目”写入简历：

- Docker Compose 从空环境构建并通过健康检查；
- 正常查询、普通写、高风险审批三条路径 E2E 通过；
- Worker 重启、重复 resume 和 Tool 超时等失败模式通过；
- OpenAPI 类型无漂移；
- 完整 Golden Dataset 和人工审查 baseline 已提交；
- Live Eval 达到 `AGENTS.md` 的门槛；
- 简历中的每个量化数字都能链接到 artifact。

在此之前，优先继续实现和测量，不包装尚不存在的成果。
