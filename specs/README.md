# Specs Index — 契约索引与冻结决策

> 本目录存放**与实现强一致的契约**（Agent 契约、工具 Schema），本文件只做索引与冻结决策记录，**不承载方案内容**。
>
> 版本：v1.0（冻结版）
> 完整方案：[`docs/architecture.md`](../docs/architecture.md) · 开发约束：[`AGENTS.md`](../AGENTS.md)（唯一真源） · 项目表达：[`PORTFOLIO.md`](../PORTFOLIO.md)
>
> 文档导航以 [`AGENTS.md` §0](../AGENTS.md) 为唯一来源，此处不重复维护清单。

---

## 1. 冻结决策清单

以下 7 条是架构层面的**不可协商决策**，实现时不得偏离；如需变更，先改 [`docs/architecture.md` §5.3](../docs/architecture.md) 与 [`AGENTS.md` §2](../AGENTS.md)，再改代码。

| 编号 | 决策 | 对应硬约束 | 方案出处 |
|---|---|---|---|
| D-01 | 路由由确定性代码完成，Supervisor 只产出结构化 `RoutingDecision`，Dispatcher 按 `next_agent` 分支；路由环节不调用 LLM | H-04 | [`docs/architecture.md` §5.3](../docs/architecture.md) |
| D-02 | 状态写入权限隔离：每个节点只写自己负责的 `TicketState` 字段 | H-03 | [`docs/architecture.md` §6](../docs/architecture.md)、[`AGENTS.md` §2.1](../AGENTS.md) |
| D-03 | 每工单最大 15 步，超限强制升级人工 | H-07 | [`docs/architecture.md` §13.4](../docs/architecture.md) |
| D-04 | Checkpointer 在每个节点执行后自动存档，支持从任意检查点恢复 | — | [`docs/architecture.md` §10.1](../docs/architecture.md) |
| D-05 | 前后端通过 OpenAPI 契约对齐，前端类型必须由 `openapi-typescript` 生成 | H-10 | [`docs/architecture.md` §14.3](../docs/architecture.md) |
| D-06 | Supervisor 不直接调用任何工具 | H-02 | [`docs/architecture.md` §7.2](../docs/architecture.md) |
| D-07 | 领域层（`agents/`、`tools/`、`skills/`）与服务层（`backend/`）分离，依赖单向：服务层 → 领域层 | H-21 | [`docs/architecture.md` §5.1](../docs/architecture.md) |

---

## 2. 本目录文件与依据文档

| 文件 | 内容 | 依据文档 |
|---|---|---|
| `agent_contracts/supervisor.yaml` | Supervisor 的输入、输出、决策边界 | [`docs/architecture.md` §7.2](../docs/architecture.md) |
| `agent_contracts/knowledge.yaml` | Knowledge 的输入、输出、只读约束 | [`docs/architecture.md` §7.3](../docs/architecture.md) |
| `agent_contracts/action.yaml` | Action 的工具白名单与幂等要求 | [`docs/architecture.md` §7.4](../docs/architecture.md) |
| `agent_contracts/policy.yaml` | Policy 的风险判定规则与输出结构 | [`docs/architecture.md` §7.5](../docs/architecture.md) |
| `tool_schemas/tools.json` | 8 个工具的完整 Schema | [`docs/architecture.md` §8](../docs/architecture.md) |

**一致性要求**：契约文件与实现不一致即缺陷。工具或 Agent 输出结构发生变化时，必须同步本目录文件（见 [`AGENTS.md` §5.2 变更影响面自查清单](../AGENTS.md)）。
