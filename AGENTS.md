# AGENTS.md — AgentDesk AI 开发约束与协作规范

> 面向所有参与本仓库开发的 AI 编码 Agent 与人工开发者。
>
> 文档版本：v2.0  
> 对应设计：`docs/architecture.md` v2.0  
> 项目状态：作品集级生产化系统，当前仍处于设计与契约落地阶段

---

## 0. 文档地位与导航

- 本文件是仓库中最高优先级的**开发约束**，规定不可违反的边界、代码落点、同步规则和质量门禁。
- 系统行为和数据流以 `docs/architecture.md` 为设计依据；机器可检查的 Agent、Tool 和 API 契约以 `specs/` 为准。
- 优先级：`AGENTS.md`（开发约束）> `specs/`（机器契约）> `docs/architecture.md`（设计说明）> `README.md`（使用说明）> 代码注释。
- 约束与设计冲突时，先由人工确认修改本文件，再修改契约、设计与代码；不得让代码静默偏离文档。
- 修改本文件属于契约级变更，必须由人工确认。
- “五份主文档”指下面的项目治理文档，不限制业务模板等 Markdown 资源文件。

| 文档 | 职责 |
|---|---|
| `README.md` | 项目价值、当前真实状态、快速开始、环境和接口概览 |
| `AGENTS.md` | 开发硬约束、目录落点、变更流程、测试和评估门禁 |
| `docs/architecture.md` | 完整系统设计和工程决策 |
| `specs/README.md` | 机器契约索引和冻结决策 |
| `PORTFOLIO.md` | 只保存有证据支撑的简历素材与面试准备 |

---

## 1. 项目坐标

AgentDesk 是一个模拟企业 IT Service Desk 的多 Agent 工作流系统。它以模拟数据运行，但必须具备可开发、可测试、可恢复、可审计、可评估和可部署的完整工程链路。

目标不是构建企业级工单产品，而是交付一个介于 Demo 与真实产品之间的**作品集级生产化系统**。

四个核心能力：

| 能力 | 代码归属 |
|---|---|
| Agent Orchestration | `agents/`、`backend/graph/` |
| Tool Governance | `tools/`、`backend/governance/` |
| Human-in-the-loop | `agents/policy.py`、`backend/graph/`、`backend/api/approvals.py` |
| Evaluation | `eval/`、`.github/workflows/eval.yml` |

进程边界：

- `api`：FastAPI，只处理认证、校验、持久化初始记录、入队和查询。
- `worker`：ARQ Worker，唯一允许启动或恢复 LangGraph 的进程。
- API 与 Worker 共享 PostgreSQL 和 Redis，但不得共享进程内状态。
- Worker 是运行进程，不是第五个 Agent。

任何改动前必须回答：

1. 改动服务于哪个核心能力？
2. 代码落在哪个既有模块？
3. 改动是否改变机器契约、状态所有权、权限或评估口径？

---

## 2. 硬约束

### H-01：Agent 角色固定为四个

仅允许 `supervisor`、`knowledge`、`action`、`policy` 四个逻辑角色，不新增 Planner、Critic、Reflection、Memory 等 Agent。

`agents/*.py` 封闭清单：

```text
supervisor.py
knowledge.py
action.py
policy.py
schemas.py
context.py
```

Supervisor、Knowledge、Action 可以使用模型完成核心职责。Policy 的最终判定必须由确定性规则完成；如需生成面向用户的解释，只能在判定完成后调用统一模型适配层，且模型输出不得覆盖判定字段。

### H-02：Supervisor 永不调用工具

- `AGENT_TOOL_WHITELIST["supervisor"] == []`。
- Supervisor 只做分类、下一角色选择和最终汇总。
- Supervisor 不生成或执行工具函数名，不分配基础设施权限。

### H-03：状态字段单一写入

每个 LangGraph 节点只返回自己拥有的字段，完整所有权见 §2.1。跨节点并发写同一字段属于缺陷。

### H-04：路由由确定性 Dispatcher 完成

- Supervisor 可以通过结构化输出建议 `next_agent`。
- `backend/graph/edges.py` 必须校验允许的转移并执行分支。
- `edges.py` 内不得调用 LLM，也不得执行 Tool。
- 非法目标、非法转移或超过 `MAX_STEPS` 必须转入明确终态。

### H-05：写操作必须经过统一执行网关

所有 `side_effects == "write"` 的 Tool 必须依次经过：Schema 校验、白名单、Policy/Approval 校验、幂等、超时/重试、执行、脱敏和审计。

- 唯一入口：`backend/governance/executor.py`。
- Tool Registry 只暴露 `ToolSpec`，不得把可执行函数直接交给 Agent。
- 每个逻辑写操作拥有独立且持久化的 `operation_id`。
- `operation_id` 位于 `PendingOperation`，不得使用一个工单级 ID 代表多个写操作。

### H-06：白名单是代码安全边界

白名单校验必须发生在执行网关中，且早于任何工具实现调用。Prompt、输入清洗和注入检测都不能代替白名单。

### H-07：最大步数固定为 15

`MAX_STEPS = 15`，由 Dispatcher 统一增加 `step_count`。达到上限后工单进入 `escalated`，不得继续调用模型或工具。

### H-08：所有写操作先过 Policy，高风险必须审批

写操作的固定顺序：

```text
Action propose
→ Policy
→ low/medium 且允许：Action execute
→ high：interrupt → Approval → Action execute
```

- Action 在 Policy 前只能生成 `PendingOperation`，不能产生写副作用。
- `risk_level == "high"` 时必须满足 `require_human=True`。
- 审批通过前执行网关必须拒绝高风险写操作。
- `process_refund` 金额达到 `REFUND_APPROVAL_THRESHOLD_USD` 时必须命中高风险路径。

### H-09：审批绑定操作内容

`ApprovalRecord` 必须绑定 `operation_id` 与 `args_hash`。恢复执行前必须重新计算并核对两者。

- MVP 只允许 approve/reject，不允许审批人编辑参数。
- 参数变化必须作废旧操作并创建新的 `PendingOperation` 和审批。
- 审批更新使用 `status + version` 乐观锁，重复决定不得重复入队。

### H-10：Policy 最终判定不得依赖 LLM

`assess_risk` 和授权规则必须是纯函数。LLM 可以生成解释文本，但不能决定 allow/deny、最终风险等级或是否需要审批。

### H-11：Prompt 使用绝对约束语言

约束必须使用 `must not`、`never`、`only` 等绝对表述，不得出现 `shouldn't`、`try to avoid`、`尽量` 等建议式表述。

### H-12：API 类型由 OpenAPI 生成

- FastAPI 请求/响应使用 Pydantic v2 模型。
- `frontend/src/types/api.d.ts` 由 `openapi-typescript` 生成。
- 前端不得手写 API DTO，也不得用 `any` 绕过契约。

### H-13：测试不得弱化

不得放宽断言、跳过用例、mock 掉被测核心逻辑、在测试中复制实现分支或使用概率性故障注入。

### H-14：Tool Schema 固定为八个字段

每个 Tool 必须声明：

```text
name
description
parameters
result_schema
side_effects
risk_level
idempotent
timeout_s
```

字段名 `timeout_s` 是唯一合法写法。读取工具也必须显式声明 `idempotent=true`。

### H-15：Tool 描述必须可独立判断风险

描述必须写清读/写、副作用、必填参数、返回结构、敏感数据和可能触发审批的条件。

### H-16：Skill 是确定性模板

- 只实现 `password_reset` 一个 Skill。
- 步骤和 `on_failure` 在 Python 定义时固定。
- LLM 只能选择 Skill，不能决定或改写内部步骤。
- Skill 内所有 Tool 调用仍走 Action 权限和统一执行网关。
- 生成通知文本可以是纯逻辑；真实发送通知必须注册为写 Tool。

### H-17：Knowledge 只读且引用可回查

Knowledge 只能调用 `search_knowledge`，只能追加 `knowledge_refs`。每条引用必须包含 `document_id`、`chunk_id`、`title`、`content_excerpt`、`score`。

### H-18：每次工具尝试都必须审计

成功、失败、拒绝、超时和缓存命中都必须生成 `ToolCallRecord`，至少包含：

```text
request_id
ticket_id
trace_id
operation_id（如适用）
agent_id
tool_name
args_summary
result_summary
outcome
latency_ms
retry_count
timestamp
```

审计由执行网关统一写入，不允许业务函数旁路。

### H-19：敏感值不得进入可观测数据

密码、临时口令、令牌、卡号和密钥不得以明文进入日志、审计、Trace、异常消息或 Langfuse。

- 重试所需的敏感结果只能加密存储在操作账本中。
- 审计和 Trace 只保存掩码、哈希或摘要。
- 脱敏必须在数据离开执行网关前完成。

### H-20：实测结果必须可追溯

任何作为成果、前后对比或简历成绩出现的指标数字，都必须来自 eval artifact，并能追溯数据集版本、模型配置、时间和 Git SHA。

- 设计阈值必须明确标注为“门槛”，不能写成实测成绩。
- 不得保留百分比、数值或待办式简历占位符。
- 未实现或未运行的简历 bullet 必须删除，而不是改写成完成时。

### H-21：失败模式必须有确定性测试

新增或修改失败处理时，必须同步 `tests/test_failure_modes.py`。每个用例必须使用固定调用序列或固定故障点，并断言最终工单状态。

### H-22：领域层不得反向依赖服务层

依赖方向：

```text
backend → agents / tools / skills
```

`agents/`、`tools/`、`skills/` 内不得 import `backend.*`、FastAPI、数据库客户端、Redis 客户端或 ARQ。

外部能力通过领域层 Protocol 注入。例如 `tools/service.py` 只能依赖 `KnowledgeStore` Protocol，具体 Chroma Adapter 在 `backend/integrations/` 中实现并由执行网关注入。

### H-23：模型调用必须经过统一适配层

- 默认协议为 OpenAI-compatible API。
- Agent 不得直接实例化供应商 SDK 客户端。
- 统一入口位于 `backend/integrations/model_client.py`。
- 测试和离线 CI 必须能注入确定性 `FakeModelClient`。

### H-24：API 不运行 Agent 图

FastAPI 只能创建记录并提交 `start_ticket` / `resume_ticket` Job。只有 Worker 可以调用 LangGraph。

- 创建工单返回 HTTP 202。
- 队列消息只携带 ID，不携带用户查询、密码或完整操作参数。
- ARQ 序列化必须使用 JSON，不得使用 pickle。

### H-25：PostgreSQL 是持久化事实源

- 工单、审批、审计、工具操作账本和 LangGraph checkpoint 使用 PostgreSQL。
- Redis 只用于队列、分布式锁和短期缓存，不是写操作结果的唯一事实源。
- Schema 变更必须通过 Alembic migration，不允许应用启动时隐式建表。

### H-26：不引入负面清单组件

禁止完整 RBAC、多租户、真实企业接口、Kubernetes/HA、第二个 Skill、额外 Agent、用户画像、跨工单团队记忆、WebSocket 和自定义设计系统。

---

## 2.1 TicketState 字段所有权

| 字段 | 类型概念 | 唯一写入方 |
|---|---|---|
| `ticket_id` | UUID/string | API 入口 |
| `thread_id` | string，默认等于 ticket_id | API 入口 |
| `requester_id` | string | API 入口 |
| `user_query` | string | API 入口 |
| `trace_id` | string | API 入口 |
| `status` | `TicketStatus` | Dispatcher |
| `intent` | `Intent` | Supervisor |
| `routing_decision` | `RoutingDecision` | Supervisor |
| `knowledge_refs` | `list[KnowledgeRef]`，只追加 | Knowledge |
| `pending_operation` | `PendingOperation | None` | Action propose 节点 |
| `tool_results` | `list[ToolResult]`，只追加 | Action execute 节点 |
| `risk_decision` | `RiskDecision | None` | Policy |
| `approval` | `ApprovalSnapshot | None` | Approval 节点 |
| `agent_history` | `list[AgentStep]`，只追加 | Dispatcher |
| `step_count` | int | Dispatcher |
| `resolution_mode` | `automated / fallback / human_required` | Supervisor finalizer |
| `final_response` | `str | None` | Supervisor finalizer |

状态类型由 `backend/graph/state.py` 引用 `agents/schemas.py` 和 `tools/schemas.py` 组成。表外字段不得临时塞入裸字典；需要新增字段时先修改本表、架构和测试。

当前节点、重试调度信息和 checkpoint id 属于 LangGraph/ARQ 元数据，不进入 `TicketState`。

---

## 3. 目录落点规则

| 改动 | 代码落点 | 必须同步 |
|---|---|---|
| Agent 行为 | 既有 `agents/{supervisor,knowledge,action,policy}.py` | Prompt、Agent contract、架构 §7、eval |
| Agent/图共享模型 | `agents/schemas.py` | `specs/agent_contracts/`、状态所有权、测试 |
| Agent 外部依赖 Protocol | `agents/context.py` | backend 实现、Agent 测试 Fake |
| Tool 入参/出参模型 | `tools/schemas.py` | Tool Schema、Registry、测试 |
| 新 Tool | `tools/<domain>.py` + `tools/registry.py` | Schema 快照、白名单、架构、测试 |
| Tool 外部依赖 Protocol | `tools/context.py` | backend Adapter、测试 Fake |
| Tool 执行治理 | `backend/governance/` | 治理测试、失败模式、架构 §9 |
| 图节点 | `backend/graph/nodes.py` | 状态所有权、路由测试 |
| 图边/Dispatcher | `backend/graph/edges.py` | `tests/test_routing.py`、架构流程图 |
| 图构建 | `backend/graph/builder.py` | Checkpointer、集成测试 |
| Worker Job | `backend/jobs/` | Compose、失败恢复测试、README |
| PostgreSQL 模型 | `backend/db/models.py` | Alembic migration、API Schema、测试 |
| Chroma/模型 Adapter | `backend/integrations/` | 对应 Protocol、环境变量、Fake |
| API 路由 | `backend/api/<domain>.py` | OpenAPI 生成物、前端 API、README |
| JWT 与身份 | `backend/auth/` | API 依赖、认证测试、安全说明 |
| Prompt | `prompts/<agent>.yaml` | live eval 前后对比、Agent contract |
| Password Reset Skill | `skills/password_reset/` | Action 白名单、Skill 测试 |
| Golden Case | `eval/golden_dataset.json` | 数据集版本、指标覆盖 |
| 指标 | `eval/metrics.py` | Eval runner、Dashboard、门禁 |
| 基线 | `eval/baseline.json` | 人工审查的 eval artifact |
| 前端页面 | `frontend/src/views/` | Router、Vitest |
| 前端 API | `frontend/src/api/` | 生成的 `types/api.d.ts` |
| CI | `.github/workflows/` | README 状态、门禁规则 |

禁止新增第二个 Skill 或第五个 Agent。此类需求必须先修改本文件并获得人工确认。

---

## 4. 编码规范

### 4.1 Python

- Python ≥ 3.11，行宽 100，Ruff + mypy。
- 所有函数必须有类型标注；禁止裸 `Any` 作为跨边界兜底。
- LLM 输出、Tool 入参出参、API DTO、审批、审计和操作提案都使用 Pydantic v2。
- 禁止裸 `except:`；异常必须分类为可重试、业务拒绝或未知失败。
- import 时不得联网、连接数据库、创建表或启动 Worker。
- 所有时间使用 UTC aware datetime；API 输出 ISO 8601。
- 金额使用 `Decimal`，禁止用浮点数处理退款金额。
- 枚举使用 `StrEnum` 或 `Literal`，不得散落自由字符串。
- 常量必须命名：`MAX_STEPS`、退款阈值、超时、重试次数、锁 TTL、审批 TTL。
- 顶层 `agents/`、`tools/`、`skills/`、`backend/` 使用命名空间包，不创建 `__init__.py`。

### 4.2 TypeScript / Vue

- Vue 3、`<script setup lang="ts">`、Composition API。
- 禁止 `any` 和手写 API DTO。
- 所有 HTTP 经过 `frontend/src/api/client.ts`。
- API 错误统一映射 `error.code`，页面不解析任意后端字符串。
- 只有跨页面共享且需轮询的状态进入 Pinia。
- 敏感结果离开页面或刷新后不得保留在 localStorage。

### 4.3 Prompt

Prompt YAML 固定字段：

```text
version
role
responsibility
constraints
output_schema
```

Prompt 版本必须进入 Trace 和 eval artifact。Prompt 不得包含密钥、真实个人数据或绕过代码权限的说明。

---

## 5. 异步执行与一致性

- `POST /api/tickets` 在 PostgreSQL 成功创建工单后入队，返回 HTTP 202。
- 入队失败时不得把工单留在不可解释的 `queued` 状态；使用事务后补偿或 outbox 模式，具体实现见架构 §11。
- `start_ticket` 的 Job ID 必须由 ticket_id 确定性生成，重复提交不得创建两个逻辑运行。
- `resume_ticket` 的 Job ID 必须包含 approval_id 和 approval version。
- Worker 重试只覆盖基础设施异常；Tool 重试由执行网关控制。
- Worker 重启后必须从 PostgreSQL checkpoint 恢复，不从头重复写操作。
- PostgreSQL 操作账本使用唯一 `idempotency_key`；Redis 锁只负责并发互斥。

---

## 6. 测试要求

### 6.1 单元测试

必须覆盖：

- 五类 intent 与非法路由；
- Policy 风险矩阵和退款金额边界；
- Tool Schema、白名单和参数校验；
- `operation_id`、`args_hash`、幂等键稳定性；
- 审批乐观锁与重复决定；
- 脱敏函数；
- 所有确定性 eval 指标；
- Password Reset Skill 的固定步骤和失败策略。

### 6.2 集成测试

- PostgreSQL migration 能从空库升级到最新版本。
- API 创建工单后只入队，不直接运行图。
- Worker 能启动、暂停和恢复图。
- Tool ledger 在重复执行时返回历史结果且不重复产生副作用。
- Redis 锁超时后可由账本状态安全恢复。
- Chroma Adapter 返回可回查引用。
- OpenAPI 生成类型与仓库文件一致。

### 6.3 失败模式测试

`tests/test_failure_modes.py` 至少覆盖：

| 编号 | 故障 | 期望结果 |
|---|---|---|
| F-01a | 可选只读 Tool 连续超时 | 达到上限后 resolved + fallback |
| F-01b | 关键写 Tool 连续超时 | 达到上限后 escalated |
| F-02 | LLM 连续输出非法结构 | 两次修复失败后 escalated |
| F-03 | 图循环 | 第 15 步停止并 escalated |
| F-04 | 未知或越权 Tool | rejected，且有审计 |
| F-05 | 审批超过 TTL | expired，不能恢复执行 |
| F-06 | Prompt injection 请求越权操作 | 治理层拒绝，零副作用 |
| F-07 | Worker 在 checkpoint 后崩溃 | 重启后恢复，不重复写 |
| F-08 | 重复审批或重复 resume | 只产生一次状态转移和一次执行 |

每个失败模式必须断言终态、审计记录和副作用次数。

### 6.4 测试替身

- 单元测试使用 `tests/fakes/` 中的 Fake Model、Fake Redis、Fake Knowledge Store 和内存账本。
- 集成测试使用 CI service containers 中的真实 PostgreSQL/Redis；不得把集成行为全替换成 mock。
- 故障注入按“第 N 次调用失败”等确定序列实现，禁止概率故障。

---

## 7. Evaluation 门禁

### 7.1 数据集

- 少于 20 条只能称为开发样例，不得发布指标。
- 20–79 条只能称为冒烟评估。
- 至少 80 条且覆盖正常、边界和 F-01～F-08 后，才可称为完整评估。
- LLM 可生成查询变体，但 expected labels 必须人工审核。

Golden Case 必含：`case_id`、`query`、`requester_id`、`expected_intent`、`expected_handoffs`、`expected_tools`、`expected_risk`、`expected_require_human`、`expected_outcome`、`relevant_document_ids`、`min_business_steps`、`reference_answer`、`tags`。

### 7.2 确定性门槛

以下数字是设计门槛，不是当前成绩：

| 指标 | 门槛 |
|---|---:|
| Intent Accuracy | ≥ 90% |
| Handoff Accuracy | ≥ 90% |
| Tool Selection Jaccard | ≥ 85% |
| Policy Decision Accuracy | 100% |
| High-risk Bypass Count | 0 |
| Task Success Rate | ≥ 80% |
| 轨迹效率 | 不低于基线 |
| 冗余工具调用率 | 不高于基线 |
| P95 延迟 | 不高于基线的 120% |

Retrieval Precision/Recall 根据 `relevant_document_ids` 确定性计算。Faithfulness 可以使用独立 LLM evaluator，但在至少 50 条人工样本上相关性未达到 0.85 前，不得进入门禁或简历。

### 7.3 两类 Eval

- PR 离线冒烟：Fake/Recorded Model，验证契约、轨迹和失败路径。
- Live Eval：真实 OpenAI-compatible 模型，记录模型、Base URL 标识、Prompt 版本、数据集版本、Git SHA、时间和成本。

Recorded Model 不能证明 Prompt 质量；涉及 Prompt、Agent、Tool、Policy、Graph 或 Eval 的行为变化，合并前必须产生 live eval 前后对比。

### 7.4 基线

- 普通 CI 只读 `eval/baseline.json`，不得自动更新。
- 只有显式 `run_eval --update-baseline` 才能生成候选基线。
- 基线修改必须人工审查并与原始 eval artifact 一起提交。

---

## 8. 变更同步规则

| 改动 | 必须同步 |
|---|---|
| `TicketState` | §2.1、架构 §6、Agent contracts、状态测试 |
| Agent Schema/输出 | Prompt、Agent contracts、OpenAPI（如暴露）、live eval |
| Tool 增删或 Schema | Registry、Tool JSON、白名单、测试、架构工具表 |
| Policy 规则 | 风险矩阵、失败测试、Golden Cases、live eval |
| 图边 | 路由测试、handoff labels、架构流程图、live eval |
| API | OpenAPI 类型、前端 API、README 接口表、集成测试 |
| 数据库模型 | Alembic migration、API/Repository、迁移测试 |
| Worker Job | Compose、重试测试、README 服务说明 |
| Prompt | Contract、Prompt 版本、live eval 前后对比 |
| Eval 指标 | Runner、Baseline Schema、Dashboard、本文门禁 |
| 失败模式 | `test_failure_modes.py`、架构失败矩阵 |
| 依赖 | `pyproject.toml`/`package.json`、lockfile、README |
| 主文档 | 相关交叉链接和版本号 |

---

## 9. CI 与验证

目标状态下，从仓库根执行：

```powershell
ruff check agents tools skills backend eval tests
mypy agents tools skills backend eval
pytest tests -q

Set-Location frontend
npm ci
npm run lint
npm run type-check
npm run test
npm run build
```

集成和 Eval：

```powershell
docker compose up -d postgres redis
alembic upgrade head
pytest tests/integration -q
python eval/run_eval.py --mode offline
```

完整服务：

```powershell
docker compose up --build
```

在当前代码尚未实现对应入口时，不得把这些命令的预期结果描述为“已通过”。

CI 工作流固定职责：

- `backend-ci.yml`：Ruff、mypy、pytest、migration、契约一致性。
- `frontend-ci.yml`：ESLint、Vue TSC、Vitest、build、OpenAPI 漂移。
- `eval.yml`：离线冒烟 + 受保护的 live eval。

---

## 10. 提交与 PR

提交格式：

```text
<type>(<scope>): <summary>
```

`type`：`feat | fix | refactor | test | docs | chore | eval`。  
`scope`：`graph | agents | tools | governance | jobs | db | auth | api | eval | frontend | docs | ci`。

PR 必须包含：

1. 改动与架构能力归属；
2. 受影响的状态、Tool、Agent、API 或数据库契约；
3. 实际执行的测试命令与原始摘要；
4. 触发行为变化时的 live eval 前后对比；
5. 文档和生成物同步情况；
6. migration、回滚和敏感数据影响。

不得写未经运行的“已通过”“零故障”或百分比成果。

---

## 11. 负面清单

- 完整 RBAC、权限配置后台和组织树
- 多租户
- 真实企业系统或支付接口
- Kubernetes、高可用和多区域部署
- 每个 Agent 独立微服务
- 第五个 Agent
- 第二个 Skill
- 三层记忆、团队记忆、用户画像
- WebSocket
- 审批人直接编辑工具参数
- LLM 决定最终授权或风险
- 在生产 Tool Registry 注册永远不可调用的 `delete_user`
- 自托管完整 Langfuse 基础设施作为默认 Compose 依赖
- 自定义设计系统
- 把计划功能或设计门槛写成已实现成绩

---

## 12. 术语

| 术语 | 含义 |
|---|---|
| Agent | 四个职责与权限隔离的图角色，不等于独立进程 |
| Dispatcher | 校验并执行图转移的确定性代码 |
| PendingOperation | Action 提出的尚未执行的写操作 |
| operation_id | 单个逻辑写操作的稳定标识 |
| idempotency_key | `tool + operation_id + args_hash` 的唯一键 |
| ApprovalRecord | 与 operation_id/args_hash 绑定的审批事实 |
| Tool Governance | Schema、白名单、Policy、审批、幂等、可靠性、审计 |
| Checkpoint | LangGraph 在 thread_id 下保存的可恢复状态 |
| Ticket projection | API 查询用的 PostgreSQL 工单读模型，可由 checkpoint/事件修复 |
| Fallback | 保守处理方式，不是工单终态 |
| Escalated | 自动流程停止，需要人工接管的终态 |
| Golden Case | 人工审核标签的评估样本 |
| Live Eval | 使用真实 OpenAI-compatible 模型的评估运行 |

---

**文件状态**：v2.0，已按“模块化单体 + 独立 Worker + PostgreSQL + Redis + LangGraph”方案重构。任何实现必须先满足本文件，再宣称可运行或可写入简历。
