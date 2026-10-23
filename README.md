# AgentDesk

**Reliable Multi-Agent Workflow Automation for IT Service Desk**

AgentDesk 是一个基于模拟企业 IT 工单数据的 Agent 工作流项目，覆盖工单分类、知识检索、工具治理、人工审批、断点恢复和评估。它展示的是边界清晰、可测试、可追溯的工程实现，不是接入真实企业系统的产品。

> 设计依据：[`docs/architecture.md`](docs/architecture.md) v2.0
> 开发约束：[`AGENTS.md`](AGENTS.md)
> 当前验收记录：[`docs/acceptance/current.md`](docs/acceptance/current.md)
> 项目表达与证据边界：[`PORTFOLIO.md`](PORTFOLIO.md)

## 当前状态

本轮正在完成源码与工件重整、缺陷修复和重新验收。当前/历史验收结论以 [`docs/acceptance/current.md`](docs/acceptance/current.md) 的命令、环境、退出码和工件为准；本 README 描述项目结构与可复现入口，不把设计门槛或旧计划勾选项写成实测成果。

旧版 L3、Phase 1、Phase 2 评估文件保留在 `artifacts/evaluation/history/`。其中离线 Fake 只验证 harness；旧版“E2E”轨迹和指标由预期标签补齐，不是图执行结果，也不能充当模型质量基线。新的真实候选写入 `artifacts/evaluation/candidates/`，须人工审阅，评估器不会自动晋升官方基线。

## 系统边界

### 解决的问题与适用范围

AgentDesk 面向模拟 IT 服务台：将自然语言请求交给职责固定的工作流处理，减少人工分类、知识查找和状态跟进的重复工作；当请求涉及写操作时，通过代码权限、风险规则和人工审批控制执行。作品集演示应同时呈现处理结果、知识依据、操作边界与审计轨迹。

### 用户能够发起的任务

以下是当前代码实现的范围，不代表全部真实模型流程已完成端到端验收。

| 任务 | 实现能力 | 限制与需要人工介入的条件 |
|---|---|---|
| 知识咨询与网络问题 | 检索已索引的模拟知识库，返回带文档、片段标识的引用与答复 | Knowledge 只读；引用分数是检索排序依据，不是答案可信度；不自主联网搜索或修复真实网络 |
| 账户、订单和服务查询 | 通过 `query_user`、`query_order`、`check_service` 查询模拟数据 | 账户和订单访问受身份与归属校验限制；服务状态来自模拟接口 |
| 创建下游工单 | 经 Policy 和网关创建模拟下游工单 | 只能为本人发起；前端提交的入口工单与 `create_ticket` 工具产生的下游工单不同 |
| 申请资源权限 | 为本人创建权限申请记录 | `request_access` 不直接授予权限；模拟 MVP 中允许的本人申请无需额外人工审批 |
| 重置密码 | 执行唯一的 `password_reset` Skill，步骤固定，工具调用经过网关 | 仅限本人的活跃账户；敏感结果按契约脱敏与加密，当前工单 API 不提供临时密码展示或真实通知发送 |
| 处理退款 | 校验本人订单、金额、币种和资源版本后，更新模拟退款记录 | 200.00 USD 及以上属于高风险，先暂停等待人工审批；不连接支付系统，不产生真实资金流转 |
| 审批与回查 | 审批人通过或拒绝固定操作；用户可查询工单详情、知识引用和执行事件 | 审批绑定 `operation_id + args_hash`，不能编辑参数；过期或重复决定不能绕过后端校验 |

### 四个 Agent 的能力与权限

| 角色 | 能做什么 | 代码权限边界 |
|---|---|---|
| Supervisor | 对 network、account、permission、query、high_risk 五类意图进行分类，建议下一角色并汇总结果 | 不调用任何工具；建议必须经过 Dispatcher 校验，不能决定基础设施权限 |
| Knowledge | 调用 `search_knowledge` 检索并追加可回查引用 | 只有一个只读工具权限；不能写业务数据或执行 Action 工具 |
| Action | 提出操作，或调用白名单内的账户、订单、服务及写工具；可以选择固定的密码重置 Skill | 写提案必须先经过 Policy；执行只允许走统一网关，不能绕过审批、幂等与审计 |
| Policy | 用确定性规则检查授权与风险，决定允许、拒绝或需要审批 | 没有工具权限；最终判定不依赖 LLM，不执行写副作用 |

### 运行、停止与恢复的边界

```text
Browser → Vue/Nginx → FastAPI → PostgreSQL + Redis/ARQ → Worker/LangGraph
                                                            ├─ Supervisor
                                                            ├─ Knowledge → governed read tools → Chroma
                                                            ├─ Action → governed tools
                                                            └─ Policy → deterministic rules + Approval
```

- 只允许 Supervisor、Knowledge、Action、Policy 四个逻辑角色；Supervisor 没有工具权限。
- API 负责认证、校验、持久化、排队和查询；只有 Worker 启动或恢复 Agent 图。
- 写 Tool 统一经过 Schema、白名单、Policy/Approval、幂等账本、执行和脱敏审计。
- 高风险写操作先挂起等待审批，审批绑定 `operation_id` 与 `args_hash`。
- PostgreSQL 是工单、审批、审计、操作账本和 checkpoint 的持久化事实源；Redis 用于队列、锁和短期缓存。
- Policy 最终授权和风险判定由确定性代码完成，不能交给 LLM。

Dispatcher 最多允许 15 步；超出上限、非法转移和无法安全完成的关键操作会停止自动流程并进入明确状态。高风险写操作在审批前保持暂停；审批恢复继续原工单。PostgreSQL checkpoint 与操作账本承担恢复和防重复副作用的持久化职责，实际可靠性须以对应故障测试和运行证据确认。

### 当前证据与界面表达

截至 2026-10-06 的本次代码盘点，仓库引用的 `docs/acceptance/current.md` 尚不存在，因此不能宣布完整验收通过。模型分类成绩、离线 Fake、真实图运行和全栈演示需要各自对应的证据。前端评估接口目前提供模式、模型、版本和指标，但不提供 stage、样本分母或对照基线；UI 只对已知固定门槛作数值比较，基线相关指标显示“未判定”，不自动将候选晋升为官方结果。

UI 覆盖登录、工单、人工审批、执行轨迹与评估记录五个页面。顶部可展开能力边界，页面始终注明模拟环境。审批入口仅对审批人开放，Token 只保留在内存；刷新页面后需要重新登录。界面改版决策见 [`docs/plans/2026-10-06-ui-redesign.md`](docs/plans/2026-10-06-ui-redesign.md)。

本轮范围沿用作品集级模拟系统的边界：固定四个 Agent、一个 Skill、注册的八个工具。真实企业接口、支付、完整 RBAC、多租户、额外 Agent／Skill、WebSocket 和部署架构扩张不在本轮范围内。

## 目录

| 路径 | 内容 |
|---|---|
| `src/{agents,tools,skills,backend,eval}/` | Python 包源码；安装后的 import 名保持不变 |
| `resources/prompts/` | 四个 Agent 的 Prompt |
| `resources/evaluation/golden_dataset.json` | 固定 80 条人工审核样例 |
| `tests/unit/`、`tests/integration/` | 单元与真实 PostgreSQL/Redis 集成测试 |
| `tests/acceptance/failure_modes/` | F-01a/b、F-02～F-08 确定性失败验收 |
| `tests/support/fakes/` | 离线测试替身 |
| `specs/acceptance/test_matrix.yaml` | 验收 ID、测试节点、命令、依赖和证据登记 |
| `scripts/maintenance/` | Schema、OpenAPI、Agent contract、数据集和矩阵校验 |
| `scripts/acceptance/` | 全栈 Smoke、恢复及真实备份/恢复演练 |
| `artifacts/evaluation/history/` | 历史记录和离线 Fake 基线 |
| `artifacts/evaluation/candidates/` | 新模型基线与完整图候选工件 |
| `docs/acceptance/` | 当前验收报告及历史 L3 日志 |
| `frontend/`、`alembic/`、`specs/` | 前端、数据库迁移和机器契约 |

## 本地验证

需要 Python 3.11+、`uv`、Node.js 22.22.2（版本见根目录 `.nvmrc`）和 Docker Compose。后端单测不需要运行模型；集成/失败验收需要可访问的 PostgreSQL 和 Redis。

```powershell
uv sync --all-extras --dev
uv run ruff check src tests scripts
uv run mypy src/agents src/tools src/skills src/backend src/eval scripts
uv run python scripts/maintenance/export_tool_schemas.py --check
uv run python scripts/maintenance/export_openapi.py --check
uv run python scripts/maintenance/validate_agent_contracts.py
uv run python scripts/maintenance/validate_golden_dataset.py
uv run python scripts/maintenance/validate_test_matrix.py
```

集成与失败验收只操作隔离的 `*_test` 数据库，集成 fixture 会重建指定的 `agentdesk_test`：

```powershell
docker compose up -d postgres redis
$env:TEST_DATABASE_URL = "postgresql+asyncpg://agentdesk:agentdesk@127.0.0.1:5432/agentdesk_test"
$env:REDIS_URL = "redis://127.0.0.1:6379/15"
uv run pytest tests/unit tests/integration tests/acceptance -q
```

前端使用固定 Node 版本：

```powershell
Set-Location frontend
npm ci
npm run lint
npm run type-check
npm run test
npm run build
```

## Ollama 评估

默认候选模型是 `qwen3:8b`。两个阶段回答不同问题，不能把分类成绩当成端到端成绩：

```powershell
ollama pull qwen3:8b
uv run python -m eval.run_eval --mode live --stage model-baseline `
  --model qwen3:8b --base-url http://localhost:11434/v1 --api-key ollama `
  --output artifacts/evaluation/candidates/qwen3-8b-model-baseline.local.json
```

完整图候选需使用独立的 PostgreSQL `*_test` 数据库，让完整 80 条固定样例经过真实模型、图、工具网关与 checkpoint。`--concurrency` 控制并行图任务上限（默认 4，最大 8）；只读样例可并发，包含写 Tool 的样例会独占模拟数据夹具并在完成后恢复。artifact 同时记录配置并发度与实测最大并发度：

```powershell
$env:EVAL_TEST_DATABASE_URL = "postgresql+asyncpg://agentdesk:agentdesk@127.0.0.1:5432/agentdesk_eval_test"
uv run python -m eval.run_eval --mode live --stage e2e-candidate `
  --model qwen3:8b --base-url http://localhost:11434/v1 --api-key ollama `
  --database-url $env:EVAL_TEST_DATABASE_URL `
  --concurrency 4 `
  --output artifacts/evaluation/candidates/qwen3-8b-e2e-candidate.local.json
```

E2E 观测值来自实际图状态、数据库投影和审计；expected labels 只在运行完成后用于计分。门槛见 `AGENTS.md` §7.2；轨迹效率和 P95 只有与相同数据集、stage、模型及 endpoint 的真实候选比较才有意义。Live artifact 标记工作树状态和变更摘要。`--update-baseline` 仍只生成待审阅副本，不会改写官方基线。

离线 Fake 入口：

```powershell
uv run python -m eval.run_eval --mode offline --stage model-baseline `
  --output artifacts/evaluation/candidates/offline-harness.local.json
```

它验证 Prompt 加载、结构校验和工件输出，不衡量模型或系统质量。

## Compose 全栈

复制 `.env.example` 为 `.env`，填写 JWT/加密密钥与模型配置。连接本机 Ollama 时配置 `OPENAI_BASE_URL=http://host.docker.internal:11434/v1`、`OPENAI_MODEL=qwen3:8b`、`OPENAI_API_KEY=ollama`。

标准服务入口保持 `docker compose`：

```powershell
docker compose up --build
```

数据迁移不会由应用启动时隐式执行；新环境须先对目标数据库运行 `alembic upgrade head`。全栈 Smoke 和备份恢复演练命令、隔离库约束及最终结果见当前验收报告。

## API 与前端

OpenAPI 快照位于 `specs/openapi/openapi.json`，由 `src/backend/` 生成；TypeScript API 类型位于 `frontend/src/types/api.d.ts`，由 `openapi-typescript` 生成。主要接口覆盖登录、工单、审批、Trace、Eval 和 readiness；具体契约以 OpenAPI 为准。

任何量化结果都必须关联模型、数据集版本、Prompt 版本、运行时间、Git SHA/工作树指纹及逐例工件。未运行、跳过或只通过静态检查的层不得标成 Live/全栈验收通过。
