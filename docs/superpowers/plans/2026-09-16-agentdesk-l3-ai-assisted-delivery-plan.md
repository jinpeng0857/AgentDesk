# AgentDesk L3 AI-Assisted Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 30 个有效工作日内，把 AgentDesk 从“Phase 1 只读纵切基础”推进到有真实运行、恢复、治理、HITL、失败注入、完整评估、前端演示和 Compose 部署证据的 L3 简历可投递版本。

**Architecture:** 延续仓库冻结的模块化单体：FastAPI 只做认证、持久化、入队和查询，ARQ Worker 独占 LangGraph 启动与恢复，PostgreSQL 是工单、审批、审计、操作账本和 checkpoint 的事实源，Redis 仅用于队列、锁和短期缓存。四个逻辑角色固定为 Supervisor、Knowledge、Action、Policy；所有写 Tool 必须通过统一执行网关，Policy 的最终判定完全确定性，高风险操作通过持久化审批与 checkpoint resume 恢复。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic v2、LangGraph、ARQ、PostgreSQL、Redis、Alembic、Chroma、OpenAI-compatible API、Vue 3、TypeScript、Vite、Pinia、Vitest、Docker Compose、GitHub Actions；可选外部 Langfuse。

## Global Constraints

- `AGENTS.md` v2.0 是最高优先级，不修改它来迁就实现；契约冲突必须先停下并由人工确认。
- 只允许 `supervisor`、`knowledge`、`action`、`policy` 四个 Agent，只允许 `password_reset` 一个 Skill。
- `MAX_STEPS = 15`，由确定性 Dispatcher 写入 `step_count`；Supervisor 永不调用 Tool。
- API 不运行图；只有 Worker 可以启动或恢复 LangGraph；ARQ 消息只携带 ID 且使用 JSON。
- PostgreSQL 是唯一持久化事实源；不得改用 SQLite checkpointer；Redis 不保存唯一业务事实。
- 写操作固定经过 Schema、白名单、Policy/Approval、幂等、超时/重试、执行、脱敏和审计。
- 高风险退款审批前必须零副作用；审批绑定 `operation_id + args_hash`，重复审批和重复 resume 不得重复执行。
- Tool Schema 继续保持八字段；前端 DTO 只从 OpenAPI 生成，不手写、不使用 `any`。
- 新增或修改失败处理必须同步 `tests/test_failure_modes.py`，故障注入必须使用固定调用序列。
- 每次行为变化先有失败测试；提交前运行相关测试；阶段结束才运行完整门禁。
- Prompt、Agent、Tool、Policy、Graph 或 Eval 行为变更合并前必须有同配置的 live eval 前后对比。
- 80 条以上且人工审核的数据集才叫完整评估；门槛不是成绩，简历数字只能来自带 Git SHA 的 artifact。
- Langfuse 是可选外部 Trace 导出端，不进入默认 Compose；PostgreSQL Audit 与 Trace API 才是治理证据源。
- 不实现完整 RBAC、多租户、真实企业接口、Kubernetes/HA、第五个 Agent、第二个 Skill、WebSocket 或自定义设计系统。

---

## 1. 当前基线与排期假设

### 1.1 2026-09-16 实测起点

本计划制定时，工作区为 clean，实际执行结果如下：

```text
uv run pytest tests/unit -q                         130 passed in 10.55s
uv run ruff check ...                               passed
uv run mypy agents tools backend eval scripts       passed, 54 source files
Tool/OpenAPI snapshot                               current
Agent/Prompt contracts                              valid
Golden Dataset                                      20 reviewed smoke cases
frontend npm run type-check                         passed
```

这些证据只证明当前契约、单元测试和类型检查，不证明图运行、HITL、完整前端、完整 Compose 或真实模型质量。

### 1.2 时间模型

- 总周期：30 个有效工作日，建议 6 周、每周 5 天。
- 每天投入：约 6 个专注小时；若每天只有 3 小时，应按 60 个工作日执行，不压缩验收。
- Day 01、Day 07、Day 22、Day 30 是硬门禁日；门禁失败时，后续日编号不前移。
- 计划不以“写了多少代码”计进度，而以可复跑的测试、服务、artifact 和页面演示计进度。

### 1.3 每日 AI 协作协议

每天严格采用同一节奏：

1. **人类给 AI 上下文**：当天目标、允许修改的文件、禁止项、上一日验收记录。
2. **AI 先检查**：读取 `AGENTS.md`、相关契约和测试；列出状态所有权、依赖方向与可能的契约影响。
3. **AI 先写失败测试**：先运行并保存预期失败摘要，再实现最小代码。
4. **AI 自检**：运行相关 Ruff、mypy、pytest、生成物漂移检查；不得只解释代码。
5. **人类审查**：重点审查授权边界、状态所有权、敏感数据、测试是否复制实现、指标是否夸大。
6. **形成证据**：当天最后一个提交必须可独立回滚；把命令、摘要、Git SHA 和未解决项写入当日验收记录。

建议每次给 AI 的任务模板：

```text
今天执行 Day XX，只允许修改计划列出的文件。
先读取 AGENTS.md、相关 specs 和既有测试，先写失败测试并运行确认失败。
实现后运行当天列出的验收命令，不得修改或弱化测试，不得修改 AGENTS.md。
最后报告：改动文件、测试原始摘要、未验证项、是否改变契约/权限/状态所有权/评估口径。
```

每个工作日创建 `docs/evidence/l3/day-XX.md`，固定记录：目标、变更文件、测试命令与原始摘要、服务实测、人工检查、Git SHA、遗留问题。这个文件只记录已经发生的事实，不预填“通过”。

---

## 2. 文件与职责地图

| 工作单元 | 主要文件 | 责任边界 |
|---|---|---|
| 图状态与路由 | `backend/graph/state.py`、`context.py`、`nodes.py`、`edges.py`、`builder.py` | 状态初始化、节点闭包、确定性转移、15 步上限、checkpoint |
| Worker | `backend/jobs/context.py`、`tickets.py`、`worker.py` | 只通过 ID 启动/恢复图，区分业务终态与基础设施重试 |
| Tool Governance | `backend/governance/executor.py`、`idempotency.py`、`audit.py` | 白名单、验证、Policy/Approval、幂等、重试、脱敏、审计 |
| Policy/HITL | `agents/policy.py`、`backend/api/approvals.py`、`backend/db/repositories/approvals.py` | 纯函数风险判定、审批乐观锁、TTL、resume 入队 |
| 写 Tool | `tools/ticket.py`、`tools/account.py`、`backend/integrations/tool_adapters.py` | 四个模拟写能力，禁止旁路执行网关 |
| 唯一 Skill | `skills/password_reset/skill.py`、`validate_user.py`、`notification_template.md` | 固定步骤与固定失败策略，模型只选择不改写 |
| 数据模型 | `backend/db/models.py`、`alembic/versions/*` | 操作账本、审批、业务写结果、eval run 的 PostgreSQL 事实 |
| API | `backend/api/tickets.py`、`approvals.py`、`eval.py`、`schemas.py` | 所有权隔离、稳定错误码、生成 OpenAPI |
| Eval | `eval/golden_dataset.json`、`metrics.py`、`run_eval.py`、`baseline.json`、`artifacts/` | 80 条人工标签、确定性指标、可追溯离线/Live 产物 |
| 前端 | `frontend/src/views/`、`api/`、`stores/`、`router/` | Login、Ticket、Approval、Trace、Eval；只使用生成 DTO |
| 部署与 CI | `docker-compose.yml`、`backend/Dockerfile`、`frontend/Dockerfile`、`.github/workflows/` | 一键构建、健康检查、三类 CI、受保护 Live Eval |
| 证据表达 | `README.md`、`docs/architecture.md`、`PORTFOLIO.md`、`docs/evidence/l3/` | 当前状态、架构同步、简历声明边界、每日原始证据 |

---

## 3. 30 个工作日执行计划

## Week 1 — 解除门禁并完成只读 Agent Runtime

### Day 01：冻结真实模型基线，解除 Phase 1 阻塞

**改动/交付文件**

- Create: `eval/artifacts/phase1-before.json`
- Create: `docs/evidence/l3/day-01.md`
- No change: Prompt、Agent、Tool、Graph 业务代码

**执行**

- [ ] 人工在本机环境注入 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，密钥不写入文件和 Git。
- [ ] 运行 `git status --short`，必须无输出。
- [ ] 运行 `uv run python eval/run_eval.py --mode live --stage model-baseline --output eval/artifacts/phase1-before.json`。
- [ ] 检查 artifact 含 dataset version、model、base URL 标识、四个 Prompt version、UTC、Git SHA、case result 和失败码。
- [ ] 提交 artifact 与验收记录：`eval(eval): record phase one model baseline`。

**验收目标**

- Live runner 真实调用 OpenAI-compatible 服务，20 条结果可解析；失败也必须有明确分类。
- artifact 不含 API key、完整 Base URL 凭据或用户敏感数据。
- 如果凭据缺失或端点不可用，Day 01 判定为阻塞；不得伪造 artifact，也不得开始 Graph 行为集成。

### Day 02：图状态、Dispatcher 与字段所有权

**改动/交付文件**

- Create: `backend/graph/state.py`
- Create: `backend/graph/context.py`
- Modify: `backend/graph/edges.py`
- Create: `tests/unit/test_routing.py`
- Create: `tests/unit/test_state_ownership.py`
- Create: `docs/evidence/l3/day-02.md`

**执行**

- [ ] 先为所有合法/非法转移、终态不可变、低置信度升级和第 15 步停止写失败测试。
- [ ] 为各节点允许返回的键集合写状态所有权测试。
- [ ] 实现 `initial_ticket_state(record)`、`advance_dispatch(state, *, actor, action)` 和纯路由函数。
- [ ] 确认 `edges.py` 不 import ModelClient 或 Tool callable。
- [ ] 运行 `uv run pytest tests/unit/test_routing.py tests/unit/test_state_ownership.py -q`，再运行 Ruff 与 mypy。

**验收目标**

- 第 15 步精确进入 `escalated`，之后不再调用模型或 Tool。
- Dispatcher 是 `status`、`agent_history`、`step_count` 的唯一写入方。
- 所有非法目标和非法转移都有明确终态及固定测试。

### Day 03：节点闭包与只读 LangGraph

**改动/交付文件**

- Modify: `backend/graph/nodes.py`
- Create: `backend/graph/builder.py`
- Create: `tests/integration/test_graph_read_only.py`
- Create: `docs/evidence/l3/day-03.md`

**执行**

- [ ] 用固定 FakeModelClient 序列写 `Supervisor → Knowledge → Supervisor finalizer` 失败测试。
- [ ] 用依赖注入闭包实现 Supervisor、Knowledge、Action、Policy 节点。
- [ ] 构建图并使用 `thread_id=ticket_id`；Knowledge 只追加验证过的引用。
- [ ] 断言最终 `resolved`、引用五字段完整、写 Adapter 调用数为零、步数有界。
- [ ] 运行图集成测试和领域依赖边界测试。

**验收目标**

- Case A“VPN 无法连接”在 Fake 模型下完整跑通并带可回查引用。
- Supervisor 的 Tool 白名单仍为空；Knowledge 只能调用 `search_knowledge`。
- 图节点不写入不属于自己的 TicketState 字段。

### Day 04：Worker 独占执行与 PostgreSQL checkpoint 恢复

**改动/交付文件**

- Modify: `backend/jobs/context.py`
- Modify: `backend/jobs/tickets.py`
- Modify: `backend/jobs/worker.py`
- Modify: `docker-compose.yml`
- Create: `backend/Dockerfile`
- Create: `tests/integration/test_worker_recovery.py`
- Create: `docs/evidence/l3/day-04.md`

**执行**

- [ ] 写固定故障点：Supervisor checkpoint 后第一次 Knowledge 调用失败。
- [ ] 实现 `TicketRunner.run(ticket_id)`，已有 checkpoint 时以 `None` 恢复，终态直接返回。
- [ ] 用长生命周期 psycopg pool 创建 `AsyncPostgresSaver`；不在运行时建表。
- [ ] 将 `api` 与 `worker` 加入 Compose，同镜像不同命令，均依赖 PostgreSQL/Redis 健康检查。
- [ ] 重建 Worker 后以同一 ticket_id 恢复，断言 Supervisor 不重复分类且零写副作用。

**验收目标**

- API 模块不 import 或调用 Graph；Worker 是唯一图入口。
- 崩溃恢复从 PostgreSQL checkpoint 继续，不从头重跑已完成模型步骤。
- ARQ payload 仍只包含 ID，且 `start:{ticket_id}` 保持确定性。

### Day 05：工单查询与脱敏 Trace API

**改动/交付文件**

- Modify: `backend/api/schemas.py`
- Modify: `backend/api/tickets.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/db/repositories/tickets.py`
- Modify: `backend/main.py`
- Modify: `specs/openapi/openapi.json`
- Modify: `frontend/src/types/api.d.ts`
- Create: `tests/integration/test_ticket_queries.py`
- Create: `tests/integration/test_trace_api.py`
- Create: `docs/evidence/l3/day-05.md`

**执行**

- [ ] 先写请求者隔离、401/404、列表顺序、引用字段和敏感值不泄漏测试。
- [ ] 实现 list/detail/trace，只从 projection、trace event 和 audit record 组合响应，不直接暴露 checkpoint blob。
- [ ] 重新生成 OpenAPI 与 TypeScript 类型并执行漂移检查。
- [ ] 运行 API 集成测试、OpenAPI 测试和依赖边界测试。

**验收目标**

- 用户只能读取自己的工单；他人资源统一返回 404。
- Trace 时间有序且不出现 fixture password、Bearer token、临时口令或未掩码邮箱。
- 三个工单查询接口不再返回 501，审批和 Eval 端点仍明确保持 501。

## Week 2 — Phase 1 前端、CI 与真实纵切验收

### Day 06：Login 与 TicketSubmit 最小前端

**改动/交付文件**

- Create: `frontend/index.html`、`frontend/vite.config.ts`、`frontend/eslint.config.js`
- Create: `frontend/src/main.ts`、`App.vue`、`api/client.ts`、`api/types.ts`
- Create: `frontend/src/stores/auth.ts`、`stores/tickets.ts`、`router/index.ts`
- Create: `frontend/src/views/LoginView.vue`、`TicketsView.vue`
- Create: `frontend/src/views/LoginView.test.ts`、`frontend/src/stores/tickets.test.ts`
- Create: `docs/evidence/l3/day-06.md`

**执行**

- [ ] 写测试证明 JWT 只在 Pinia 内存，密码提交后立即清空，`localStorage` 始终为空。
- [ ] 实现生成类型驱动的 ApiClient 和统一 `error.code` 映射。
- [ ] 实现工单创建、列表、2 秒轮询、网络退避、页面隐藏暂停和终态停止。
- [ ] 运行 `npm run lint`、`npm run type-check`、`npm run test`、`npm run build`。

**验收目标**

- 浏览器可完成登录、提交 VPN 工单、查看 queued/running/resolved 和引用。
- 刷新后敏感认证状态不保留；登出和卸载后无残留轮询。
- 前端没有手写 API DTO、`any` 或解析任意后端错误文本。

### Day 07：Phase 1 Smoke、CI 与 live 前后对比

**改动/交付文件**

- Create: `scripts/phase1_smoke.py`
- Create/Modify: `.github/workflows/backend-ci.yml`、`frontend-ci.yml`、`eval.yml`
- Modify: `eval/run_eval.py`
- Create: `eval/artifacts/phase1-after.json`
- Modify: `README.md`、`docs/architecture.md`、`PORTFOLIO.md`
- Create: `docs/evidence/l3/day-07.md`

**执行**

- [ ] 运行 `docker compose up -d --build postgres redis api worker`，迁移空库并播种演示数据。
- [ ] Smoke 脚本登录、提交 VPN 工单、确认 202、轮询到 resolved、校验五字段引用和零业务写审计。
- [ ] 重启 Worker，证明既有工单仍可查询且 checkpoint 恢复测试通过。
- [ ] 启用三条固定 CI；普通 CI 只跑 offline，live job 仅受保护手动触发。
- [ ] 用 Day 01 相同模型和端点运行 live candidate，与 `phase1-before.json` 比较。
- [ ] 通过完整 Phase 1 门禁后，才同步三份主文档状态。

**验收目标**

- Phase 1 Definition of Done 的 12 项同时满足。
- `phase1-after.json` 与 before 元数据可比；缺凭据、模型不一致或脏工作区均应拒绝运行。
- README 不把 20 条冒烟结果称为完整评估，也不声称写操作/HITL 已完成。

### Day 08：Phase 2 数据模型与空库迁移

**改动/交付文件**

- Create: `eval/artifacts/phase2-before.json`
- Modify: `backend/db/models.py`
- Create: `alembic/versions/20260916_0002_phase2_governance_hitl.py`
- Create: `backend/db/repositories/operations.py`
- Create: `backend/db/repositories/approvals.py`
- Modify: `tests/integration/test_migrations.py`
- Create: `tests/unit/test_operation_identity.py`
- Create: `docs/evidence/l3/day-08.md`

**执行**

- [ ] 在修改任何 Phase 2 行为、Prompt 或 Agent 前，从 Day 07 的 clean commit 运行 live baseline 并提交 `phase2-before.json`。
- [ ] 先写操作账本唯一键、`operation_id`/`args_hash` 稳定性、Approval 绑定与 version 乐观锁测试。
- [ ] 增加 Pending Operation、operation ledger、Approval Record 和必要业务写结果表。
- [ ] 使用 `Decimal`、UTC aware datetime 和数据库唯一约束；敏感结果只允许加密列。
- [ ] 从全新数据库运行 `alembic upgrade head` 并执行迁移结构测试。

**验收目标**

- 一个工单的多个写操作拥有不同且持久化的 `operation_id`。
- 幂等键严格等于 `tool + operation_id + args_hash` 的稳定派生。
- 应用启动不隐式建表，旧数据库升级和空库升级均成功。

### Day 09：确定性 Policy 风险矩阵

**改动/交付文件**

- Modify: `agents/policy.py`
- Modify: `agents/action.py`
- Modify: `prompts/action.yaml`、`prompts/policy.yaml`
- Modify: `specs/agent_contracts/action.yaml`、`policy.yaml`
- Create: `tests/unit/test_policy.py`
- Modify: `tests/unit/test_agents.py`
- Create: `docs/evidence/l3/day-09.md`

**执行**

- [ ] 写 read、create_ticket、request_access、reset_password、refund 199.99/200.00、跨用户、inactive account 和未知 Tool 的风险矩阵测试。
- [ ] 实现纯函数 `assess_risk` 与授权规则；LLM 只能在判定后生成解释。
- [ ] Action 在 Policy 前只创建 `PendingOperation`，不执行任何写调用。
- [ ] 校验 Prompt 使用 `must not`、`never`、`only` 等绝对约束语言。

**验收目标**

- 200.00 USD 达到高风险阈值且 `require_human=True`；199.99 USD 保持 medium。
- `allow/deny`、最终 risk 和 `require_human` 不依赖 LLM。
- 跨用户操作和未知/越权 Tool 确定性拒绝、零副作用。

### Day 10：统一写执行网关与幂等账本

**改动/交付文件**

- Modify: `backend/governance/executor.py`
- Modify: `backend/governance/idempotency.py`
- Modify: `backend/governance/audit.py`
- Create: `tests/unit/test_write_executor.py`
- Create: `tests/integration/test_operation_ledger.py`
- Create: `docs/evidence/l3/day-10.md`

**执行**

- [ ] 用 Recording Adapter 写白名单早于实现解析、无批准高风险拒绝、缓存命中、超时、重试耗尽测试。
- [ ] 按固定顺序实现 Schema → whitelist → Policy/Approval → ledger/lock → timeout/retry → execute → validate → redact → audit。
- [ ] 所有成功、失败、拒绝、超时和缓存命中写入完整 H-18 字段。
- [ ] 并发执行同一幂等键，断言一次副作用，其他调用读取历史结果。

**验收目标**

- Tool Registry 从不把 callable 直接交给 Agent。
- Redis 锁丢失后仍能由 PostgreSQL ledger 判断是否已经执行。
- 审计与异常中不出现密码、临时口令、token、卡号或密钥明文。

## Week 3 — 四个写 Tool、唯一 Skill 与 HITL

### Day 11：低风险 create_ticket 与 medium request_access

**改动/交付文件**

- Modify: `tools/ticket.py`
- Create: `tools/access.py`
- Modify: `backend/integrations/tool_adapters.py`
- Create: `tests/unit/test_write_tools.py`
- Create: `tests/integration/test_write_adapters.py`
- Create: `docs/evidence/l3/day-11.md`

**执行**

- [ ] 写本人请求、跨用户拒绝、重复 operation 和输出 Schema 测试。
- [ ] 实现模拟下游工单创建与权限申请；`request_access` 只创建申请，绝不直接授权。
- [ ] 所有调用经统一网关；Adapter 不自行做 Policy 或审计旁路。
- [ ] 对同一幂等键重复调用并核对数据库行数保持 1。

**验收目标**

- Case“创建后续排查工单”和“为本人申请权限”可完整 resolved。
- 跨用户权限申请被拒绝且业务表零新增。
- 返回结果、ledger 和 audit 三者可用 request/ticket/trace/operation ID 串联。

### Day 12：Password Reset Skill 与敏感结果保护

**改动/交付文件**

- Modify: `skills/password_reset/skill.py`、`validate_user.py`、`notification_template.md`
- Modify: `tools/account.py`
- Modify: `backend/integrations/tool_adapters.py`
- Create: `backend/governance/encryption.py`
- Create: `tests/unit/test_password_reset_skill.py`
- Create: `tests/integration/test_password_reset.py`
- Create: `docs/evidence/l3/day-12.md`

**执行**

- [ ] 写固定步骤、固定 `on_failure`、本人 active、他人账号、inactive account 和重复执行测试。
- [ ] 模型只选择 Skill；步骤顺序由 Python 定义且不可被模型改写。
- [ ] 临时口令只向授权请求者展示一次；ledger 重试所需值使用应用级加密。
- [ ] 扫描日志、审计、Trace、异常和数据库非加密列，确认无临时口令明文。

**验收目标**

- Case B `query_user → Policy → reset_password → Audit` 跑通。
- 重放同一 operation 不再次重置密码，且敏感值不重新展示给未授权调用方。
- 没有新增第二个 Skill，也没有把通知发送伪装成纯模板生成。

### Day 13：Refund 写 Tool 与金额边界

**改动/交付文件**

- Modify: `tools/ticket.py`
- Modify: `backend/integrations/tool_adapters.py`
- Create: `tests/unit/test_refund_policy.py`
- Create: `tests/integration/test_refund_execution.py`
- Create: `docs/evidence/l3/day-13.md`

**执行**

- [ ] 写订单所有权、可退款状态、版本、币种、金额上限、199.99 与 200.00 边界测试。
- [ ] 全链路使用 `Decimal`，禁止 float。
- [ ] medium 退款可以自动执行；high 退款在无批准时由网关拒绝。
- [ ] 重复 operation 和并发调用均断言退款业务记录只有一条。

**验收目标**

- 199.99 USD 合法请求完成一次模拟退款。
- 200.00 USD 创建待审批操作，退款表与外部模拟副作用计数均为 0。
- 订单版本或 args_hash 变化时旧批准不可复用。

### Day 14：Approval API、乐观锁与 TTL

**改动/交付文件**

- Modify: `backend/api/approvals.py`、`schemas.py`、`dependencies.py`
- Modify: `backend/db/repositories/approvals.py`
- Modify: `specs/openapi/openapi.json`、`frontend/src/types/api.d.ts`
- Create: `tests/integration/test_approval_api.py`
- Create: `docs/evidence/l3/day-14.md`

**执行**

- [ ] 写 approver 权限、列表/详情、approve/reject、版本冲突、重复决定、TTL 过期和禁止编辑参数测试。
- [ ] 实现 `status + version` 条件更新；第一次成功决定后才创建 resume outbox。
- [ ] 在 resume 入队前重新计算并核对 `operation_id + args_hash`。
- [ ] 重新生成 OpenAPI 和前端类型。

**验收目标**

- 两个并发决定只有一个成功，且只产生一个 resume outbox。
- 过期审批变为 `expired`，不能恢复执行。
- API 不接受修改参数；参数变化必须产生新 operation 和新审批。

### Day 15：LangGraph interrupt/resume 与审批节点

**改动/交付文件**

- Modify: `backend/graph/nodes.py`、`edges.py`、`builder.py`
- Modify: `backend/jobs/tickets.py`、`worker.py`
- Create: `tests/integration/test_graph_hitl.py`
- Create: `tests/integration/test_resume_idempotency.py`
- Create: `docs/evidence/l3/day-15.md`

**执行**

- [ ] 写 high-risk 到 `waiting_approval`、approve resume、reject terminal、args_hash mismatch 和重复 resume 测试。
- [ ] 实现 Approval 节点写 `approval` 快照，高风险路径 interrupt，恢复后再进入 Action execute。
- [ ] resume job ID 包含 approval_id 与 version；Job 载荷仍只含 ID。
- [ ] 在 resume 路径让执行网关再次校验批准与冻结参数。

**验收目标**

- Case C 完整达到 `Action Proposal → Policy → interrupt → Approval → resume → execute`。
- 审批前退款副作用严格为 0；批准后严格为 1。
- 重复审批、重复 Job、重复 resume 任意组合下仍只执行一次。

### Day 16：Approval 前端与三 Case 演示闭环

**改动/交付文件**

- Create: `frontend/src/views/ApprovalView.vue`、`ApprovalView.test.ts`
- Modify: `frontend/src/router/index.ts`、`api/client.ts`
- Create/Modify: `frontend/src/stores/approvals.ts`
- Create: `scripts/l3_case_smoke.py`
- Create: `docs/evidence/l3/day-16.md`

**执行**

- [ ] 写待审批列表、详情、approve/reject、409 version conflict 和过期状态页面测试。
- [ ] 页面显示冻结 Tool、脱敏参数、风险原因、版本和过期时间，不显示敏感值。
- [ ] Smoke 脚本依次运行 Case A、B、C，并核对状态、审计和副作用次数。
- [ ] 在浏览器手动完成“提交高风险退款 → 审批 → 恢复 → 查看结果”。

**验收目标**

- Approval 页面不是静态 Mock，真实调用 API 并驱动 Worker resume。
- 三个演示 Case 都能从 UI/API 入口运行，且可从 Trace 找到完整链路。
- 前端构建、类型检查和 Vitest 全部通过。

## Week 4 — 八类失败模式与恢复证据

### Day 17：F-01 Tool timeout 的重试、fallback 与 escalation

**改动/交付文件**

- Create: `tests/test_failure_modes.py`
- Modify: `backend/governance/executor.py`
- Create: `tests/fakes/failures.py`
- Create: `docs/evidence/l3/day-17.md`

**执行**

- [ ] 用“第 N 次调用失败”的 Fake 写 F-01a 可选只读 Tool 连续超时测试。
- [ ] 写 F-01b 关键写 Tool 连续超时测试。
- [ ] 实现有界重试；只读耗尽后 `resolved + fallback`，关键写耗尽后 `escalated`。
- [ ] 断言每次尝试均有 audit、retry_count 正确、最终副作用次数为 0。

**验收目标**

- F-01a/F-01b 的终态、审计条数和副作用次数均确定性可复跑。
- 不使用概率故障、真实 sleep 或放宽超时断言。

### Day 18：F-02 非法模型结构与 F-03 图循环

**改动/交付文件**

- Modify: `tests/test_failure_modes.py`
- Modify: `backend/integrations/model_client.py`
- Modify: `backend/graph/edges.py`
- Create: `docs/evidence/l3/day-18.md`

**执行**

- [ ] Fake 依次返回两次非法结构，断言两次修复失败后 `escalated`。
- [ ] Fake 固定循环合法路由，断言第 15 步停止且不发生第 16 次模型/Tool 调用。
- [ ] 异常摘要只记录验证类别，不回显原始敏感输出。
- [ ] 运行模型适配层、路由与失败模式测试。

**验收目标**

- 非法 JSON/Pydantic 输出不会穿透到状态或 Tool。
- 图循环的停止位置、终态和 agent_history 可审计。

### Day 19：F-04 越权 Tool 与 F-06 Prompt Injection

**改动/交付文件**

- Modify: `tests/test_failure_modes.py`
- Modify: `backend/governance/executor.py`
- Modify: `prompts/supervisor.yaml`、`action.yaml`
- Modify: `specs/agent_contracts/supervisor.yaml`、`action.yaml`
- Create: `docs/evidence/l3/day-19.md`

**执行**

- [ ] 固定输入请求 Supervisor 调用 Tool、Knowledge 调写 Tool、`delete_user` 和绕过审批退款。
- [ ] 证明网关在实现解析前拒绝未知/越权 Tool。
- [ ] 检查 Prompt 只强化模型行为，不承担安全边界。
- [ ] 断言四类攻击均为 rejected、存在审计、业务副作用为 0。

**验收目标**

- `delete_user` 不在生产 Registry，也没有隐藏 callable。
- Prompt Injection 即使影响模型建议，也不能改变白名单、Policy 或审批结论。

### Day 20：F-05 审批过期与 F-08 重复决定/恢复

**改动/交付文件**

- Modify: `tests/test_failure_modes.py`
- Modify: `backend/db/repositories/approvals.py`
- Modify: `backend/jobs/tickets.py`
- Create: `docs/evidence/l3/day-20.md`

**执行**

- [ ] 注入固定 UTC 时钟，测试 TTL 到期前后边界。
- [ ] 并发发送 approve/approve、approve/reject 和重复 resume。
- [ ] 断言只有一次状态转移、一次入队、一次执行和一条业务结果。
- [ ] 证明过期 approval 不能通过直接调用 Worker 绕过 API。

**验收目标**

- F-05、F-08 同时断言终态、审计和副作用次数。
- 任何重放不依赖 Redis 锁作为最终正确性来源。

### Day 21：F-07 checkpoint 后崩溃恢复

**改动/交付文件**

- Modify: `tests/test_failure_modes.py`
- Modify: `tests/integration/test_worker_recovery.py`
- Modify: `backend/jobs/worker.py`
- Create: `scripts/recovery_drill.py`
- Create: `docs/evidence/l3/day-21.md`

**执行**

- [ ] 在写 Tool 完成并提交 ledger 后、图状态推进前注入固定崩溃。
- [ ] 彻底关闭第一 Worker context，再建立新 pool/checkpointer 恢复。
- [ ] 断言 ledger cache hit、业务副作用仍为 1、图最终进入 resolved。
- [ ] 用真实 Compose Worker 执行一次恢复演练并记录容器日志摘要。

**验收目标**

- F-07 证明的不是“进程没崩”，而是“崩溃后恢复且不重复写”。
- 恢复证据同时包含自动测试与真实服务重启结果。

### Day 22：Phase 2 总门禁与 live 行为对比

**改动/交付文件**

- Create: `eval/artifacts/phase2-after.json`
- Modify: `README.md`、`docs/architecture.md`、`PORTFOLIO.md`
- Create: `docs/evidence/l3/day-22.md`

**执行**

- [ ] 在 Phase 2 完成后的 clean commit 上，以 Day 08 相同模型/端点/数据集运行 `phase2-after.json`，并与 `phase2-before.json` 比较。
- [ ] 运行 `tests/test_failure_modes.py`、所有 governance/HITL 单元与集成测试。
- [ ] 用 `scripts/l3_case_smoke.py` 和 `scripts/recovery_drill.py` 验证真实服务。
- [ ] 只有全部通过后才把 HITL、幂等、恢复和八类失败模式标记为完成。

**验收目标**

- F-01～F-08 全部有固定故障点、终态断言、审计断言和副作用次数断言。
- Case C 可在浏览器完成；high-risk bypass count 在测试证据中为 0。
- 20 条数据仍只能叫 smoke，不能发布为完整系统指标。

## Week 5 — 完整 Eval 与可视化

### Day 23：扩充到 80 条人工审核 Golden Cases

**改动/交付文件**

- Modify: `eval/golden_dataset.json`
- Modify: `tests/unit/test_golden_dataset.py`
- Modify: `scripts/validate_golden_dataset.py`
- Create: `docs/evidence/l3/day-23.md`

**执行**

- [ ] AI 起草 60 条候选，但人类逐条确认 expected intent/handoffs/tools/risk/human/outcome/doc IDs/reference answer。
- [ ] 最终 80 条覆盖正常、边界、参数缺失、模糊请求、越权、Prompt Injection、Tool timeout、模型错误、HITL、幂等与 F-01～F-08。
- [ ] 保持五类 intent、八个 Tool、正常/负面/恢复路径都有覆盖。
- [ ] 将 dataset version 提升为完整评估版本，并由校验器拒绝重复 case_id、未注册 Tool 和非法 handoff。

**验收目标**

- 恰有至少 80 条、`review_status=reviewed`，每条 12 个必填字段完整。
- AI 生成不等于人工标签；未完成人工复核时保持 draft，不允许进入指标运行。

### Day 24：确定性指标与门槛实现

**改动/交付文件**

- Create: `eval/metrics.py`
- Modify: `eval/schemas.py`
- Create: `tests/unit/test_eval_metrics.py`
- Create: `docs/evidence/l3/day-24.md`

**执行**

- [ ] 用小型固定轨迹为每个指标写精确数值测试。
- [ ] 实现 Intent Accuracy、Handoff Accuracy、Tool Selection Jaccard、Policy Decision Accuracy、High-risk Bypass Count、Task Success Rate、Retrieval Precision/Recall、轨迹效率、冗余 Tool 率、P95 latency。
- [ ] Human Escalation Rate 与 average token/cost 作为诊断指标，不设置伪造门槛。
- [ ] 分母为 0、失败 Case、部分轨迹和非 live cost 缺失均有明确语义。

**验收目标**

- Policy Decision Accuracy 门槛 100%，High-risk Bypass Count 门槛 0；其余门槛与 AGENTS.md 一致。
- 所有指标由确定性测试覆盖，不能用 LLM 充当门禁裁判。

### Day 25：Offline/Live Eval、baseline 与 artifact 持久化

**改动/交付文件**

- Modify: `eval/run_eval.py`
- Create: `eval/baseline.json`
- Create: `backend/db/repositories/eval_runs.py`
- Modify: `backend/api/eval.py`、`schemas.py`
- Create: `tests/integration/test_eval_runner.py`、`test_eval_api.py`
- Create: `docs/evidence/l3/day-25.md`

**执行**

- [ ] 离线运行真实 Graph + Fake/Recorded Model，不把 Recorded 结果解释为 Prompt 质量。
- [ ] Live 运行记录 dataset/model/base URL label/Prompt/Git SHA/UTC/token/cost/latency 和逐 Case 轨迹。
- [ ] 只有显式 `--update-baseline` 生成候选 baseline；普通 CI 只读。
- [ ] Eval API 从 PostgreSQL 返回运行列表与详情，不扫描任意本地文件。

**验收目标**

- 不同数据集、模型、端点或 Prompt 元数据之间禁止做伪比较。
- baseline 修改可人工审查并能关联原始 artifact。
- Eval API 不再返回 501，且 artifact/数据库/API 三者 run_id 一致。

### Day 26：TraceViewer 页面

**改动/交付文件**

- Create: `frontend/src/views/TraceViewerView.vue`、`TraceViewerView.test.ts`
- Create/Modify: `frontend/src/stores/traces.ts`
- Modify: `frontend/src/router/index.ts`
- Create: `docs/evidence/l3/day-26.md`

**执行**

- [ ] 写 Agent、Dispatcher、Tool、Approval、retry、cache hit、failure 时间线渲染测试。
- [ ] 实现状态/结果/延迟/重试/operation ID 的结构化展示。
- [ ] 敏感字段只展示后端脱敏摘要；前端不尝试恢复原值。
- [ ] 用 Case A/B/C 和 F-07 的真实 trace 手动检查可读性。

**验收目标**

- 面试演示能从工单详情跳到 Trace，并解释每次角色切换和 Tool 尝试。
- TraceViewer 不依赖 Langfuse 才可工作。

### Day 27：EvalDashboard 页面

**改动/交付文件**

- Create: `frontend/src/views/EvalDashboardView.vue`、`EvalDashboardView.test.ts`
- Create/Modify: `frontend/src/stores/eval.ts`
- Modify: `frontend/src/router/index.ts`
- Create: `docs/evidence/l3/day-27.md`

**执行**

- [ ] 写运行列表、指标、门槛/实测区分、baseline 对比和失败 Case 展开测试。
- [ ] 每个数值显示 artifact run_id、Git SHA、dataset version、model 和运行时间。
- [ ] 门槛使用“门槛”标签，实测使用“本次结果”，缺失值显示“未测”而不是 0。
- [ ] 运行前端完整质量门禁。

**验收目标**

- 看板不能把设计门槛显示为项目成绩。
- 可从某项指标下钻到失败 Case 和对应 Trace。

## Week 6 — 可观测、部署、真实指标与简历交付

### Day 28：结构化 Trace 与可选 Langfuse 导出

**改动/交付文件**

- Create: `backend/integrations/trace_exporter.py`
- Modify: `backend/config.py`、`backend/graph/nodes.py`、`backend/integrations/model_client.py`
- Create: `tests/unit/test_trace_exporter.py`
- Modify: `.env.example`
- Create: `docs/evidence/l3/day-28.md`

**执行**

- [ ] 定义 no-op 与 Langfuse exporter Protocol，未配置时不影响主流程。
- [ ] 导出 request/ticket/trace/agent/prompt version/model/latency/token/cost 和脱敏 outcome。
- [ ] 测试 exporter 故障不改变业务授权、状态或审计事实。
- [ ] 若有外部 Langfuse 凭据，运行一次 Case A/B/C 并保存脱敏截图/链接说明；没有则明确记录“可选集成未实测”。

**验收目标**

- PostgreSQL Audit 仍是治理事实源，Langfuse 只做观察。
- 默认 Compose 不增加完整自托管 Langfuse 基础设施。

### Day 29：完整 Compose、健康检查与备份恢复演练

**改动/交付文件**

- Modify: `docker-compose.yml`
- Modify: `backend/Dockerfile`
- Create: `frontend/Dockerfile`、`frontend/nginx.conf`
- Create: `scripts/full_stack_smoke.py`、`scripts/backup_restore_drill.ps1`
- Modify: `README.md`、`.env.example`
- Create: `docs/evidence/l3/day-29.md`

**执行**

- [ ] 从 clean checkout 视角运行 `docker compose up --build`，而非只做 config 校验。
- [ ] 等待 PostgreSQL、Redis、API、Worker、Frontend 健康；运行数据库迁移和 demo seed。
- [ ] 执行 Login、Case A/B/C、Trace、Eval API 的 full-stack smoke。
- [ ] 备份 PostgreSQL 与 Chroma volume，恢复到隔离目标并验证工单、审批、audit、ledger、checkpoint 和引用。

**验收目标**

- `git clone → .env → docker compose up --build` 的文档步骤可复现。
- 重启容器后数据可查询；备份恢复是实测而非仅写说明。
- 不引入 Kubernetes、HA、真实支付/企业接口或多租户。

### Day 30：最终 Live Eval、CI、3 分钟演示与简历证据

**改动/交付文件**

- Create: `eval/artifacts/l3-live-final.json`（真实 run ID 保存在 artifact 内容中）
- Create: `docs/demo/l3-demo-script.md`
- Create: `docs/evidence/l3/final-acceptance.md`
- Modify: `README.md`、`docs/architecture.md`、`PORTFOLIO.md`
- Modify/Create: `.github/workflows/backend-ci.yml`、`frontend-ci.yml`、`eval.yml`

**执行**

- [ ] 在 clean commit 上运行 80 条完整 Live Eval，保存真实指标、成本、P95 和所有元数据。
- [ ] 运行 backend、frontend、migration、failure、offline eval、生成物漂移和 full-stack smoke 全门禁。
- [ ] 验证 GitHub Actions 三条工作流；Live Eval 由受保护手动 job 上传 artifact。
- [ ] 按 3 分钟脚本演示：提交 → Timeline → 高风险 → Approval → resume → Trace → Eval Dashboard。
- [ ] 只从 final artifact 和 CI/服务证据生成 `PORTFOLIO.md` 的完成时 bullet；不手填示例百分比。

**验收目标**

- 80 条完整 Eval 有可追溯真实结果；数字不要求“漂亮”，但必须真实、可解释、可复跑。
- 全栈 Compose 实测、CI 实际运行、Case A/B/C、F-01～F-08、恢复演练和前端五路由均有证据。
- 最终工作区 clean，文档状态与代码完全一致，才能标记 L3 完成。

---

## 4. 阶段门禁与停止规则

### Gate A — Phase 1 可运行纵切（Day 07）

必须同时具备：真实 202/outbox/Worker 图执行、PostgreSQL checkpoint 恢复、引用可回查、请求者隔离、Login/Ticket 页面、CI offline 门禁、live before/after artifact。缺任一项就不能进入“Phase 1 完成”表述。

### Gate B — 治理与 HITL（Day 22）

必须同时具备：四个写 Tool、唯一 Password Reset Skill、纯函数 Policy、账本幂等、Approval 绑定与锁、interrupt/resume、Case A/B/C、F-01～F-08。高风险审批前有任何副作用即 Gate 失败。

### Gate C — L3 简历可投递（Day 30）

必须同时具备：

- 四角色真实运行，Supervisor 零 Tool 权限；
- PostgreSQL checkpoint/resume 与 Worker 崩溃恢复；
- Tool Governance 全链路与审计脱敏；
- 真实 Approval UI 和 high-risk interrupt/resume；
- 八类失败模式的确定性自动测试；
- 80 条人工审核完整数据集与一次完整 Live Eval；
- Login、TicketSubmit、Approval、TraceViewer、EvalDashboard 五个路由；
- `docker compose up --build` 全栈实测；
- backend/frontend/eval 三条 CI；
- 指标、演示、简历 bullet 全部可回查到 artifact 和 Git SHA。

满足 Gate C 后立即停止新增业务功能，转入投递、演示和面试反馈循环。以下内容明确不做：完整 RBAC、多租户、第二个 Skill、第五个 Agent、真实企业/支付接口、WebSocket、Kubernetes、HA、自定义设计系统。

---

## 5. 最终验收命令

```powershell
uv lock --check
uv run ruff check agents tools skills backend eval scripts tests
uv run mypy agents tools skills backend eval scripts
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/test_failure_modes.py -q
uv run python scripts/export_tool_schemas.py --check
uv run python scripts/export_openapi.py --check
uv run python scripts/validate_agent_contracts.py
uv run python scripts/validate_golden_dataset.py
uv run python eval/run_eval.py --mode offline --output eval/artifacts/l3-offline.local.json

Set-Location frontend
npm ci
npm run lint
npm run generate:api-types
npm run type-check
npm run test
npm run build
Set-Location ..

git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts
git diff --check
docker compose up -d --build
uv run python scripts/full_stack_smoke.py
uv run python scripts/recovery_drill.py
docker compose ps
git status --short
```

Live Eval 必须单独在 clean commit、真实凭据、固定模型与人工审核数据集上运行：

```powershell
uv run python eval/run_eval.py --mode live --output eval/artifacts/l3-live-final.json
```

文件内部的 `run_id` 必须由 runner 在本次运行中生成，不能手工填写或复制旧运行的 ID。

---

## 6. 每日验收记录模板

每天复制下面结构到 `docs/evidence/l3/day-XX.md`，只填写已经执行的事实：

```markdown
# Day XX Acceptance

- Date (UTC+8):
- Goal:
- Commit SHA:
- Changed files:
- Contract/permission/state/eval impact:

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|

## Human review

- Authorization boundary:
- Sensitive-data review:
- Test-quality review:
- Documentation accuracy:

## Remaining gaps

- None, or list a concrete blocker without claiming completion.
```

---

## 7. 自审结果

- **需求覆盖**：L3 所需的 Agent Runtime、Tool Governance、HITL、checkpoint/resume、失败处理、Eval、Trace、前端、CI、部署、真实指标均有明确工作日、文件和验收证据。
- **与冻结设计一致**：未引入 SQLite、第五个 Agent、第二个 Skill、默认自托管 Langfuse、Kubernetes、多租户或完整 RBAC。
- **证据边界**：静态检查、单元/集成测试、真实服务、Live 模型、渲染页面、CI 外部结果分别验收，不互相替代。
- **当前最大阻塞**：Day 01 的 OpenAI-compatible 凭据与 live baseline；这是行为开发前的真实门禁，不能由离线 Fake 替代。
