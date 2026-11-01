# AgentDesk｜简历表达与证据清单

> 本文件是简历、作品集与面试表达的唯一来源，不替代设计或验收报告。
> 设计：[`docs/architecture.md`](docs/architecture.md) · 约束：[`AGENTS.md`](AGENTS.md) · 当前证据：[`docs/acceptance/current.md`](docs/acceptance/current.md)
> 更新时间：2026-10-27（以当前工作树为准；每个数字的口径见 §3）
> 用法：§1 直接粘简历；§2 用于作品集/面试展开；§3 是数字的证据索引；§4 是绝对不能说的话。

---

## 0. 一句话定位

**AgentDesk 是一个“可治理、可恢复、可度量”的多 Agent IT 工单工作流系统：用确定性编排约束 LLM 路由，用统一执行网关约束工具副作用，用 PostgreSQL checkpoint 与幂等账本保证故障恢复，用分层评估证明系统行为。**

这句话适合放在简历项目标题下、GitHub README 首段或自我介绍开场。它回答的不是“我用了哪些框架”，而是“我如何把 LLM 的不确定性关进工程边界”。

---

## 1. 一页简历版（推荐直接使用）

**AgentDesk｜可治理的多 Agent IT 工单工作流** ｜ 个人作品集项目 ｜ 2026.09 – 2026.10

技术栈：Python 3.11 · FastAPI · LangGraph · PostgreSQL · Redis / ARQ · Chroma（RAG） · Vue 3 · Docker Compose

规模：4 个 Agent 角色 · 8 个注册工具 · 1 个确定性 Skill · 5 类意图 / 6 类工单场景 · 80 条人工审核评估样例 · 325 项测试

项目简介：从 0 到 1 设计并实现一套可治理的 Agent 工单工作流，重点解决 LLM 进入真实业务后的三个工程问题——**路由不可控、副作用不可逆、效果不可度量**。

- **多 Agent 编排**：按“权限边界”而非拟人化拆分 Supervisor / Knowledge / Action / Policy 四个 Agent，Supervisor 不持有任何工具权限；以确定性 Dispatcher 校验每一次状态转移、集中维护 `step_count` 并设 15 步硬上限，避免 LLM 自由路由导致的越权、漂移与死循环。
- **工具治理**：所有写操作收敛到唯一执行网关，按 Schema 校验 → 角色白名单 → Policy/审批回执 → PostgreSQL 幂等账本 → Redis 执行锁 → 超时重试 → 脱敏审计执行；以 `operation_id + args_hash` 绑定审批，保证“批的是这一笔、执行的也是这一笔”，重复投递不产生二次副作用。
- **高风险审批与恢复**：基于 LangGraph `interrupt / Command(resume)` 实现 ≥200 USD 退款人工审批，审批前只生成 `PendingOperation`；用 TTL 过期校验（读取时惰性收敛）、`status + version` 乐观锁与确定性 Job ID 处理审批过期、重复决策与重复恢复；Worker 崩溃后从 checkpoint 续跑，写操作至多一次。
- **分层评估体系**：构建 80 条人工审核 Golden Dataset、9 项确定性指标（意图 / Handoff / 工具选择 / Policy / 高风险绕过 / 任务成功 / 轨迹效率 / 冗余调用 / P95）与 F-01a~F-08 故障矩阵；offline 用 Fake 模型做契约回归，live 走真实模型 + 真实图 + PostgreSQL 轨迹，结果全部带 run_id、数据集版本、Prompt 版本与 Git SHA。
- **结果与迭代**：80 例 live E2E 候选评估中意图识别 93.8%、Policy 决策 100%（24 项观测，覆盖率 82.8%）、高风险绕过 0、冗余工具调用 0、轨迹效率 96.5%；针对首轮仅 17.5% 的任务成功率，基于逐例 Trace 归因定位 8 类根因并完成修复，复跑提升至 63.8%（3.6×）。
- **全栈交付**：交付 Vue 3 五页工作台（登录 / 工单 / 审批 / Trace / Eval）与 Docker Compose 全栈编排；OpenAPI 生成前端类型杜绝手写 DTO，CI 覆盖 ruff、mypy、pytest 与契约漂移检查，本地 325 项单元与 Chroma 集成测试通过。

### 1.1 极简版（版面紧张时用这一段）

**AgentDesk｜多 Agent IT 工单工作流（个人项目）**：以四角色权限隔离 + 确定性 Dispatcher + 统一工具执行网关构建可治理的 Agent 工作流；用 PostgreSQL checkpoint 与幂等账本实现故障恢复和写操作至多一次；搭建 80 例 Golden Dataset 与 9 项确定性指标，80 例 live E2E 中高风险绕过 0、冗余工具调用 0，任务成功率 17.5% → 63.8%。

### 1.2 按岗位裁剪建议

| 目标岗位 | 优先保留的 bullet | 可以弱化 |
|---|---|---|
| AI Agent / LLM 应用 | 1、2、4、5 | 6 |
| 后端 / 平台工程 | 2、3、6 | 1 的角色细节 |
| 全栈 / 交付 | 3、6、4 | 2 的网关细节 |

---

## 2. 作品集 / 面试详细版

### 2.1 项目背景与定位

AgentDesk 模拟企业 IT Service Desk 的完整工单闭环：自然语言请求进入后，经过意图分类、知识检索、工具调用、风险判定、高风险审批、断点恢复和结果汇总。它使用模拟数据，不连接真实企业账户、订单或支付接口；目标不是做企业级 SaaS，而是交付一个介于 Demo 与真实产品之间的**作品集级生产化系统**。

系统采用模块化单体：API 与 ARQ Worker 进程隔离，LangGraph 只在 Worker 中执行；PostgreSQL 承载工单投影、审批、审计、操作账本和 checkpoint；Redis 只做队列、锁和短期缓存；Chroma 由 Knowledge Tool 只读访问。

### 2.2 系统架构

```text
Browser → Vue/Nginx → FastAPI → PostgreSQL + Redis/ARQ → Worker/LangGraph
                                                            ├─ Supervisor（分类 / 路由建议，零工具权限）
                                                            ├─ Knowledge → search_knowledge → Chroma
                                                            ├─ Action → 只读工具 / PendingOperation
                                                            └─ Policy（纯函数风险判定）→ Approval
```

关键进程边界：

- API 只负责认证、校验、落库、写 Outbox 和查询，创建工单返回 HTTP 202，不运行 Agent 图；
- 只有 Worker 能启动或恢复 LangGraph；
- 写操作必须经过统一执行网关，任何 Agent 都不能直接调用工具实现。

### 2.3 六个技术亮点（问题 → 方案 → 证据）

**① 多 Agent 不是为了角色扮演，而是为了权限与评估边界**

单 Agent 同时拥有检索、执行和授权权限时，Prompt 注入或一次错误规划就可能直接变成业务副作用，而且无法区分“分类错”“工具选错”“策略判错”。AgentDesk 把流程拆成四个职责与权限隔离的角色：Supervisor 只做分类与路由建议，Knowledge 只有 `search_knowledge`，Action 只能提案或调用白名单工具，Policy 用纯函数做最终 allow/deny。Supervisor 的工具白名单是空数组。

**② 确定性 Dispatcher：把路由从 LLM 手里拿走**

Supervisor 可以建议 `next_agent`，但每一次转移都必须经过 `edges.py` 的合法转移校验；Dispatcher 统一维护 `step_count`，第 15 步强制进入 `escalated`，不再调用模型或工具。这样即使模型输出漂移，图也不会越权或无限循环。

**③ Action 两阶段：提案与执行分离**

Action 在 Policy 之前只能生成 `PendingOperation`，不能产生写副作用；Policy 通过后才进入执行网关，高风险操作则先 `interrupt` 等待审批。这个拆分让“想做什么”和“允许做什么”在代码层彻底分开。

**④ 统一执行网关：全链路管线 + 幂等账本**

写操作固定经过：Registry 查找 → 入参模型校验 → 角色白名单 → Policy/审批回执 → PostgreSQL 操作账本 → Redis 执行锁 → 超时/条件重试 → 工具实现 → 敏感结果加密 → 脱敏 → 审计。幂等键为 `sha256(tool_name + operation_id + args_hash)`，PostgreSQL 唯一约束是最终事实源，Redis 锁只负责缩短竞争窗口。成功、失败、拒绝、超时、缓存命中都会生成 `ToolCallRecord`。

**⑤ Human-in-the-loop：审批绑定操作内容，而不是工单号**

`ApprovalRecord` 绑定 `operation_id` 与 `args_hash`；恢复执行前重新计算并核对两者，参数变化必须作废旧操作、重新提案。审批更新使用 `status + version` 乐观锁，重复决策不会重复入队；`resume:{approval_id}:{version}` 作为确定性 Job ID，重复 resume 只产生一次状态转移。

**⑥ 分层评估：门槛是门槛，结果是结果**

评估分两层：offline 用 Fake/Recorded Model 验证图、Schema 和失败路径；live 用真实 OpenAI-compatible 模型跑完整图，经过 Tool Gateway、PostgreSQL 和 checkpoint。指标计算全部是确定性函数，不让 LLM 当裁判；每个 artifact 记录数据集版本、模型、Prompt 版本、运行时间、Git SHA 和工作树指纹。设计门槛（Intent ≥90%、Handoff ≥90%、Tool Jaccard ≥85%、Task Success ≥80% 等）与实测结果分表展示。

### 2.4 评估驱动的迭代（面试主线，建议重点讲）

这是项目里最能体现工程能力的一段，不要只讲“做了什么”，要讲“怎么发现、怎么修、怎么验证”。

**第一轮（2026-10-23，80 例 live E2E）**：任务成功率只有 17.5%，44 条工单被升级。面对一份“看起来很差”的 artifact，先从逐例 CSV、Trace 和代码三条线做归因，而不是直接调 Prompt。

**定位到 8 类根因**：

| # | 根因 | 证据 |
|---|---|---|
| 1 | 知识引用在检索后被清空：只保留 `chunk_id` 完全相等的引用，模型给空数组或拿 document_id 冒充就得到空列表 | `src/agents/knowledge.py` + 31/44 次升级 |
| 2 | 知识索引缺 20/23 篇预期文档：fixture 只有 5 篇 | `src/backend/fixtures/knowledge.json` |
| 3 | 写执行网关用 `resource=None` 重算 Policy，自服务重置被误拒 | `nodes.py` / `executor.py` |
| 4 | high_risk 写请求被直接 finalize，没先走 Action | Supervisor Prompt 1.0.8 缺规则 |
| 5 | 记录查询被路由到 Knowledge | 11 条 handoff 分歧 |
| 6 | 完成后多跑一个节点 | 13 条 handoff 分歧 |
| 7 | `check_service` 中文服务名未解析 | `_ALIASES` 只有 6 个英文别名 |
| 8 | 确定性拒绝没有 `rejected` 出口，全部落 `escalated` | 三处 route 均无 rejected 分支 |

**修复与复跑**：补齐知识 fixture（25 篇 / 51 chunk）、修正引用校验、让 policy 节点与 action_execute 复用同一 `ResourceContext`、新增 `reject_request` 终态、补中文别名、升级 Supervisor Prompt 至 1.0.9+；同时修掉 vLLM 侧的 Schema 上下文溢出——完整 JSON Schema 被同时塞进 Prompt 和 `response_format`，4,936 tokens 超过 4,096 上限，改为只保留一份并去掉 grammar 不支持的 look-around 正则。

**第二轮（2026-10-24 07:42）**：任务成功率 17.5% → 68.8%，原先报错的 40 条工单中 38 条恢复正常，整轮运行错误降至 1 条。**随后（10:31）**修复 retrieval 指标字段派生，最新一轮 Task Success 为 63.8%，Retrieval Precision / Recall 为 42.4% / 83.3%（不同轮次 Prompt 版本与失败构成不同，指标不可简单相加）。

**当前仍差什么**：Handoff 73.5%（门槛 90%）、Tool Selection 75.0%（门槛 85%）、P95 16.4s（门槛 10s），完整 E2E 门禁尚未通过。这部分如实讲，反而比“全部达标”更可信。

### 2.5 结果与当前边界

- 已验证的工程能力：四角色权限隔离、确定性路由、统一写网关、绑定审批、checkpoint 恢复、幂等账本、分层评估、325 项测试通过。
- 已跑出的候选指标：Intent 93.8%、Policy 100%（24 项观测 / 覆盖率 82.8%）、High-risk Bypass 0、Redundant Tool Call 0、Trajectory Efficiency 96.5%、Task Success 63.8%。
- 尚未达成：完整 live E2E 门禁（Handoff、Tool Selection、Task Success、P95 未过线）；因此对外只能说“候选运行”，不能说“验收通过”。
- 不适用范围：真实企业系统接入、真实支付、多租户、完整 RBAC、Kubernetes/HA。

---

## 3. 量化结果与证据索引

> 规则：简历里每出现一个数字，都必须能在这张表里找到对应 artifact。没有证据的数字不写。

### 3.1 测试与工程质量（2026-10-27 当前工作树）

| 声明 | 数值 | 证据 / 命令 | 口径 |
|---|---:|---|---|
| 单元 + Chroma 集成测试 | 325 passed | `uv run pytest tests/unit tests/integration/test_chroma_knowledge.py -q` | 不含需要 PostgreSQL/Redis 的全量集成与失败验收 |
| Ruff | 通过 | `uv run ruff check src tests scripts` | — |
| mypy | 通过 | `uv run mypy src/agents src/tools src/skills src/backend src/eval scripts` | — |
| 单元测试模块 | 37 个 | `tests/unit/test_*.py` | 模块数，不等于用例数 |
| 集成测试模块 | 22 个 | `tests/integration/test_*.py` | 需真实 PostgreSQL/Redis 才可执行 |
| 失败模式测试文件 | 8 个 | `tests/acceptance/failure_modes/test_*.py` | 覆盖 F-01a/b、F-02~F-08 |

### 3.2 三轮 80 例 live E2E 候选运行

| 指标 | 首轮 2026-10-23 | 修复轮 2026-10-24 07:42 | 最新轮 2026-10-24 10:31 | 设计门槛 |
|---|---:|---:|---:|---:|
| Run ID | `d2b8e0cf2347` | `69fd59f627fa` | `e16cf561dfd7` | — |
| Intent Accuracy | 95.00% | 98.75% | 93.75% | ≥90% |
| Handoff Accuracy | 46.25% | 70.88% | 73.54% | ≥90% |
| Tool Selection Jaccard | 67.08% | 77.29% | 75.00% | ≥85% |
| Policy Decision Accuracy | 100%（16 项） | 96.00%（25 项） | 100%（24 项） | 100% |
| Policy Coverage | 55.17% | 86.21% | 82.76% | 参考项 |
| High-risk Bypass | 0 | 0 | 0 | 0 |
| Task Success Rate | 17.50% | 68.75% | 63.75% | ≥80% |
| Trajectory Efficiency | 98.13% | 96.88% | 96.46% | ≥70% |
| Redundant Tool Call | 0 | 0 | 0 | ≤20% |
| Retrieval Precision / Recall | 0 / 0（度量缺陷） | 0 / 0（度量缺陷） | 42.36% / 83.33% | 口径保留 |
| P95 Latency | 13,495 ms | 16,261 ms | 16,402 ms | ≤10,000 ms |
| 门禁结论 | 未通过 | 未通过 | 未通过 | — |

**Artifact 路径**

- 首轮：`artifacts/evaluation/candidates/qwen3-30b-a3b-instruct-2507-awq-e2e80-final-20261023.local.json`
- 修复轮：`artifacts/evaluation/candidates/qwen3-30b-a3b-e2e80-fixes-20261024.json`
- 最新轮：`artifacts/evaluation/candidates/qwen3-30b-a3b-e2e80-tracefix3-20261024.json`
- 逐例失败表：`artifacts/evaluation/candidates/qwen3-30b-a3b-e2e80-fixes-20261024-failures.csv`
- 诊断报告：`docs/acceptance/diagnosis-20261023.md`
- 当前验收结论：`docs/acceptance/current.md`

**运行元数据**

| 项目 | 值 |
|---|---|
| 数据集 | `1.0.0`，reviewed / full，80/80 条 |
| 模型 | `Qwen3-30B-A3B-Instruct-2507-AWQ`，本地 vLLM OpenAI-compatible |
| 并发 | 4（实测最大 4） |
| Prompt 版本 | 首轮 supervisor 1.0.8；最新轮 action 1.0.6 / knowledge 1.0.1 / policy 1.0.0 / supervisor 1.0.11 |
| Git SHA | 首轮 `dee52a2`；修复轮与最新轮 `aef79142`（工作树非 clean） |
| 计费口径 | `local_vllm_no_provider_billing` |

### 3.3 项目规模类事实

| 事实 | 值 | 来源 |
|---|---:|---|
| Agent 角色 | 4 个 | `AGENTS.md` H-01 |
| 注册工具 | 8 个 | `specs/tool_schemas/tools.json` |
| 确定性 Skill | 1 个（password_reset） | `src/skills/password_reset/` |
| 最大步数 | 15 | `AGENTS.md` H-07 |
| 退款审批阈值 | 200 USD | `AGENTS.md` H-08 |
| 知识库 | 25 篇 / 51 chunk | `src/backend/fixtures/knowledge.json` |
| 前端页面 | 5 个 | `frontend/src/views/` |
| CI 工作流 | 3 个 | `.github/workflows/` |

---

## 4. 表达红线（必须遵守）

1. **不得说“已通过完整 E2E 门禁”**。三轮候选运行的 `threshold_gate.passed` 均为 `false`；最新轮 Handoff 73.5%、Tool Selection 75.0%、Task Success 63.8%、P95 16.4s 均未过线。
2. **不得把设计门槛写成实测成绩**。`AGENTS.md` §7.2 的数字是门槛；简历只能引用 §3 表里的实测值。
3. **不得引用旧 Gate C / `l3-live-final.json` / `phase1-after.json` 作为成绩**。这些是历史工件，其中旧“E2E”指标由 expected labels 补齐，已被当前验收否定。
4. **不得说“生产级 / 已上线 / 接入真实企业系统”**。项目使用模拟数据，不连接真实账户、订单或支付。
5. **不得说“零故障 / 零缺陷”**。可以说“高风险绕过 0、冗余工具调用 0”，这是两个具体指标，不是系统级结论。
6. **不得把 325 项测试说成“全量测试”**。它只覆盖单元测试 + Chroma 集成；需要 PostgreSQL/Redis 的集成与失败验收未在该命令内执行。
7. **不得脱离覆盖率单独说“Policy 100%”**。必须同时给出“24 项观测 / 覆盖率 82.8%”，否则会被理解为写路径全覆盖。
8. **不得引用首轮 Retrieval 0 / 0 作为检索能力结论**。该字段存在派生缺陷；最新轮已修复为 42.36% / 83.33%。
9. **被问“达到门槛了吗”**：如实回答“尚未，最新一轮 Task Success 63.8%、Handoff 73.5%，仍在迭代；但首轮到最新轮已从 17.5% 提升 3.6 倍，且高风险绕过始终为 0”。

---

## 5. 面试问答

### Q1：为什么不用单 Agent？

工单分类、知识检索、业务执行和安全授权具有不同权限边界。让一个 Agent 同时拥有这些能力，会让 Prompt 注入或错误规划直接获得写权限，也难以区分路由、工具和策略错误。AgentDesk 把它们拆为 Supervisor、Knowledge、Action 和 Policy，并在代码层分别限制状态写入和 Tool 权限。

### Q2：四个角色都需要 LLM 吗？

不需要。Supervisor、Knowledge 和 Action 需要语言理解；Policy 的最终 allow/deny、风险等级和审批要求由纯函数计算。使用多 Agent 不等于所有决策都交给模型，安全边界必须确定性和可测试。

### Q3：为什么用 LangGraph？

核心需求包含显式条件图、节点级 checkpoint、人工 interrupt 和跨请求 resume。LangGraph 与这些需求直接匹配。选择依据是持久化工作流能力，不是单纯因为它是 Agent 框架。

### Q4：为什么 API 和 Worker 分开？

工单运行可能包含多次模型调用、工具超时和长时间审批。API 只创建记录并返回 HTTP 202，Worker 执行图，可以独立重启和恢复。这样 HTTP 生命周期不会承载长任务，也不会把进程内后台任务误当成可靠队列。

### Q5：如何避免重复退款或重复重置密码？

每个逻辑写操作拥有稳定 `operation_id`，已验证参数产生 `args_hash`，两者与 Tool 名共同形成幂等键。PostgreSQL 操作账本以该键做唯一约束，Redis 只负责短期执行锁。即使 Worker Job 被重新投递，执行器也会读取历史状态，而不是再次产生副作用。

### Q6：审批如何保证“批的是这一笔，执行的也是这一笔”？

`ApprovalRecord` 绑定 `operation_id` 和 `args_hash`。恢复执行前重新计算参数哈希并核对审批记录。MVP 不允许审批时修改参数；如果参数改变，旧操作作废并重新发起审批。

### Q7：如何防止 Prompt injection？

输入检测只用于告警和保守路由。真正的安全边界是 Tool Registry、Agent 白名单、参数 Schema、确定性 Policy、审批凭证和执行网关。Dispatcher 负责图转移，不负责 Tool 授权。

### Q8：为什么 Redis 不是幂等事实源？

Redis 锁可能过期或被清空，只适合并发协调。最终状态和加密结果必须落在 PostgreSQL 账本中，才能在 Redis 丢失后判断操作是否已经成功。

### Q9：这个项目最难的部分是什么？

不是把 Agent 跑通，而是让它在失败时仍然可控：路由要确定、写操作要幂等、审批要绑定内容、崩溃后要能恢复、指标要可追溯。最难的是把这些约束同时放进一个系统，并且用确定性测试证明它们没有被绕过。

### Q10：Task Success 只有 63.8%，为什么还敢写进简历？

因为它是真实跑出来的，而且有完整的诊断和提升链路：首轮 17.5%，通过逐例 Trace 归因出 8 类根因，修复后到 63.8%，高风险绕过始终为 0。简历不写“达标”，写的是“可复现的迭代能力”。如果面试官追问门槛，我会如实说还没过 80%，并说明下一步优先修 Handoff 和 P95。

### Q11：怎么证明这不是一个 Demo？

三个层面：第一，真实进程边界（API / Worker / PostgreSQL / Redis / Chroma）和真实图执行，不是 notebook；第二，F-01a~F-08 确定性故障注入，覆盖超时、非法模型输出、循环、越权工具、审批过期、注入、Worker 崩溃、重复恢复；第三，80 例人工审核数据集 + 9 项确定性指标 + 带 Git SHA 的 artifact，所有结论可回查。

### Q12：如果重做，你会先改什么？

先把 Handoff 和 Tool Selection 做上去：当前 Prompt 规则已经覆盖大部分场景，但模型仍会在“完成后多跑一步”和“记录查询走 Knowledge”上分歧；我会用逐例错误聚类 + few-shot 示例，而不是继续堆全局规则。其次是减少模型往返以降低 P95，最后才是扩数据集。

### Q13：为什么不用 AutoGen / CrewAI 这类现成框架？

这个项目的核心诉求是确定性路由、持久化 checkpoint、可中断审批和可审计的工具治理。现成 Agent 框架更擅长“让 Agent 自由协作”，但会把路由和权限藏在框架内部，难以做状态所有权和失败路径的确定性测试。LangGraph 只提供图与 checkpoint，路由、Policy 和执行网关仍然由我控制，边界更清楚。

---

## 6. 经过核实的历史工件边界

- `artifacts/evaluation/history/offline-fake-baseline.json`：离线确定性 Fake，不是模型质量或延迟基线。
- `artifacts/evaluation/history/historical-offline-fake-l3-final.json`：虽曾以“live”命名，内容实际是离线 Fake。
- `artifacts/evaluation/history/phase1-after.json` 和 `phase2-after.json`：旧运行器把 expected labels 用作 E2E 指标，不可引用其轨迹或成绩。
- `artifacts/evaluation/candidates/`：新 Live run 输出目录；候选状态必须保留，只有人工审阅后才能讨论晋升。
- 旧版 PORTFOLIO 中“Gate C All Passed / Phase 1-3 全量通过”的表述已被当前验收否定，不得再使用。

上述归档判断来自对工件 stage、mode、分母与旧运行器行为的读取；它不替代当前运行结果。

---

## 7. 当前证据状态

逐层结果只以 [`docs/acceptance/current.md`](docs/acceptance/current.md) 中真实执行的命令、退出码和工件为准。验收结束前，不预先给以下能力写“全量通过”结论：

| 能力 | 所需证据 | 当前状态 |
|---|---|---|
| Agent Orchestration | 路由/状态所有权单测、真实图集成和逐例节点轨迹 | 单测与候选轨迹已有；完整 live 门禁未通过 |
| Tool Governance | Schema/白名单/Policy 单测、数据库账本及副作用断言、确定性拒绝审计 | 单测与账本实现已有；需 PostgreSQL 集成复验 |
| Human-in-the-loop | interrupt、绑定审批、resume 与重复操作集成证据 | 实现与单测已有；需隔离库集成复验 |
| Recovery | F-07 故障点后的持久化审计/账本、恢复终态与单次副作用证据 | 测试模块存在；需 PostgreSQL/Redis 环境执行 |
| Evaluation | 80 条完整固定样例的真实 `e2e-candidate`、门槛结果及追溯元数据 | 三轮候选已跑；门槛未通过 |
| Frontend | 固定 Node 22.22.2 下的 lint、类型检查、Vitest、生产构建 | 代码与页面已有；需按固定版本复验 |
| Compose | API/Worker/Frontend 镜像构建和运行时 readiness/Smoke | 配置已有；需完整构建复验 |
| Disaster recovery | `pg_dump`/`pg_restore` 的真实备份恢复和 schema/数据逐表比较 | 脚本已有；需真实演练 |

---

## 8. 证据登记规则

新增任何量化表述前，必须补齐：

```text
run_id
artifact 路径
数据集版本与样本数
模型 / Base URL 标识
Prompt 版本
运行时间
Git SHA 与工作树摘要
实际指标、分母、门槛结论
```

缺少任一项，不得写入简历或对外材料。候选 artifact 不自动晋升为官方基线；晋升必须人工审阅。
