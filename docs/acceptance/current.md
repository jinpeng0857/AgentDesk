# 当前验收结论

**结论：仍未通过真实模型完整图 E2E 门禁。** 本文同时记录最近一次候选运行的失败结论，以及随后基于该 artifact 完成的诊断与修复。修复尚未用 live E2E 复验，因此**不得**把任何“预期/投影”数值写成实测成绩。

## 1. 最近一次候选运行（未通过）

| 项目 | 值 |
|---|---|
| Artifact | `artifacts/evaluation/candidates/qwen3-30b-a3b-instruct-2507-awq-e2e80-final-20261023.local.json` |
| Run ID | `live-e2e-candidate-d2b8e0cf2347` |
| 数据集 | v1.0.0，reviewed/full，80/80 条 |
| 模型 | `Qwen3-30B-A3B-Instruct-2507-AWQ`，本地 OpenAI-compatible endpoint |
| 并发 | 4（实测最大 4） |
| Git | `dee52a2`，工作树非 clean |
| Gate | `threshold_gate.passed=false` |

| 指标 | 实测 | 门槛 | 结果 |
|---|---:|---:|---|
| Intent Accuracy | 0.9500 | ≥0.90 | 通过 |
| Handoff Accuracy | 0.4625 | ≥0.90 | 未通过 |
| Tool Selection Jaccard | 0.6708 | ≥0.85 | 未通过 |
| Policy Decision Accuracy | 1.0000（16 项决策，覆盖 0.5517） | 100% | 观察范围内通过，覆盖不足 |
| High-risk Bypass | 0 | 0 | 达标 |
| Task Success Rate | 0.1750 | ≥0.80 | 未通过 |
| Trajectory Efficiency | 0.9813 | ≥0.70 | 通过 |
| Redundant Tool Call Rate | 0.0000 | ≤0.20 | 通过 |
| Retrieval Precision / Recall | 0.0000 / 0.0000 | 口径保留 | 未通过 |
| Latency P95 | 13,495.17 ms | ≤10,000 ms | 未通过 |

终态：resolved 35、escalated 44、waiting_approval 1；报错 2 条（均为 `ModelOutputInvalid:ActionProposal`）。handoff 完全一致 10/80，工具集合完全一致 47/80，终态一致 22/80，三者同时一致 14/80。

逐例失败表：`artifacts/evaluation/diagnostics/qwen3-30b-e2e80-failure-table-20261023.csv`（由 `scripts/maintenance/diagnose_eval_failures.py` 生成）。

## 2. 诊断归因（详见 `docs/acceptance/diagnosis-20261023.md`）

| 根因 | 证据 | 影响 |
|---|---|---|
| 知识引用在检索后被清空 | `src/agents/knowledge.py` 只保留 chunk_id 完全相等的模型引用；空数组/文档 id 冒充 chunk/编造 id 都得到空列表 | retrieval 0；31/44 升级 |
| 知识索引缺 20/23 篇预期文档 | artifact 时点 fixture 仅 5 篇，dataset 引用 23 个 id | 20/23 个预期文档不可检索 |
| 写执行网关用 resource=None 重算 Policy | `nodes.py` action_execute 未传 `resource`；`executor.py` 重新 `assess_risk` | ACC-001/005/009/015 自服务重置被 POLICY_DENIED |
| high_risk 直接 finalize | 16 条 RFD 首次决策即 `finalize`；1.0.8 Prompt 未要求 high_risk 写请求先走 Action | 13 条 handoff 分歧、16 条终态错误 |
| 记录查询被送到 Knowledge | 11 条请求要 query_user/request_access/query_order | 11 条 handoff 分歧 |
| 完成后多跑节点 | 9 条多送 Action、4 条多送 Knowledge | 13 条 handoff 分歧 |
| check_service 中文名未解析 | `_ALIASES` 仅 6 个英文别名，DEMO_SERVICES 为 ASCII 键 | 6 条 TOOL_EXECUTION_FAILED |
| 确定性拒绝没有 rejected 出口 | 三处 route 均回 escalated；reject 节点仅 approval 可达 | 12 条 rejected 预期落空 |

## 3. 本轮已实施的修复（代码与契约）

- `src/agents/knowledge.py`：模型引用按 chunk_id 或 document_id 校验；模型未给出可验证引用时保留受治理检索的 top-1，避免把检索证据从工单里抹掉。
- `src/backend/fixtures/knowledge.json`：补齐 20 篇缺失文档（25 篇 / 51 chunk），并为 VPN 补一条应急指引。
- `src/backend/integrations/knowledge_store.py`：分块向量同时编码文档标题，修复“标题词命中”场景。
- `src/backend/graph/nodes.py`：新增 `_resource_context_for_operation`，policy 节点与 action_execute 使用同一 ResourceContext；action 节点的确定性拒绝改为返回空提案；`dispatch_action_execute` 不再把 governance rejection 预升级。
- `src/backend/graph/edges.py` + `builder.py`：新增 `reject_request` 终态节点；policy 拒绝、工具拒绝、确定性身份拒绝统一进入 `rejected`。
- `src/agents/policy.py`：退款操作在 resource 缺失订单时返回 DENY_RESOURCE_NOT_FOUND。
- `src/backend/integrations/tool_adapters.py`：`check_service` 增加中文显示名解析。
- `src/tools/registry.py` 与 `specs/tool_schemas/tools.json`：补充 tracked service 名称说明。
- `resources/prompts/supervisor.yaml` 1.0.9 + `specs/agent_contracts/supervisor.yaml` 1.0.3：新增 5 条强制路由规则（high_risk 写请求先走 Action、权限申请走 Action、账号/订单记录走 Action、不重复已完成步骤）。
- `src/eval/e2e.py`：retrieval 指标改记受治理 search 实际返回的 document_id；`reject_request` 归一为 role-level `finalize`。

## 4. 确定性验证

已执行（本机、无 PostgreSQL/无模型端点）：

- `uv run pytest tests/unit tests/integration/test_chroma_knowledge.py -q` → 310 passed
- `uv run ruff check src tests scripts` → 通过
- `uv run mypy src/agents src/tools src/skills src/backend src/eval scripts` → 通过
- `uv run python scripts/maintenance/validate_agent_contracts.py` → 通过
- `uv run python scripts/maintenance/validate_test_matrix.py` → 通过
- 检索离线探针（真实 Chroma，25 篇 fixture）：24 个需检索 case 的 top-1 命中 24/24，top-3 命中 24/24，Precision 0.5069，Recall 1.0000（**投影值，不是 live eval 成绩**）

新增回归测试：`tests/unit/test_resource_context.py`、`tests/unit/test_graph_rejection_and_knowledge.py`，以及 action routing / read tools / e2e handoff 的补充用例。

## 5. 未完成与阻塞

- **无法运行 live E2E**：本机没有 Ollama/vLLM 端点（`http://172.20.0.1:8001/v1` 不可达）且没有 PostgreSQL/Docker，因此**没有**新的 80 条实测结果，也没有把任何候选晋升为基线。
- 修复后的 handoff/task-success/延迟仍需在新的隔离 `_test` 数据库上跑完整 80 条确认；定向测试不能替代该重跑。
- RFD 系列的预期标签本身混合（rejected/waiting_approval/resolved/escalated），其中纯注入类（RFD-008/009/010 等）要求 Supervisor 直接 rejected；当前 RoutingDecision 契约没有 reject 目标。是否新增该目标属于路由语义变更，需要人工确认并同步标签与路由测试，本轮未擅自实施。
- P95 的 artifact 侧归因显示主因是模型往返次数与服务端状态，而非重复工具调用；减少往返预计可降 P95，但必须在 live 运行中验证。

## 6. 验收范围

本次完成的是：失败分类、逐例证据、代码/提示词/契约修复、确定性回归。**这不改变“真实模型 E2E 未通过”的结论**。后续必须先在可用环境执行完整 80 条候选，并保留逐例 artifact 后再讨论晋升。
