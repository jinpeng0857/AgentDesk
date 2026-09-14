# AgentDesk

**Reliable Multi-Agent Workflow Automation for IT Service Desk**

AgentDesk 是一个作品集级生产化项目：使用模拟企业数据实现工单分类、知识检索、受治理工具执行、人工审批、断点恢复和可追溯评估。

> 设计文档版本：v2.0
> 软件版本：0.1.0 / pre-alpha
> 当前状态：设计与契约落地，尚无可运行的业务实现

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

仓库已完成 Phase 0 的契约产出，正在执行最终验收；它仍不是完整可运行的业务应用。

当前可以验证的内容：

- Git 仓库、隔离开发分支、Python/前端锁文件和基础质量配置已建立；
- 四份 Agent contract 与四份 Prompt 已填写并可由脚本校验；
- 八个 Tool 的 Pydantic v2 模型、Registry、白名单和生成式 JSON Schema 已落库；
- FastAPI 暴露 13 个 Phase 0 接口契约，OpenAPI 与前端 TypeScript 类型均由代码生成；
- `eval/golden_dataset.json` 包含 20 条已人工确认标签的冒烟评估样本；
- Docker Compose 当前只提供 PostgreSQL 与 Redis，两个基础设施服务已做健康启动验证。

仍未实现的是 Agent 行为、LangGraph、Worker、数据库表与 migration、JWT、写操作执行网关、审批恢复、业务 Tool 实现、前端页面、评估指标与 CI。因此不得把契约端点的存在描述为业务能力已经完成，也不得宣称完整应用可以一键部署。

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
| 最终验收门禁 | 进行中 |

Phase 0 只建立后续开发必须遵守的契约基线，不代表 Phase 1～3 的业务实现已经完成。

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

下表中的路由和请求/响应模型已经进入 OpenAPI。Phase 0 仅实现契约外壳：`/health` 返回存活状态，`/ready` 在依赖未接入时返回 503，其余业务端点稳定返回 501；真实认证、持久化、入队和查询在后续阶段实现。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 模拟用户登录 |
| GET | `/api/auth/me` | 当前身份 |
| POST | `/api/tickets` | 创建工单，返回 HTTP 202 |
| GET | `/api/tickets` | 当前用户工单列表 |
| GET | `/api/tickets/{ticket_id}` | 查询状态与结果 |
| GET | `/api/tickets/{ticket_id}/trace` | 查询脱敏轨迹 |
| GET | `/api/approvals` | approver 的待审批列表 |
| GET | `/api/approvals/{approval_id}` | 审批详情 |
| POST | `/api/approvals/{approval_id}/decision` | approve/reject，带乐观锁版本 |
| GET | `/api/eval/runs` | Eval 历史 |
| GET | `/api/eval/runs/{run_id}` | 单次 Eval 报告 |
| GET | `/health` | 进程存活 |
| GET | `/ready` | PostgreSQL/Redis/配置就绪 |

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

当前可运行契约校验和 PostgreSQL/Redis 基础设施；完整应用尚不可启动。请先阅读：

1. [AGENTS.md](AGENTS.md)：开发契约；
2. [docs/architecture.md](docs/architecture.md)：完整设计；
3. [specs/README.md](specs/README.md)：机器契约索引；
4. [PORTFOLIO.md](PORTFOLIO.md)：证据与简历纪律。

### Phase 0 验证

安装锁定依赖并检查契约：

```powershell
Copy-Item .env.example .env
uv sync --all-groups
uv run ruff check agents tools backend eval scripts tests/unit
uv run mypy agents tools backend eval scripts
uv run pytest tests/unit -q
uv run python scripts/export_tool_schemas.py --check
uv run python scripts/export_openapi.py --check
uv run python scripts/validate_agent_contracts.py
uv run python scripts/validate_golden_dataset.py

Set-Location frontend
npm ci
npm run generate:api-types
npm run type-check
```

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

1. **Phase 0 — 契约闭合（最终验收中）**：Agent/Tool/OpenAPI 契约、20 条人工审核 Golden Cases、依赖与基础设施 Compose。
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
