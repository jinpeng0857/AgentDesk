# E2E 指标失败诊断（2026-10-23 Qwen3-30B 候选）

- Artifact：`artifacts/evaluation/candidates/qwen3-30b-a3b-instruct-2507-awq-e2e80-final-20261023.local.json`
- Run ID：`live-e2e-candidate-d2b8e0cf2347`；数据集体：reviewed/full 80 条；Git：`dee52a2`（工作树不干净）
- 模型：`Qwen3-30B-A3B-Instruct-2507-AWQ`，本地 vLLM，并发 4
- 结论：门禁未通过；本文只使用该 artifact 与当前代码做可追溯归因，不重新声明实测成绩

## 1. 指标与失败规模

| 指标 | 实测 | 门槛 |
|---|---:|---:|
| Intent Accuracy | 0.9500 | ≥0.90 |
| Handoff Accuracy | 0.4625 | ≥0.90 |
| Tool Selection Jaccard | 0.6708 | ≥0.85 |
| Task Success Rate | 0.1750 | ≥0.80 |
| Retrieval Precision / Recall | 0.0000 / 0.0000 | 口径保留 |
| Latency P95 | 13,495.17 ms | ≤10,000 ms |
| Policy Decision Accuracy | 1.0000（16 项决策） | 100% |
| High-risk Bypass | 0 | 0 |

终态混淆（预期 → 实际）：

| 转换 | 数量 |
|---|---:|
| resolved → escalated | 41 |
| resolved → resolved | 21 |
| rejected → resolved | 9 |
| waiting_approval → resolved | 4 |
| rejected → escalated | 3 |
| waiting_approval → waiting_approval | 1 |
| escalated → resolved | 1 |

80 条中 handoff 完全一致仅 10 条、工具集合完全一致 47 条、终态一致 22 条；工具与终态同时一致 14 条（即 task success 0.175）。

## 2. 逐项归因（按首个分歧点与代码证据）

### A. 知识引用在检索之后被清空（retrieval 0，31 条升级的直接触发）

证据：`src/agents/knowledge.py:52-67` 只保留模型输出中 `chunk_id` 与受治理检索结果完全相等的引用；模型输出空数组、把 `document_id` 写进 `chunk_id`、或编造 id 时都得到空列表。`src/backend/graph/nodes.py:144-174` 只把该过滤结果写回 `knowledge_refs`。

放大路径：`src/backend/graph/edges.py:96-101` 在 knowledge 已访问且 `knowledge_refs` 为空时，第二次 knowledge 路由直接转 escalated。该路径覆盖 31/44 次升级。

复现：在 HEAD 上单独探测该过滤函数，空列表 / document_id 冒充 chunk_id / 编造 id 均保留 0 条，只有完全相等保留 1 条。

### B. 知识索引缺少预期文档（retrieval 0 的结构性原因）

证据：artifact 时点的 `src/backend/fixtures/knowledge.json` 仅 5 篇文档，而 `resources/evaluation/golden_dataset.json` 的 `relevant_document_ids` 涉及 23 个 id，缺 20 个。`seed_knowledge` 会原样装入该 fixture（`src/eval/e2e.py:549`），所以 20/23 个预期文档在检索层不存在。

补充核查：Chroma 本身并非空库。用 5 篇旧 fixture 探测时每个查询仍返回 top-3（只是无关）；因此“检索返回 0”不是 Chroma 未装入，而是索引覆盖 + 引用过滤叠加。

### C. 写执行网关用 `resource=None` 重算 Policy（5 条自服务重置被拒）

证据：`src/backend/graph/nodes.py` 的 policy 节点用 ResourceContext 计算 allowed=true；同文件的 action_execute 调用 `WriteToolExecutor.execute` 时没有传 `resource`，而 `src/backend/governance/executor.py:466` 会重新执行 `assess_risk`。对 reset_password，`resource=None` 落到 `src/agents/policy.py:194-201` 的 DENY_RESOURCE_NOT_FOUND，最终 `POLICY_DENIED`。

受影响：ACC-001、ACC-005、ACC-009、ACC-015（本应 resolved）以及 ACC-014（本应 rejected）。

### D. high_risk 被直接 finalize（13 条 handoff 分歧 + 16 条终态错误）

证据：16 条 RFD 的首个 Supervisor 决策均为 `next_agent=finalize`、`reason_code=complete`、无任何工具；`src/backend/graph/edges.py` 对非 knowledge/action 目标落到 finalizer，`builder.py` 的 dispatch_finalizer 固定写 resolved。1.0.8 版 Supervisor Prompt 只规定 high_risk 分类，没有规定“high_risk 写请求必须先走 Action”。

### E. 记录查询被路由到 Knowledge（11 条 handoff 分歧）

证据：ACC-007/011/013、PRM-005/006/009、QRY-007/009/010/011/015 的首个转折为预期 `supervisor->action_propose`、实际 `supervisor->knowledge`。这些请求分别要 query_user / request_access / query_order，但 Prompt 的“记录查询走 Action”规则没有覆盖“权限申请”“账号资料/登录记录”“按订单号问政策”等表述边界。

### F. 完成后多跑一个节点（9 + 4 条 handoff 分歧）

证据：NET-004/007/009/011/012、PRM-001/014/016 在工具已成功后仍被 Supervisor 再送 Action（`route_from_action` 的已执行短路把它转 finalizer，但多出的 supervisor→action_propose 边仍计入 handoff）；PRM-007/010/011/013 在 request_access 成功后再送 Knowledge。

### G. check_service 中文服务名未解析（6 条 action_execute 升级）

证据：`src/backend/integrations/tool_adapters.py` 的 `_ALIASES` 只有 6 个英文别名，中文名原样传库；`DEMO_SERVICES` 的键是 ASCII，查不到即抛 LookupError，执行网关记为 `TOOL_EXECUTION_FAILED`。受影响：NET-003/008/011/013/015/016。

### H. 确定性拒绝没有 rejected 出口（12 条 rejected 预期全部落空）

证据：`route_from_action`（无可执行提案时回 supervisor）、`route_from_policy`（拒绝时回 escalated）、`route_from_action_execute`（非成功一律 escalated）都没有 rejected 分支；`builder.py` 的 reject 节点只从 approval 可达。ACC-002/PRM-002 的跨用户操作被确定性身份校验拦下后转成 ModelOutputInvalid → escalated，而不是 rejected。

### I. 延迟构成

artifact 侧估算（非实测）：80 例共 268 次模型往返（+2 次修复重试），中位 3 次/例；`latency ≈ 159 + 2049 × R`（R²=0.54）。重复 supervisor→action 仅 11 次（4.1%），对 P95 几乎无影响。主要成本是模型往返次数与运行期服务端状态；减少“知识后再次 supervisor”这类往返可降低 P95，但不能只靠并发调参。

## 3. 与审查结论的边界

- 5 篇旧 fixture 下 Chroma 仍返回 top-3，说明“Chroma 未装入”不成立；真实问题是索引覆盖不足 + 引用过滤。
- artifact 不记录原始工具参数，check_service 的中文名归因是强推断而非字段级证据。
- artifact 不记录逐节点耗时与 token，延迟只能做相关性与回归估算。
- Policy Decision Accuracy=1.0 仅覆盖 16 项决策；写路径覆盖不足，不能据此宣称写路径通过。
