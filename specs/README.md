# Specs Index — 契约索引与冻结决策

> 本目录存放与实现强一致的 Agent、Tool 和 API 契约。本文件只负责索引、来源和生成规则，不重复承载系统方案。
>
> 版本：v2.0
> 完整方案：[`docs/architecture.md`](../docs/architecture.md) · 开发约束：[`AGENTS.md`](../AGENTS.md)（最高优先级） · 项目证据：[`PORTFOLIO.md`](../PORTFOLIO.md)

---

## 1. 冻结决策清单

以下决策不得由实现静默改变。如需调整，必须先按 [`AGENTS.md` §8](../AGENTS.md) 完成契约与文档同步；涉及硬约束时还需人工确认修改 `AGENTS.md`。

| 编号 | 决策 | 对应硬约束 | 方案出处 |
|---|---|---|---|
| D-01 | 路由由确定性 Dispatcher 校验和执行，Supervisor 只建议 `next_agent` | H-04 | [`docs/architecture.md` §5.4](../docs/architecture.md) |
| D-02 | 每个节点只写自己拥有的 `TicketState` 字段 | H-03 | [`docs/architecture.md` §6.2](../docs/architecture.md)、[`AGENTS.md` §2.1](../AGENTS.md) |
| D-03 | 每工单最大 15 步，超限后停止模型和工具调用并升级人工 | H-07 | [`docs/architecture.md` §13](../docs/architecture.md) |
| D-04 | PostgreSQL 是 checkpoint、审批、审计和写操作账本的持久化事实源 | H-25 | [`docs/architecture.md` §10.1](../docs/architecture.md)、[`docs/architecture.md` §11](../docs/architecture.md) |
| D-05 | OpenAPI 是前后端 API 类型唯一来源，前端 DTO 必须生成 | H-12 | [`docs/architecture.md` §11.2](../docs/architecture.md)、[`docs/architecture.md` §16.4](../docs/architecture.md) |
| D-06 | Supervisor 永不调用 Tool | H-02 | [`docs/architecture.md` §7.1](../docs/architecture.md) |
| D-07 | 依赖方向固定为 `backend → agents / tools / skills` | H-22 | [`docs/architecture.md` §5](../docs/architecture.md)、[`AGENTS.md` §2](../AGENTS.md) |

---

## 2. 契约文件与来源

| 文件 | 内容 | 权威来源 |
|---|---|---|
| `agent_contracts/supervisor.yaml` | Supervisor 输入、输出和决策边界 | [`docs/architecture.md` §7.1](../docs/architecture.md)、`agents/schemas.py`、`prompts/supervisor.yaml` |
| `agent_contracts/knowledge.yaml` | Knowledge 输入、输出和只读边界 | [`docs/architecture.md` §7.2](../docs/architecture.md)、`agents/schemas.py`、`prompts/knowledge.yaml` |
| `agent_contracts/action.yaml` | Action propose/execute 边界和 Tool 白名单 | [`docs/architecture.md` §7.3](../docs/architecture.md)、`agents/schemas.py`、`tools/registry.py`、`prompts/action.yaml` |
| `agent_contracts/policy.yaml` | Policy 确定性判定边界 | [`docs/architecture.md` §7.4](../docs/architecture.md)、`agents/schemas.py`、`prompts/policy.yaml` |
| `tool_schemas/tools.json` | 八个 Tool 的可检查 Schema 快照 | [`docs/architecture.md` §9](../docs/architecture.md)、`tools/registry.py`、`tools/schemas.py` |
| `openapi/openapi.json` | Phase 0 API 契约快照 | [`docs/architecture.md` §11.2](../docs/architecture.md)、`backend/main.py`、`backend/api/` |

`eval/golden_dataset.json` 位于 `eval/`，不属于本目录；它是人工审核的评估输入，不是由代码生成的接口契约。

---

## 3. 生成与校验

以下 JSON 文件是生成物，不得手工修改：

```powershell
uv run python scripts/export_tool_schemas.py
uv run python scripts/export_openapi.py

Set-Location frontend
npm run generate:api-types
```

提交前使用只读检查确认生成物没有漂移：

```powershell
uv run python scripts/export_tool_schemas.py --check
uv run python scripts/export_openapi.py --check
uv run python scripts/validate_agent_contracts.py
uv run python scripts/validate_golden_dataset.py

Set-Location frontend
npm run generate:api-types
npm run type-check
```

Agent contract 和 Prompt YAML 是人工维护的契约文件，由校验脚本检查字段、角色、白名单和绝对约束语言；它们不是自动生成物。

---

## 4. 一致性规则

- Tool 名称、参数或返回结构变化时，同步 Registry、Schema 快照、白名单、测试和架构工具表。
- Agent 输出变化时，同步 Pydantic 模型、Prompt、Agent contract、状态所有权和相关评估。
- API 变化时，重新导出 OpenAPI，再生成 `frontend/src/types/api.d.ts`，不得手写 DTO。
- 契约文件与实现不一致即缺陷；完整同步矩阵见 [`AGENTS.md` §8](../AGENTS.md)。
