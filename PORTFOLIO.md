# AgentDesk｜项目表达与证据清单

> 本文件约束简历与面试表述，不替代设计或验收报告。
> 设计：[`docs/architecture.md`](docs/architecture.md) · 约束：[`AGENTS.md`](AGENTS.md) · 当前证据：[`docs/acceptance/current.md`](docs/acceptance/current.md)

## 表达原则

- 只有当前工作树可重复运行的代码、测试和工件才支撑“已实现/已通过”表述。
- Fake/Recorded Eval 证明 harness 和契约行为，不证明模型质量。
- `model-baseline` 只测模型分类；只有 `e2e-candidate` 才包含真实图、Tool Gateway、PostgreSQL/checkpoint 执行轨迹。
- 设计门槛不是结果；历史 Phase/L3 工件不是当前验收结果；跳过不是通过。
- 候选 artifact 不自动晋升为官方基线。所有数字需链接到逐例结果、模型、数据集、Prompt、时间和代码版本信息。

## 项目说明

AgentDesk 是模拟 IT Service Desk 工作流的模块化单体。Python 源码使用 `src/` 布局，但保留 `agents`、`tools`、`skills`、`backend`、`eval` 导入名。API 与 ARQ Worker 进程隔离；LangGraph 在 Worker 中执行；PostgreSQL 存储工单、审批、审计、操作账本及 checkpoint；Redis 提供队列与锁；Chroma 由 Knowledge Tool 只读访问。

实现边界包括四个固定角色、确定性 Dispatcher、Action propose/execute、统一工具执行治理、Policy 纯函数风险判定、绑定 `operation_id + args_hash` 的审批、严格 checkpoint 反序列化和可追溯评估。该系统使用模拟数据，不连接真实企业账户、订单或支付接口。

## 当前证据状态

逐层结果只以 [`docs/acceptance/current.md`](docs/acceptance/current.md) 中真实执行的命令、退出码和工件为准。验收结束前，不预先给以下能力写“全量通过”结论：

| 能力 | 所需证据 |
|---|---|
| Agent Orchestration | 路由/状态所有权单测、真实图集成和逐例节点轨迹 |
| Tool Governance | Schema/白名单/Policy 单测、数据库账本及副作用断言、确定性拒绝审计 |
| Human-in-the-loop | interrupt、绑定审批、resume 与重复操作集成证据 |
| Recovery | F-07 故障点后的持久化审计/账本、恢复终态与单次副作用证据 |
| Evaluation | 80 条完整固定样例的真实 `e2e-candidate`、门槛结果及追溯元数据 |
| Frontend | 固定 Node 22.22.2 下的 lint、类型检查、Vitest、生产构建 |
| Compose | API/Worker/Frontend 镜像构建和运行时 readiness/Smoke |
| Disaster recovery | `pg_dump`/`pg_restore` 的真实备份恢复和 schema/数据逐表比较 |

## 经过核实的历史工件边界

- `artifacts/evaluation/history/offline-fake-baseline.json`：离线确定性 Fake，不是模型质量或延迟基线。
- `artifacts/evaluation/history/historical-offline-fake-l3-final.json`：虽曾以“live”命名，内容实际是离线 Fake。
- `artifacts/evaluation/history/phase1-after.json` 和 `phase2-after.json`：旧运行器把 expected labels 用作 E2E 指标，不可引用其轨迹或成绩。
- `artifacts/evaluation/candidates/`：新 Live run 输出目录；候选状态必须保留，只有人工审阅后才能讨论晋升。

上述归档判断来自对工件 stage、mode、分母与旧运行器行为的读取；它不替代当前运行结果。

## 通过当前验收后可用的面试叙事

只挑当前报告中已通过的能力，回答：

1. 为什么按权限而不只是按角色把工单流程拆成四个 Agent；
2. 如何用确定性 Dispatcher 与状态字段唯一写入约束防止图路由漂移；
3. Action 为什么分提案和执行，写工具如何经过 Policy、审批、幂等、审计；
4. checkpoint 与 PostgreSQL 操作账本如何在 Worker 故障后防止重复副作用；
5. 如何将离线契约验证、真实服务集成、模型分类和完整图评估分层报告。

如果某项没有对应报告或 artifact，应将其表述为设计/实现范围，不表述为已通过验收。

## 简历量化结果登记

当前不在本文件预填成绩。通过完整 Live Eval 后，逐项从 `docs/acceptance/current.md` 和对应候选 artifact 中引用：

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
