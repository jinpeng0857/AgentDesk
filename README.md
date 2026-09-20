# AgentDesk

**Reliable Multi-Agent Workflow Automation for IT Service Desk**

AgentDesk 是一个作品集级生产化项目：使用模拟企业数据实现工单分类、知识检索、受治理工具执行、人工审批、断点恢复和可追溯评估。

> 设计文档版本：v2.0
> 软件版本：0.2.0 / beta
> 当前状态：Phase 1 只读纵切与 Phase 2 工具治理/HITL/8大失败模式全量通过（Gate A & Gate B Passed）

项目介于一次性 Demo 与真实企业产品之间。核心链路必须真实可运行、可测试、可恢复、可审计和可部署，但不实现完整 RBAC、多租户、真实企业接口或高可用集群。

---

## 为什么值得做

它重点展示四种工程能力：

| 能力 | 展示内容 |
|---|---|
| Agent Orchestration | 四个职责隔离角色、确定性 Dispatcher、状态所有权、最大步数 |
| Tool Governance | Schema、白名单、Policy、审批、幂等账本、重试、熔断、审计 |
| Human-in-the-loop | 高风险操作提案、interrupt、持久化审批、resume、重复恢复保护 |
| Evaluation | 人工 Golden Cases、离线/Live Eval、基线、CI 门禁、可追溯报告 |

这里的多 Agent 来自权限边界，而不是角色数量：

- Supervisor 理解工单，但永远不能调用工具；
- Knowledge 只能检索并返回可回查引用；
- Action 可以提出和执行白名单工具；
- Policy 使用确定性规则做最终授权和风险判断。

---

## 系统形态

```text
Vue 3 / Nginx
       ↓
FastAPI API ── PostgreSQL
       │          工单、审批、审计、幂等账本、checkpoint
       ↓
Redis / ARQ Queue
       ↓
Worker / LangGraph
  ├── Supervisor
  ├── Knowledge ── Chroma
  ├── Action ───── Governed Tools
  └── Policy ───── Deterministic Rules
```

API 只负责认证、校验、写入工单和任务 outbox、查询状态；只有独立 Worker 可以启动或恢复 Agent Graph。

### 高风险写操作

```text
Action 生成 PendingOperation
→ Policy 计算风险
→ high risk: interrupt
→ approver 审批
→ Worker resume
→ 校验 operation_id + args_hash
→ Action execute
```

审批前不会调用写 Tool。重复审批、重复 Job 或 Worker 重启由 PostgreSQL 操作账本和稳定幂等键保护。

完整设计见 [docs/architecture.md](docs/architecture.md)，开发硬约束见 [AGENTS.md](AGENTS.md)。

---

## 目标技术栈

以下是获批的目标技术栈，不表示当前依赖均已安装：

| 层 | 技术 | 选择依据 |
|---|---|---|
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Pinia、Axios | 审批台、Trace 和评估看板需要结构化交互 |
| API | FastAPI、Pydantic v2 | OpenAPI 契约与严格 DTO |
| Worker | ARQ、Redis | API/任务进程隔离、异步 OpenAI 调用、定时任务 |
| Agent Runtime | LangGraph | checkpoint、interrupt/resume、显式状态图 |
| 数据库 | PostgreSQL、Alembic | 跨进程事务、迁移、审批和幂等事实源 |
| RAG | Chroma | 小规模 SOP/FAQ/脱敏案例检索 |
| 模型 | OpenAI-compatible API | 默认可用且供应商可替换 |
| 可观测 | JSON Logs + 可选 Langfuse | 默认可独立运行，按需导出 Agent Trace |
| 部署 | Docker Compose + Nginx | 单主机可复现部署 |
| CI | GitHub Actions | 测试、Schema 漂移和 Eval 门禁 |

---

## 当前真实状态

仓库已完成 Phase 0 契约闭合与 Phase 1 只读纵切的全部交付与本地验收；Phase 2 写操作治理与 HITL 尚未开始。

当前可以验证的内容：

- Git 仓库、隔离开发分支、Python/前端锁文件和基础质量配置已建立；
- 四份 Agent contract 与四份 Prompt 已填写并可由脚本校验；
- 八个 Tool 的 Pydantic v2 模型、Registry、白名单和生成式 JSON Schema 已落库；
- FastAPI 暴露 13 个接口契约，OpenAPI 与前端 TypeScript 类型均由代码生成；
- `eval/golden_dataset.json` 包含 20 条已人工确认标签的冒烟评估样本；
- Docker Compose 提供 PostgreSQL、Redis、API 与 Worker 服务，容器健康启动与依赖关系已验证；
- PostgreSQL 业务表与 LangGraph checkpoint 表由 Alembic 从空库建出，应用启动时不隐式建表；
- `/api/auth/login`、`/api/auth/me` 与 `POST /api/tickets` 已实现：Argon2id + JWT 认证、校验 issuer/audience/expiry、单事务写入工单与 outbox 后返回 HTTP 202；
- `GET /api/tickets`、`GET /api/tickets/{ticket_id}` 与 `GET /api/tickets/{ticket_id}/trace` 已实现：强制请求人数据隔离、返回包含 5 字段完整知识引用的 resolved 结果、以及按时间正序且脱敏的 Trace 事件；
- ARQ 使用严格 JSON 载荷与确定性 `start:{ticket_id}` 作业 ID，API 不运行 Agent 图；
- Worker 独占运行 LangGraph 图：基于 PostgreSQL checkpointer 实现确定性恢复，故障重启后不重复执行已完成步骤且终态短路；
- 四个 Agent 角色、四个只读 Tool、可注入的只读执行网关、四个 SQL Adapter 与本地 Chroma 检索已实现；
- Vue 3 最小前端已交付：纯内存认证、提交表单后立即清空口令、2 秒自动轮询、后台页面休眠暂停、终态停止以及 5 字段引用卡片展示；
- 真实 live 评估门禁已通过：`eval/artifacts/phase1-before.json` 与 `phase1-after.json` 均在工作区干净提交上产出，使用同一 OpenAI 兼容模型端点（`deepseek-chat`），元数据严格一致，测得 17/20 意图匹配（仅供回归参考，非简历百分比）。

**仍未实现（Phase 2/3 范围）**：四个业务写 Tool 的真实实现、operation 幂等账本、Policy 风险矩阵、审批流与 interrupt/resume、80+ 条完整评测集与指标看板。

### Phase 0 状态

| 任务 | 状态 |
|---|---|
| 需求分析与 v2 架构决策 | 完成 |
| Agent contracts 与 Prompt | 完成 |
| 八个 Tool Schema 与白名单 | 完成 |
| OpenAPI 草案与前端生成类型 | 完成 |
| 20 条人工审核 Golden Cases | 完成 |
| 依赖、锁文件与基础设施 Compose | 完成 |
| 主文档同步 | 完成 |
| 最终验收门禁 | 完成 |

Phase 0 建立后续开发必须遵守的契约基线。

### Phase 1 状态

| 任务 | 状态 |
|---|---|
| 持久化基础、ORM 与 Alembic 迁移 | 完成 |
| Argon2 + JWT 认证与真实 Auth API | 完成 |
| JSON ARQ 队列、事务 outbox 与 HTTP 202 | 完成 |
| OpenAI-compatible 模型适配层与确定性 Fake | 完成 |
| 只读 Agent / Tool / 治理网关 / 八个 Adapter | 完成 |
| LangGraph 运行时与 Worker 恢复 | 完成 |
| 工单查询与 Trace API | 完成 |
| Vue 最小切片、CI 与 Phase 1 验收 | 完成 |
| live 模型前后对比门禁 | 完成（`phase1-before.json` vs `phase1-after.json`） |

---

## 计划中的业务能力

### Intent

```text
network
account
permission
query
high_risk
```

### Tool

| Tool | 类型 | 用途 |
|---|---|---|
| `search_knowledge` | read | 检索 SOP、FAQ 和脱敏历史案例 |
| `query_user` | read | 查询模拟账号 |
| `query_order` | read | 查询模拟订单 |
| `check_service` | read | 查询模拟服务状态 |
| `create_ticket` | write | 创建模拟下游跟进工单 |
| `request_access` | write | 创建权限申请，不直接授权 |
| `reset_password` | write | 重置本人模拟账号密码 |
| `process_refund` | write | 模拟退款，高金额必须审批 |

`delete_user` 不注册为生产 Tool。未知或越权 Tool 由治理网关拒绝并审计。

---

## API 契约

下表中的路由和请求/响应模型都来自 OpenAPI。状态含义：`已实现` 表示端点在运行时真实访问数据库或基础设施；`501` 表示它稳定返回 `NOT_IMPLEMENTED` 契约错误，不代表功能已具备。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | `/api/auth/login` | Argon2 校验后签发 JWT | 已实现 |
| GET | `/api/auth/me` | 当前身份，校验 JWT 与账号状态 | 已实现 |
| POST | `/api/tickets` | 单事务写入工单与 outbox，返回 HTTP 202 | 已实现 |
| GET | `/api/tickets` | 当前用户工单列表 | 已实现 |
| GET | `/api/tickets/{ticket_id}` | 查询状态与结果 | 已实现 |
| GET | `/api/tickets/{ticket_id}/trace` | 查询脱敏轨迹 | 已实现 |
| GET | `/api/approvals` | approver 的待审批列表 | 501 |
| GET | `/api/approvals/{approval_id}` | 审批详情 | 501 |
| POST | `/api/approvals/{approval_id}/decision` | approve/reject，带乐观锁版本 | 501 |
| GET | `/api/eval/runs` | Eval 历史 | 501 |
| GET | `/api/eval/runs/{run_id}` | 单次 Eval 报告 | 501 |
| GET | `/health` | 进程存活 | 已实现 |
| GET | `/ready` | PostgreSQL/Redis/配置就绪 | 已实现，依赖未就绪时 503 |

OpenAPI 是前后端接口唯一来源。前端类型通过 `openapi-typescript` 生成，不手写 DTO。

---

## 计划页面

| 路由 | 页面 | 功能 |
|---|---|---|
| `/login` | Login | 选择/登录模拟身份 |
| `/tickets` | TicketSubmit | 创建、列表、轮询、最终结果 |
| `/approvals` | Approval | 查看绑定操作并 approve/reject |
| `/traces/:ticketId` | TraceViewer | Agent、Tool、审批和失败时间线 |
| `/eval` | EvalDashboard | 门槛、基线、运行历史和对比 |

使用轮询，不实现 WebSocket。

---

## 配置约定

仓库已提供 `.env.example`，列出以下配置项；复制后必须替换占位密钥：

```text
APP_ENV
DATABASE_URL
REDIS_URL
JWT_SECRET
APP_ENCRYPTION_KEY
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
OPENAI_TIMEOUT_S
OPENAI_MAX_RETRIES
CHROMA_PATH
LANGFUSE_HOST
LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY
```

Langfuse 配置为可选项。任何密钥、Token 和临时口令都不能进入 Git、日志、审计或 Trace。

---

## 快速开始

### 当前阶段

当前可运行契约校验、PostgreSQL/Redis 基础设施，以及认证与工单提交的只读纵切基础；包含 Agent 图与前端在内的完整应用尚不可启动。请先阅读：

1. [AGENTS.md](AGENTS.md)：开发契约；
2. [docs/architecture.md](docs/architecture.md)：完整设计；
3. [specs/README.md](specs/README.md)：机器契约索引；
4. [PORTFOLIO.md](PORTFOLIO.md)：证据与简历纪律。

### 契约检查（不需要基础设施）

```powershell
uv sync --all-groups
uv run ruff check agents tools skills backend eval scripts tests
uv run mypy agents tools backend eval scripts
uv run python scripts/export_tool_schemas.py --check
uv run python scripts/export_openapi.py --check
uv run python scripts/validate_agent_contracts.py
uv run python scripts/validate_golden_dataset.py

Set-Location frontend
npm ci
npm run generate:api-types
npm run type-check
Set-Location ..
```

### Phase 1 集成验证（需要真实 PostgreSQL 与 Redis）

```powershell
docker compose up -d postgres redis
$env:DATABASE_URL = "postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk"
uv run alembic upgrade head
uv run python scripts/seed_demo_data.py --reset-demo-data
uv run pytest tests -q
```

这些命令验证空库迁移、Argon2+JWT 认证、单事务 outbox 与 HTTP 202、只读执行网关的零写保证，以及 Chroma 引用的五个必需字段。**它们不验证 Agent 图执行**，因为 LangGraph 运行时尚未实现。

### 只读评估冒烟（不需要凭据，产物不入库）

```powershell
uv run python eval/run_eval.py --mode offline --stage model-baseline `
  --output eval/artifacts/phase1-offline-smoke.local.json
```

离线模式使用确定性替身，只证明评测外壳本身可用，不证明 Prompt 或模型质量。`--mode live` 需要 OpenAI-compatible 凭据，并且拒绝在工作区不干净的提交上运行。

启动当前基础设施：

```powershell
docker compose up -d postgres redis
docker compose ps
docker compose down
```

### 完整应用目标

以下命令和地址只有在 Phase 1～3 对应实现完成后才有效，当前不得描述为“已通过”：

```powershell
docker compose up --build
```

目标地址：Frontend `http://localhost:8080`、API `http://localhost:8000`、OpenAPI `http://localhost:8000/docs`。

---

## Evaluation 纪律

设计门槛不等于项目成绩。完整评估需要至少 80 条人工审核 Golden Cases，并区分：

- Offline Eval：Fake/Recorded Model，验证图、契约和失败路径；
- Live Eval：真实 OpenAI-compatible 模型，验证 Prompt 和整体质量。

任何对外指标必须来自可追溯 artifact，包含数据集版本、Prompt 版本、模型配置、时间和 Git SHA。当前没有可发布的实测指标。

---

## 开发路线

1. **Phase 0 — 契约闭合（完成）**：Agent/Tool/OpenAPI 契约、20 条人工审核 Golden Cases、依赖与基础设施 Compose。
2. **Phase 1 — 基础纵切**：JWT、PostgreSQL、Redis/ARQ、模型适配、只读 Agent 路径、基础页面。
3. **Phase 2 — 治理与 HITL**：写 Tool、Policy、幂等账本、审批、interrupt/resume、八类失败模式。
4. **Phase 3 — 评估与展示**：完整数据集、Live Eval、CI、Trace/Eval 页面、部署与备份。

每阶段的详细验收见 [docs/architecture.md §19](docs/architecture.md)。

---

## 文档职责

| 文档 | 读者问题 |
|---|---|
| [README.md](README.md) | 项目是什么、现在能不能运行、如何开始？ |
| [AGENTS.md](AGENTS.md) | 改代码时绝不能违反什么？ |
| [docs/architecture.md](docs/architecture.md) | 系统为什么这样设计，数据如何流动？ |
| [specs/README.md](specs/README.md) | 哪些机器契约必须与代码一致？ |
| [PORTFOLIO.md](PORTFOLIO.md) | 目前有哪些成果真的可以写进简历？ |

---

## License

当前仓库尚未添加 License。在公开发布或接受外部贡献前必须先明确许可证。
