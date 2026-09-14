# AgentDesk 项目表达与证据清单

> 本文件用于管理简历素材和面试叙事，不是系统设计文档。
>
> 文档版本：v2.0
> 当前结论：Phase 0 契约基线已落地、最终验收进行中；完整项目尚未完成
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

截至 Phase 0 契约闭合：

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
- 初始化 Git，并以独立功能分支保留可审查的 Phase 0 提交历史。

### 尚未完成

- Agent 行为、业务 Tool、Graph、Policy、Worker 和 API 业务实现；
- PostgreSQL Schema 和 Alembic migrations；
- JWT 身份与审批接口；
- 前端页面；
- E2E、集成测试和确定性失败注入；
- 完整 80 条 Golden Dataset、baseline、Live Eval 和任何可发布实测指标；
- 包含 frontend/api/worker 的完整 Docker Compose 可运行栈；
- 三个 CI 工作流及其实际运行记录。

因此当前可以展示为“已完成契约基线、正在开发业务纵切的个人工程项目”，不能写成“设计并实现了完整系统”。

---

## 3. 简历证据门禁

| 候选能力 | 写入简历前必须具备的证据 | 当前状态 |
|---|---|---|
| 多 Agent 编排 | 可运行图、路由测试、真实 Trace | 未满足 |
| 独立 Worker | HTTP 202、队列记录、Worker 重启恢复测试 | 未满足 |
| Tool Governance | Registry、白名单、Policy、幂等账本、审计测试 | 部分满足：Registry、白名单和 Schema 已有 |
| Human-in-the-loop | interrupt、审批记录、resume、重复恢复测试 | 未满足 |
| RAG | 可回查引用、检索评估产物 | 未满足 |
| Evaluation | 人工审核数据集、baseline、带元数据的 Live Eval | 部分满足：20 条人工审核冒烟样本已提交 |
| 前端 | 可访问页面、OpenAPI 生成类型、构建记录 | 部分满足：OpenAPI 生成类型已提交 |
| 部署 | Compose 构建、健康检查、备份恢复演练 | 部分满足：PostgreSQL/Redis 健康检查已验证 |
| 量化指标 | 可下载 eval artifact + Git SHA | 未满足 |

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
