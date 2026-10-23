# 当前验收结论

**结论：未通过（真实模型完整图 E2E 门槛失败）。** 本次已使用正式 reviewed 80-case 数据集运行完整候选；“80 条都被评测器尝试”不代表“80 条业务成功”。候选未晋升为基线。

## 本次运行

| 项目 | 值 |
|---|---|
| Artifact | `artifacts/evaluation/candidates/qwen3-8b-e2e80-concurrency4-20261021.local.json` |
| Run ID | `live-e2e-candidate-6cbb55acfd67` |
| 数据集 | v1.0.0，reviewed/full，80/80 条，80 个唯一 case ID |
| 模型 | `qwen3:8b`，本地 Ollama OpenAI-compatible endpoint |
| 并发 | 配置上限 4，实测最大同时运行 4 个 case |
| 数据库 | `agentdesk_e2e80_conc4_20261021_test`（专用 `_test` 数据库） |
| Git | `39bf219fb9b85d40f93301a09e8e2f9f8f4abc98`，工作树非 clean；artifact 含变更摘要 |
| Gate | `threshold_gate.passed=false` |

## 核心结果

| 指标 | 实测 | 门槛 | 结果 |
|---|---:|---:|---|
| Intent Accuracy | 98.75% | ≥90% | 通过 |
| Handoff Accuracy | 5.49% | ≥90% | 未通过 |
| Tool Selection Jaccard | 32.50% | ≥85% | 未通过 |
| Policy Decision Accuracy | 100%（仅 2 项决策） | 100% | 达标，但覆盖不足，不能据此证明完整 Policy 路径 |
| High-risk Bypass | 0 | 0 | 达标 |
| Task Success Rate | 0% | ≥80% | 未通过 |
| Trajectory Efficiency | 91.93% | ≥70% | 通过 |
| Redundant Tool Call Rate | 78.57% | ≤20% | 未通过 |
| case latency P95 | 155,112.53 ms | ≤10,000 ms | 未通过 |

失败/状态证据：68/80 个 case 未完成，其中 67 个记录为 `ModelOutputInvalid`，1 个为 `ModelTransportError`；数据库中 12 个工单为 `escalated`、68 个仍为 `running`。工具调用共 112 次（`search_knowledge` 111 次、`check_service` 1 次）；Policy 决策 2 项，Approval 记录 0、写操作账本 0。数据库核验到 80 个工单及 520 个 checkpoint 记录。

## 解释与边界

- 本次并发是**完整图 case 级并发**，实测峰值 4；不是 80 个 Worker/ARQ job 并发，也不是 FastAPI admission 基准。
- 失败轨迹中出现重复 Knowledge/搜索调用，但本次 artifact 没有把它们标注为经过人工核验的“模型幻觉死循环”，因此不报告该项数量。
- 67 个结构化输出错误只保留异常类别，没有保留可安全审阅的逐字段验证摘要；本 artifact 足以判定候选失败，不足以仅凭该文件归因到具体 schema 字段。
- 评测 runner 捕获图异常后记录异常类别并读取当前 checkpoint；没有像 Worker `GraphTicketRunner.fail()` 那样将这些异常统一转成 Dispatcher 的 `escalated` 终态。因此 68 个 `running` 同时暴露了**评测路径的失败终态处理缺口**，不能解释为 68 个仍在执行。
- Policy Accuracy 的 100% 仅基于 2 个观察到的判定；Approval、写 Tool 和完整写入/恢复路径在本次真实模型轨迹中没有覆盖，不能据此宣称这些路径已通过。

## 验收范围

本次完成 80 条真实模型完整图候选运行及 artifact 校验。并发调度、全量数据集门禁和相关评测单测通过；这不改变真实模型候选的失败结论。后续需先修复结构化输出/路由循环及 E2E runner 的异常终态写入，再以新的独立 `_test` 数据库重跑完整 80 条。定向测试不能替代该重跑。
