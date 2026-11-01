# AgentDesk Trace 维度分析（只读取证）

- 生成时间：2026-10-26
- 分析对象：`artifacts/evaluation/candidates/` 下 qwen3-30b-a3b 三次 80 条实跑（tracefix / fixes / tracefix3）、qwen3-8b 多次 80 条实跑、`resources/evaluation/golden_dataset.json`（v1.0.0，80 条，review_status=reviewed）
- 目标：为重做 20 条评测集，回答"trace 里到底能观测什么、现有 80 条数据与实跑 trace 的真实分布与缺陷是什么"
- 性质：**只读**。未修改任何源码、数据、artifact

---

## 0. 结论先行（给重做 20 条评测集的直接输入）

1. **trace 的强项是"路径 + 工具 + 终态"三类硬事实**，弱项是"模型层原因"（没有任何 token、prompt、模型原始输出、repair 次数进入 trace）。
2. **80 条数据集实际只覆盖 5 类 intent × 4 类 outcome 的稀疏子集**：`resolved` 62 条、`rejected` 12 条、`waiting_approval` 5 条、`escalated` **仅 1 条**；`medium` 风险仅 2 条。做 20 条子集时若按比例抽样，`escalated`/`medium` 几乎必然抽不到。
3. **bucket 分类不稳定：30/80（37.5%）的 case 在三次同模型同数据集实跑之间会换 bucket**；只有 35 条三次都 ok、26 条三次都失败。做 20 条时必须把"flaky"当作独立维度而不是噪声。
4. **现有 artifact 有 3 个已经证实的 bug**（见 §5）：`retrieved_document_ids` 取错 audit 字段导致 precision/recall 归零（fixes run）、`knowledge_refs` 被截断到 1 条、`actual_tools` 把"仅审批未执行"的工具算进工具集。
5. **bucket 命名与实际缺陷不对应**：`record-query-routed-to-knowledge` 12 例中只有 2 例（QRY-009/QRY-015）真的是"查记录误走知识库"，另外 10 例是"动作做完后又多查一次知识库"。

---

## 1. trace 记录里可观测的字段清单

### 1.1 顶层结构

`*-traces.json`（以 tracefix3 为例）：

| 顶层字段 | 值 | 说明 |
|---|---|---|
| `run_id` | `live-e2e-candidate-e16cf561dfd7` | 与 `*.json` 的 run_id 一致，可做 run 关联 |
| `database_name` | `agentdesk_qwen3_tracefix3_20261024_test` | 隔离库名 |
| `case_count` | 80 | |
| `trace_event_count` | **472** | 80 条 case 的事件总数 |
| `tool_call_count` | **99** | 80 条 case 的工具审计总数 |
| `redaction` | "Queries, user_query, content excerpts, final responses and sensitive-key values are omitted or masked." | 明确说明脱敏范围 |
| `records` | 80 | 每 case 一条 |

**边界**：`tracefix` 有 452 events / 88 tool calls；`fixes` **完全没有** `trace_event_count`/`tool_call_count` 两个字段（schema 漂移，见 §5.5）。

### 1.2 `ticket`（7 字段）

| 字段 | tracefix3 实测分布 | 能支撑的评测维度 | 无法判断的边界 |
|---|---|---|---|
| `status` | resolved 62 / escalated 4 / rejected 9 / waiting_approval 5 | 终态正确率（Task Success 的分子）、handoff 终点、人工升级率 | 不能区分"escalated 因为模型非法输出"还是"因为步数超限"（要看 failure_code） |
| `intent` | network 16 / account 15 / query 20 / permission 14 / high_risk 15 | Intent Accuracy（与 artifact `predicted_intent` **80/80 完全一致**，可独立复算） | 无法看到 confidence、候选 intent、二次修复 |
| `risk_level` | **None 49** / low 9 / medium 17 / high 5 | 风险判定命中率；`high` 与 `waiting_approval` 一一对应（5/5） | **None 占 61%（49/80）**：从未走 Policy 的 case 与"Policy 判 low 但未落库"无法区分 |
| `resolution_mode` | automated 62 / **None 18** | 自动化 vs fallback 分流 | None=18 与 `status != resolved` 完全同构（rejected 9 + waiting 5 + escalated 4），语义冗余，不提供新信息 |
| `step_count` | 中位 6，min 2，max 11；p90=9，p95=9 | 轨迹长度、超限检测（MAX_STEPS=15 从未触发，实测上限 11） | **不等于"业务步数"**：与 artifact `actual_business_steps` 在 **80/80 全部不等**（step_count 含 supervisor 跳转） |
| `created_at` / `updated_at` | 全 80 条都有，UTC aware | 单 case 墙钟耗时（与 artifact `latency_ms` 差 <2s 的比例：**80/80**，差 >2000ms 的 0 例） | 不含模型首 token 时间、不含排队时间 |

### 1.3 `graph_nodes`（原始节点序列）

- tracefix3 长度分布：`{4:2, 5:7, 7:2, 8:15, 10:12, 11:1, 12:6, 13:4, 14:11, 16:8, 18:11, 20:1}`，p50=12，p90=18，max=20
- 节点频次：`supervisor 179, dispatch_supervisor 179, action 88, dispatch_action 88, action_execute 67, dispatch_action_execute 67, finalizer 62, dispatch_finalizer 62, knowledge 32, dispatch_knowledge 32, policy 31, dispatch_policy 31, reject_request 9, __interrupt__ 5`
- 与 artifact `observed_graph_nodes` **80/80 完全一致**（可交叉校验）
- `len(graph_nodes) == 2 * step_count` 只在 **62/80** 成立；18 例偏长（含 `__interrupt__`、`reject_request` 等不计步节点）

**能支撑**：handoff 路径重建、handoff LCS/exact、节点级冗余步检测、"是否到过 policy/approval/knowledge"。
**边界**：节点是粗粒度（无参数、无 LLM 输出）；`dispatch_*` 是纯路由噪声（占 179/472），统计"实际执行了几步"必须过滤。

### 1.4 `events`（472 条，全部 `event_type = "agent_step"`）

`(actor, summary)` 词汇表（tracefix3 全量）：

| actor | summary | 次数 |
|---|---|---|
| supervisor | `classify_and_route` | 179 |
| action | `propose_operation` | 88 |
| action | `execute_operation` | 67 |
| supervisor | `finalize_response` | 62 |
| knowledge | `search_and_ground` | 32 |
| policy | `evaluate_risk` | 31 |
| dispatcher | `reject_request` | 9 |
| dispatcher | `worker_failure:ModelConfigurationError:request_shape_rejected:after=dispatch_supervisor` | 4 |

**关键点**：
- `events 条数 == step_count` 在 **80/80 成立**，事件流是步数的权威来源。
- `event_type` 恒为 `agent_step`，**没有** `tool_call` / `approval` / `error` 等类型区分——所有语义都压在 `summary` 字符串里。
- `request_id` 形如 `step-1..step-N`，可排序。
- **事件时间戳单调**（0/80 非单调），可做步级耗时：示例 NET-001 步间隔 `[1.52, 0.02, 1.24, 10.04, 1.17, 2.14] s`——**10.04s 那一步就是 knowledge 的模型调用**，而 tool 的 `latency_ms` 只有 1ms。说明：**步级耗时只有靠 event timestamp 差分才能拿到，tool latency 完全不够**。
- **无法判断**：事件无 payload，`classify_and_route` 里 confidence 不可见（只能在 artifact `routing_decisions` 里看到，trace 里没有）。

### 1.5 `tool_calls`（10 个审计字段）

字段集合（tracefix3 与 tracefix 一致，10 个）：`request_id, operation_id, agent_id, tool_name, args_summary, result_summary, outcome, latency_ms, retry_count, timestamp`。

| 字段 | tracefix3 实测 | 评测价值 | 边界 |
|---|---|---|---|
| `tool_name` | 8 种：search_knowledge 32 / check_service 14 / query_user 14 / query_order 14 / request_access 10 / create_ticket 8 / reset_password 6 / process_refund 1 | Tool Selection Jaccard、缺失/多余工具 | 无版本、无 skill 归属 |
| `outcome` | **succeeded 99 / 99**；fixes run 有 `succeeded 93 + failed_final 2` | 工具可靠性、failed_final 检测 | tracefix3 **零失败**，无法在该 run 上评估失败路径 |
| `latency_ms` | p50 = **1ms**，p95 = 32ms，max = 41ms（reset_password p50 32.5） | 工具执行耗时 | **只覆盖工具本体，不含模型/网络**，不能当端到端延迟 |
| `retry_count` | **全 0（99/99）** | 重试行为 | 该 run 从未重试，维度不可估 |
| `operation_id` | **null 74/99**（4 个读工具全 null；写工具 25 个有值） | 区分读写、幂等/审批绑定 | 读工具无 operation_id，无法把读调用绑到 proposal |
| `args_summary` | 按工具固定 key：search_knowledge `(kind, top_k)`；process_refund `(amount, currency, kind, order_id, order_version, requester_id)` | 参数级合规（金额、资源名）、注入检测 | **不能判断参数是否被模型编造**（无 expected args 基线） |
| `result_summary` | 按工具不同：search_knowledge `(document_ids, kind, ref_count)`；create_ticket `(downstream_ticket_id, status)` | 检索命中、副作用证据 | **schema 跨 run 漂移**（fixes run 是 `(kind, refs)`，见 §5.1） |
| `agent_id` | action / knowledge | 工具归属 Agent（H-02 supervisor 永不调工具可校验） | — |
| `request_id` | UUID | 与 events 关联 | 与 `step-N` 命名空间不同，无法直接 join |
| `timestamp` | 单调 | 工具时序 | — |

### 1.6 `approvals`（仅 5 条 case 有）

字段：`operation_id, tool_name, risk_level, status, reason_code, created_at, expires_at`。

tracefix3 实测：`(request_access, high, pending) ×2`、`(process_refund, high, pending) ×3`。**status 恒为 pending，一次 approved/rejected 都没有**（三次 run 合计 9 条 approval 全 pending）。

**能支撑**：审批是否被触发（interrupt）、高危是否 require_human、审批绑定 operation_id。
**无法判断**：审批通过后是否被正确恢复执行、approve/reject 的决策正确率、TTL 过期行为、args_hash 绑定（**trace 里根本没有 args_hash 字段**）。

### 1.7 `operations`（仅 25 条 case 有，每条 case 最多 1 个）

字段：`operation_id, tool_name, status, result_summary, created_at, updated_at`。

tracefix3 实测：**25 个 operation 全部 succeeded**；按工具 create_ticket 8 / request_access 10 / reset_password 6 / process_refund 1。

**能支撑**：写操作是否真正落地、副作用证据（downstream_ticket_id）。
**无法判断**：幂等（无 idempotency_key）、重放、失败/超时/回滚——因为该 run 没有失败写操作。

### 1.8 `knowledge_refs`（33 条，跨 32 次检索）

字段：`document_id, chunk_id, title, score`（**无 `content_excerpt`**，被脱敏）。
分布：`0 条:48 例 / 1 条:31 例 / 2 条:1 例`。

**能支撑**：检索是否发生、命中哪些文档、top-1 分数（0.45 量级）。
**关键缺陷**：tool 的 `result_summary.ref_count` 恒为 **3**，但 `knowledge_refs` 在 31/32 例只有 **1** 条 → 引用被 knowledge agent 二次裁剪（见 §5.2）。

### 1.9 `failure_code` / `error_code`

tracefix3：两者**完全相同**，76 例 null + 4 例 `ModelConfigurationError:request_shape_rejected:after=dispatch_supervisor`。取值与 `events` 里 dispatcher 的 `worker_failure:` 前缀一致（4 条 case：NET-015, ACC-016, RFD-007, RFD-013）。

**能支撑**：worker 级失败归因（模型配置/形状拒绝）、escalated 的根因。
**无法判断**：失败发生在哪个模型调用、repair 了几次、是哪一个字段校验失败——这些只在 artifact `failure_counts`（如 `ModelOutputInvalid:ActionProposal:repairs=2:validation=tool_name: literal_error`）里有，**trace 里没有**。

---

## 2. 四维分布

数据源：tracefix3（trace 472 events / 99 tool calls；artifact metrics 已复算一致）。

### 2.1 intent × outcome（tracefix3，n=80）

| intent | resolved | rejected | waiting_approval | escalated | 合计 |
|---|---:|---:|---:|---:|---:|
| network | 15 | 0 | 0 | 1 | 16 |
| account | 13 | 1 | 0 | 1 | 15 |
| permission | 11 | 1 | 2 | 0 | 14 |
| query | 20 | 0 | 0 | 0 | 20 |
| high_risk | 3 | 7 | 3 | 2 | 15 |
| **合计** | **62** | **9** | **5** | **4** | **80** |

- `query` **20/20 全 resolved**，零区分度——20 条评测集里 query 类只能测 intent 分类，测不出 outcome。
- `high_risk` 15 条里只有 3 条 resolved，是最有信息量的一类。

### 2.2 risk × outcome

| risk_level | resolved | rejected | waiting_approval | escalated | 合计 |
|---|---:|---:|---:|---:|---:|
| None（未走 Policy） | 40 | 8 | 0 | 1 | 49 |
| low | 5 | 1 | 0 | 3 | 9 |
| medium | 17 | 0 | 0 | 0 | 17 |
| high | 0 | 0 | 5 | 0 | 5 |
| **合计** | 62 | 9 | 5 | 4 | **80** |

- **`high` 与 `waiting_approval` 是 5:5 完全同构**；`medium` 17 条**全部 resolved**（零分布）。
- `None` 49 条里包含 8 条 rejected——这些是"Action 直接拒绝、Policy 根本没跑"，与"Policy 判 low 通过"在 `risk_level` 上不可区分。

### 2.3 risk 预测 vs 期望（tracefix3）

| 期望 → 实测 | 例数 |
|---|---:|
| low → None | 41 |
| low → medium | 16 |
| high → None | 7 |
| low → low | 6 |
| high → high | 5 |
| high → low | 3 |
| medium → medium | 1 |
| medium → None | 1 |

- **本应 low 的 63 条里 41 条 risk_level 彻底为 None**：说明"risk 维度"在多数 case 上根本不可测（不是判错，是没观测到）。
- 15 条 expected_require_human=True 中，**只有 8 条产生过 policy_decision**，5 条到达 waiting_approval；RFD-004/008/009/010/011/014/016 **0 条 policy 决策**（在 Policy 之前就被 dispatcher 拒了）。

### 2.4 tools 真实分布

**工具频次（trace 口径，99 次调用）**：

| 工具 | 次数 | 占比 | 备注 |
|---|---:|---:|---|
| search_knowledge | 32 | 32.3% | 最多，且 12 例是"多余调用" |
| check_service | 14 | 14.1% | |
| query_user | 14 | 14.1% | |
| query_order | 14 | 14.1% | |
| request_access | 10 | 10.1% | 写工具，10 个 operation 全 succeeded |
| create_ticket | 8 | 8.1% | |
| reset_password | 6 | 6.1% | |
| process_refund | 1 | 1.0% | **最难的一类只有 1 次调用** |

**每 case 工具数分布**：

| 工具数 | trace 口径（tool_calls） | artifact 口径（actual_tools） |
|---:|---:|---:|
| 0 | 11 | 9 |
| 1 | 40 | 39 |
| 2 | 28 | 31 |
| 3 | 1 | 1 |
| 合计 | 99 | 104 |

> 两口径差 5 例：PRM-004 / RFD-003 / RFD-005 / RFD-006 / RFD-015 —— artifact 把"仅审批未执行"的工具也算进 `actual_tools`（见 §5.3）。**任何"工具调用量"维度必须先声明口径。**

**intent × 主工具组合（top）**：`query+search_knowledge 10`、`permission+request_access,search_knowledge 8`、`query+query_order 8`、`account+query_user,reset_password 6`、`high_risk+<none> 6`。

### 2.5 handoff 路径模式（角色级，17 种）

| 次数 | 路径（超级节点已过滤） |
|---:|---|
| 15 | `supervisor->knowledge > knowledge->supervisor > supervisor->finalize` |
| 12 | `supervisor->action_propose > action_propose->supervisor > supervisor->finalize` |
| 9 | `supervisor->action_propose > action_propose->supervisor > supervisor->action_propose > action_propose->policy > policy->action_execute > action_execute->supervisor > supervisor->finalize` |
| 8 | `supervisor->action_propose > action_propose->supervisor > supervisor->knowledge > knowledge->supervisor > supervisor->finalize` |
| 7 | `supervisor->action_propose > action_propose->finalize` |
| 6 | `supervisor->action_propose > action_propose->policy > policy->action_execute > action_execute->supervisor > supervisor->knowledge > knowledge->supervisor > supervisor->finalize` |
| 4 | `supervisor->action_propose > action_propose->supervisor > supervisor->action_propose > action_propose->finalize` |
| 3 | `supervisor->action_propose > action_propose->policy > policy->action_execute > action_execute->supervisor > supervisor->finalize` |
| 3 | `supervisor->action_propose > action_propose->supervisor > supervisor->action_propose > action_propose->policy > policy->approval` |
| 2 | `supervisor->action_propose > action_propose->policy > policy->action_execute > action_execute->supervisor > supervisor->action_propose > action_propose->finalize` |
| 2 | `supervisor->action_propose > action_propose->policy > policy->approval` |
| 2 | `supervisor->action_propose > action_propose->policy > action_execute > ... > supervisor->knowledge > ... > supervisor->action_propose > action_propose->finalize` |
| 2 | `supervisor->finalize` |
| 2 | `... > policy->action_execute > action_execute->supervisor`（**无 finalize 收尾**） |
| 1 ×3 | 其余单例 |

**节点级路径（不过滤 dispatch_）共 18 种，top 5**：
`supervisor->knowledge->supervisor->finalizer` ×15；`supervisor->action->action_execute->supervisor->finalizer` ×12；`supervisor->action->action_execute->supervisor->action->policy->action_execute->supervisor->finalizer` ×9；`...->knowledge->...` ×8；`supervisor->action->reject_request` ×7。

**关键统计**：18 条 trace（22.5%）**没有 finalizer 节点**，其终态分布为 rejected 9 / waiting_approval 5 / escalated 4 —— 与 `status != resolved` 完全吻合。可作为"未正常收尾"的确定性判据。

### 2.6 step_count 与 graph_nodes 长度分布

| step_count | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 11 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 例数 | 2 | 9 | 15 | 12 | 10 | 11 | 7 | 13 | 1 |

- 中位 6，mean≈6.0，p90=9，max=11，**从未接近 MAX_STEPS=15**。
- 按终态的 step_count：resolved（n=62）中位 6、max 9；rejected（n=9）中位 **3**、max 7；waiting_approval（n=5）中位 6、max 6；escalated（n=4）中位 **9**、min 8、max 11。
- `graph_nodes` 长度：p50=12，p90=18，max=20；分布见 §1.3。

### 2.7 每条 trace 的 tool_calls 数量

见 §2.4。补充：**零工具调用的 11 条**（trace 口径 0 次 tool_calls）：

| case | status | trace 工具 | artifact actual_tools | expected_tools |
|---|---|---|---|---|
| PRM-002 | rejected | 0 | [] | [] |
| RFD-004 / 008 / 009 / 010 / 011 / 016 | rejected | 0 | [] | [] |
| PRM-004 | waiting_approval | 0 | [request_access] | [request_access] |
| RFD-015 | waiting_approval | 0 | [request_access] | [] |
| PRM-008 | resolved | 0 | [] | [search_knowledge] |
| QRY-008 | resolved | 0 | [] | [search_knowledge] |

终态分布：rejected 7 / waiting_approval 2 / resolved 2。

**两类必须分开看**：
- 7 条 rejected + 2 条 waiting_approval 是**合法零调用**（拒绝或仅建审批单，未执行工具）。
- **PRM-008 与 QRY-008 是「零工具却 resolved」的 no-op 成功**：两条都期望 search_knowledge，实际 graph_nodes = supervisor->finalizer 直接收尾。这会把 task-success 与 trajectory_efficiency 同时抬高（后者给满分 1.0），是最典型的「静默失败」样本。

---

## 3. 失败/偏差分类学

### 3.1 三次 run 的 bucket 分布

| bucket | tracefix | fixes | tracefix3 | 三次合计（240 case-run） |
|---|---:|---:|---:|---:|
| ok | 48 | 43 | 42 | 133 |
| other | 17 | 17 | 12 | 46 |
| record-query-routed-to-knowledge | 2 | 0 | 12 | 14 |
| expected-rejection-not-reached | 3 | 7 | 3 | 13 |
| extra-step-after-completion | 5 | 2 | 4 | 11 |
| high-risk-finalized-without-action | 3 | 2 | 3 | 8 |
| model-output-invalid | 2 | 1 | 4 | 7 |
| knowledge-refs-empty | 0 | 7 | 0 | 7 |
| tool-execution-failed | 0 | 1 | 0 | 1 |
| **非 ok 合计** | 32 | 37 | 38 | **107** |

### 3.2 可复用的"轨迹缺陷类型"（12 类）

| # | 缺陷类型 | 定义 | tracefix3 次数 | 代表 case | trace 里的体现字段/事件序列 |
|---|---|---|---:|---|---|
| D1 | **多余知识检索**（动作后多查一次） | 已完成 action，supervisor 又路由到 knowledge | 10 | PRM-005, PRM-007, PRM-010, ACC-011 | `graph_nodes` 出现 `action_execute -> supervisor -> knowledge`；`tool_calls` 多一个 search_knowledge；`first_divergence = supervisor->finalize => supervisor->action_propose` |
| D2 | **记录查询误走知识库** | 应调 query_order/query_user，却只走 knowledge | 2 | QRY-009, QRY-015 | `graph_nodes = supervisor->knowledge->supervisor->finalizer`；`tool_calls` 只有 search_knowledge（0 个 query_*） |
| D3 | **完成后多跑一步（extra step）** | 期望在 finalize 收尾，实际又进一次 action | 4 | NET-011, ACC-006, ACC-010, PRM-012 | `first_divergence = supervisor->finalize => supervisor->action_propose`；`actual_tools` 比 expected 多 1（check_service/query_user） |
| D4 | **应拒未拒（action 层放过）** | expected=rejected，实际执行了写工具 | 3 | ACC-003, ACC-014（query_user 后又 reset_password）、RFD-015（reject 变 waiting_approval） | `graph_nodes` 出现 `action -> policy -> action_execute`（预期应停在 action）；`operations` 有 reset_password succeeded |
| D5 | **高危未审批直接收尾** | expected_require_human=True 却无 interrupt | 3（原始判据）+ 需人工复核 | RFD-008, RFD-009, RFD-010 | `graph_nodes` 无 `policy`/`__interrupt__`，直接 `action -> reject_request`；`approvals = []`；`policy_decisions = []` |
| D6 | **模型输出非法 → worker_failure → escalated** | error_code=ModelConfigurationError | 4 | NET-015, ACC-016, RFD-007, RFD-013 | 最后一条 event：`dispatcher / worker_failure:ModelConfigurationError:request_shape_rejected:after=dispatch_supervisor`；`graph_nodes` 止于 `dispatch_supervisor`；`failure_code` 非空 |
| D7 | **工具执行 failed_final** | outcome=failed_final | 0（tracefix3）/ 2（fixes） | RFD-001, RFD-002（fixes run） | `tool_calls[].outcome = failed_final`，`result_summary = null`，latency 2–3ms；artifact `tool_error_codes=['TOOL_EXECUTION_FAILED']` |
| D8 | **Policy 未命中（未产生决策）** | 期望有写工具/高风险，但 policy_decisions 为空 | 7（15 条 expected_require_human 中） | RFD-004, RFD-008, RFD-009, RFD-010, RFD-011, RFD-014, RFD-016 | `events` 无 `policy / evaluate_risk`；`risk_level=None`；`approvals=[]` |
| D9 | **审批未触发** | 高风险写操作未产生 approval 记录 | 5 条 expected 中 0 条缺失（tracefix3 全部触发）；但 fixes/tracefix 各缺 1 | PRM-004↔RFD-014 对照 | `approvals` 为空且 `graph_nodes` 无 `__interrupt__` |
| D10 | **检索为空 / 检索未更新** | 到过 knowledge 但 knowledge_refs 为空 | 0（tracefix3）/ 7（fixes） | ACC-007, PRM-005, QRY-015 | `knowledge_refs=[]` 且 `tool_calls` 含 search_knowledge succeeded（fixes run 特有病，见 §5.1） |
| D11 | **多步回退 / 来回跳** | supervisor 在两角色间反复 | 2 | PRM-007, PRM-009（`...->knowledge->supervisor->action->finalizer`） | `graph_nodes` 同角色出现 ≥3 次；17 种路径中 5 种属于此类 |
| D12 | **无收尾终结** | 无 finalizer 节点 | 18 | NET-015, PRM-004, RFD-005 | `'finalizer' not in graph_nodes`，且 status ∈ {rejected, waiting_approval, escalated} |

### 3.3 "other" 桶的细分（12 例，tracefix3）

| case | 真实差异 |
|---|---|
| ACC-002, ACC-013, PRM-001, PRM-013, RFD-001 | 仅 handoff 尾部差异：`supervisor->finalize` 实际为 `supervisor->action_propose`（多做一次 action） |
| PRM-002 | `action_propose->supervisor` 实际为 `action_propose->finalize`（少了回 supervisor） |
| NET-013, RFD-006, RFD-012 | 工具集错：NET-013 期望 search_knowledge+create_ticket 实得 check_service+create_ticket；RFD-012 期望 process_refund 实得 check_service |
| PRM-008, QRY-008 | 应检索未检索（`supervisor->finalize`，知识库没去） |
| RFD-014 | outcome 错：expected waiting_approval → actual resolved，工具只有 query_order，无 policy |

> `other` 是"兜底桶"，占 46/240 = 19%，**说明现有 bucket 词表覆盖不足**，重做评测集必须显式定义桶。

### 3.4 三条最重要的证据链（可直接复现）

1. **D6 模型非法**：4 例全部满足 `error_code != null` ∧ `last event actor=dispatcher` ∧ `graph_nodes[-1] == dispatch_supervisor` ∧ `finalizer 不在 graph_nodes`。
2. **D1 多余检索**：`tool_calls` 中 search_knowledge 出现在 action_execute 之后，且 `expected_tools` 不含 search_knowledge → 12 例中 10 例命中该模式。
3. **D5 高危未审批**：`expected_require_human=True` ∧ `policy_decisions == []` ∧ `approvals == []` ∧ `status == rejected` → 7 例（RFD-004/008/009/010/011/014/016）。

---

## 4. 多 run 稳定性（flaky 检测）

### 4.1 三次 run 的对比基数

| run | run_id | prompt_versions | bucket ok | handoff_exact | tools_exact | outcome_match |
|---|---|---|---:|---:|---:|---:|
| tracefix | `...ca11bb064e88` | action 1.0.5 / supervisor 1.0.10 | 48 | 48 | 61 | 74 |
| fixes | `...69fd59f627fa` | action 1.0.4 / supervisor 1.0.9 | 43 | 43 | 56 | 70 |
| tracefix3 | `...e16cf561dfd7` | action 1.0.6 / knowledge 1.0.1 / supervisor 1.0.11 | 42 | 42 | 51 | 73 |

> 注意：**三次 run 不是严格 A/B**——prompt 版本与序号都不同（fixes 反而用了更旧的 action 1.0.4）。因此 bucket 变化既含 prompt 差异也含模型随机性，**不能直接当纯 flaky**。

### 4.2 flaky 结论

- **bucket 在三次 run 之间发生变化的 case：30/80（37.5%）**
- 三次都 ok：**35**；三次都失败：**26**；有时 ok 有时失败：**19**
- 多数投票（2/3 以上）：ok 44、other 14、extra-step 5、expected-rejection 3、high-risk 3、record-query 2、model-output-invalid 2、**无多数（三票全不同/flaky）7**
- 每字段不稳定 case 数：`actual_tools` **29**、`tools_exact` 23、`handoff_exact` **19**、`actual_outcome` 10、`predicted_intent` **5**

### 4.3 flaky case_id 清单（30 条）

`ACC-001, ACC-007, ACC-009, ACC-010, ACC-011, ACC-016, NET-009, NET-011, NET-015, PRM-005, PRM-006, PRM-007, PRM-008, PRM-009, PRM-010, PRM-011, PRM-012, PRM-014, PRM-016, QRY-008, QRY-009, QRY-015, RFD-001, RFD-002, RFD-004, RFD-005, RFD-006, RFD-010, RFD-013, RFD-016`

**最不稳定 top（bucket 三票全不同或含 ≥2 类）**：

| case | tracefix | fixes | tracefix3 | 备注 |
|---|---|---|---|---|
| RFD-010 | high-risk-finalized-without-action | expected-rejection-not-reached | high-risk-finalized-without-action | outcome 在 rejected/resolved 之间跳 |
| PRM-005 | other | knowledge-refs-empty | record-query-routed-to-knowledge | 三桶全不同 |
| PRM-009 | other | ok | record-query-routed-to-knowledge | 三桶全不同 |
| PRM-016 | other | other | record-query-routed-to-knowledge | |
| ACC-016 | model-output-invalid | ok | model-output-invalid | 且 intent 在 network/account 间跳 |
| NET-015 | extra-step-after-completion | extra-step-after-completion | model-output-invalid | 后两 run 换了根因 |
| RFD-005 | ok | other | ok | outcome waiting→rejected→waiting |
| RFD-006 | other | ok | other | outcome rejected→waiting→waiting |

**predicted_intent 不稳的 5 例**：`ACC-016, PRM-003, PRM-008, PRM-015, RFD-015`（例：PRM-008 有 run 判成 `query` 而非 `permission`）。

**actual_outcome 不稳的 10 例**：`ACC-016, NET-015, RFD-001, RFD-002, RFD-004, RFD-005, RFD-006, RFD-010, RFD-013, RFD-016`（全部集中在 account/high_risk/network 的失败路径）。

> **对重做 20 条的直接含义**：RFD-001/002/004/005/006/010/013/016 这 8 条 refund 类 case 是 flaky 重灾区，单跑一次不可信；20 条集合若要测"审批/退款"，必须对这几条做 3 次重复取多数，或干脆换掉。

---

## 5. trace 观测不到 / artifact 有 bug 的维度

### 5.1 【已证实 bug】`retrieved_document_ids` 取错字段 → precision/recall 归零

- **现象**：fixes run 的 artifact 中 `retrieved_document_ids` **80/80 全空**，但 `retrieved_references` 有 **27/80 非空**；对应 metrics `retrieval_precision = 0.0000, retrieval_recall = 0.0000`。
- **根因**：`src/eval/e2e.py` 的 `_retrieved_document_ids_from_audits()` 读的是 audit `result_summary['document_ids']`；但 fixes run 的 trace 里 search_knowledge 的 `result_summary` key 是 **`('kind','refs')`**，没有 `document_ids` → 取到空列表。
- **对照证据**：

| run | `result_summary` keys | doc_ids 非空 | precision | recall |
|---|---|---|---:|---:|
| tracefix | `document_ids, kind, ref_count` | 23/80 | 0.4514 | 0.8750 |
| tracefix2 | 同上 | 30/80 | 0.4236 | 0.8333 |
| fixes | **`kind, refs`** | **0/80** | **0.0000** | **0.0000** |
| tracefix3 | `document_ids, kind, ref_count` | 32/80 | 0.4236 | 0.8333 |

- **验证**：tracefix3 上复算 artifact 的 doc_ids 与 tool 里 `document_ids` 的并集 **80/80 完全一致**，说明该字段确实来自 audit 投影。
- **结论**：fixes run 的 0.0000/0.0000 **是测量 bug，不是模型没检索**。任何引用"retrieval precision 0"作为模型结论的说法都无效。

### 5.2 【已证实 bug】`knowledge_refs` 被截断到 1 条

- search_knowledge 的 `result_summary.ref_count` **恒为 3（32/32）**，但 `knowledge_refs` 长度分布是 `{1: 31, 2: 1, 0: 48}`。
- 即 **31/32 次检索只留下 1 条引用**，trace 侧合计 33 条引用 vs 工具侧宣称 96 条（32×3）。
- 代码依据：`src/agents/knowledge.py` 用模型输出的 `knowledge_refs` 去反查 `available_refs`，只有被模型回引的 chunk 才保留；若一条都没匹配上，退化为 `[available_refs[0]]`（**只留 top-1**）。
- **影响**：`retrieved_references` 的**召回天花板被人为压低**。以 NET-015 为例，tool 返回 `[doc-network-vpn, doc-access-cloud, doc-network-rdp]` 三个 doc_id，artifact `retrieved_document_ids` 是 3 个，但 `retrieved_references` 只有 1 条（doc-network-vpn#4）。
- **计数不一致**：artifact 里 `len(retrieved_document_ids) != len(retrieved_references)` 的 case 有 **30/80**；`set(doc_ids) == set(refs.doc_ids)` 仅 **49/80**。

### 5.3 【口径缺陷】`actual_tools` 把"仅审批未执行"的工具算作已调用

- `src/eval/e2e.py` 在 `actual_tools` 末尾追加 `approval.tool_name`（仅当该 operation 未被审计到）。
- 实测 5 例不一致：`PRM-004`（trace 0 次 / artifact 1 次）、`RFD-003`、`RFD-005`、`RFD-006`（trace 1 次 / artifact 2 次）、`RFD-015`（trace 0 / artifact 1）。
- 合计：trace 99 次 vs artifact 104 次。**Tool Jaccard 用哪个口径结果不同**（按现口径 0.7500；剔除仅审批项后约 0.7312）。
- 副作用：`RFD-015` 期望 `expected_tools = []`，artifact 却因审批未执行记成 `request_access`，被误判为工具错误。

### 5.4 【指标缺陷】trajectory_efficiency 对"啥也没做"给满分

- `compute_trajectory_efficiency`：`min(1.0, min_business_steps / max(1, actual_business_steps))`。
- tracefix3 中 **11 例 `actual_business_steps == 0`**，分母被 clamp 成 1，全部得 **1.0**。
- 结果：80 例平均 0.9646（与 artifact 报告一致），但其中 74 例 = 1.0，只有 6 例 <1（NET-011/ACC-007/ACC-010/ACC-011/PRM-012 = 0.5，NET-015 = 0.667）。
- **该维度几乎没有区分度**，且"零工具 resolved"被奖励。

### 5.5 【schema 漂移】trace 文件字段集不统一

| 文件 | record keys | 顶层计数 |
|---|---|---|
| tracefix | 12（含 approvals/operations/knowledge_refs） | 有 |
| tracefix3 | 12 | 有 |
| fixes | **9（无 approvals/operations/knowledge_refs）** | **无 trace_event_count/tool_call_count** |

→ 跨 run 做"审批/知识引用"维度统计时，fixes run **结构性缺席**，必须显式标注 N/A 而不是 0。

### 5.6 【完全观测不到的维度】

| 维度 | 证据 |
|---|---|
| **审批决策结果**（approve/reject） | 三次 run 共 9 条 approval **全部 status=pending**；无 approved/rejected 记录 |
| **args_hash / operation 绑定校验** | trace 的 approval schema 只有 7 字段，**无 args_hash** |
| **idempotency_key / 重复执行** | operations 无该字段；无任何重放证据 |
| **模型层信息** | 无 token 数、prompt 版本（只在 run 顶层）、模型原始输出、repair 次数（只在 artifact `failure_counts` 字符串里） |
| **Policy 判定理由** | `events` 只有 `evaluate_risk` 摘要，`reason_code` 仅在 artifact `policy_decisions` 可见，trace 无 |
| **检索查询文本** | search_knowledge 的 `args_summary` 只有 `(kind, top_k)`，**无 query 字符串** → 无法评估检索 query 质量 |
| **参考回答质量 / faithfulness** | trace 无 final_response（被脱敏），只能看 status |
| **端到端排队与模型延迟分解** | 只有 event timestamp 差分；tool latency（p50 1ms）完全不含模型耗时 |
| **8b 模型的逐 case trace** | `qwen3-8b*` 目录下 **无任何 `*-traces.json` 也无 `*-failures.csv`**，只有 `.local.json` 汇总 → 8b 无法做 trace 级取证 |
| **MAX_STEPS 超限路径** | 实测 step_count max=11，从未触发 15 上限，该分支在 80 条上零样本 |

### 5.7 【数据集标签缺陷】影响 handoff 维度

1. **RFD-008 / RFD-010 的 expected_handoffs 只有 `supervisor->finalize`，但实际图必然走 `supervisor->action->reject_request`** → 任何实际路径都会与标签不一致，**结构上不可达**。RFD-009 类似（期望 `supervisor->knowledge->knowledge->finalize`）。
2. **`reject_request` 不在标注词表里**（golden 中 0 次出现），`derive_actual_handoffs` 把它映射成 `finalizer`，导致 9 条 rejected case 的路径被压平成 `action_propose->finalize`，与期望的 `action_propose->supervisor->supervisor->finalize` 天然冲突。
3. **量化后果**：12 条 expected=rejected 的 case，handoff LCS = **0.45**；其余 68 条 = **0.7712**。也就是说整体 0.7354 里有很大一块是**标注口径问题而非模型问题**。
4. 数据分布失衡：`relevant_document_ids` 只有 **24/80** 有值（且全部长度为 1）；`expected_tools` 59 条只有 1 个工具、8 条为 0；`escalated` 仅 1 条；`medium` 风险仅 2 条；`multi_step / fallback / timeout / failure / escalated / gateway / audit / recovery / idempotency / concurrency` 这些 tag **各只有 1–2 条**。

### 5.8 【元数据缺陷】

- tracefix3 artifact 的 `git_sha` 值带尾随换行：长度为 **42**（应为 40），实际值 `'aef79142eeca74acdc073ddfd273ffc0b286de32\n'` → **按 sha 做 join/校验会失配**。
- 三次 run 的 `worktree_clean` 全为 `false`，即所有结果都来自脏工作区，**H-20 要求的可追溯性打折**。

---

## 6. 建议：可从 trace 观测的评测维度（10 个）

| # | 维度名 | 数据来源 | 现状可测性 |
|---|---|---|---|
| 1 | `intent_accuracy` | `ticket.intent` vs golden `expected_intent` | ✅ 完整（trace 与 artifact 80/80 一致） |
| 2 | `trajectory_path_fidelity` | `graph_nodes` → handoff 序列，LCS/exact | ⚠️ 可测但标签有缺陷（§5.7） |
| 3 | `tool_selection_fidelity` | `tool_calls[].tool_name` 集合 Jaccard / missing / extra | ✅ 需先锁定口径（§5.3） |
| 4 | `retrieval_hit_rate` | `tool_calls.result_summary.document_ids` ∩ `relevant_document_ids` | ⚠️ 依赖 result_summary schema（§5.1） |
| 5 | `retrieval_grounding_ratio` | `len(knowledge_refs) / result_summary.ref_count` | ✅ 可算（当前 33/96 = 34%） |
| 6 | `policy_decision_correctness` | `events(actor=policy)` + artifact `policy_decisions` | ⚠️ 只有 31/80 有观测（coverage 0.8276） |
| 7 | `approval_gate_reached` | `approvals` 非空 + `__interrupt__` 节点 + `status=waiting_approval` | ✅ 可判"是否触发"；❌ 不可判"审批结果" |
| 8 | `terminal_outcome_match` | `ticket.status` vs `expected_outcome` | ✅ 完整（outcome_match 73/80） |
| 9 | `step_efficiency_overrun` | `step_count` / `graph_nodes` 长度 vs `min_business_steps` | ✅ 但需重定义（现指标失效，§5.4） |
| 10 | `worker_failure_class` | `failure_code` / `error_code` / last event `worker_failure:*` | ✅ 可判 4 例；只覆盖 ModelConfigurationError 一类 |

**可选补充（若有 trace 扩展）**：`tool_execution_reliability`（outcome=failed_final/timeout + retry_count）、`audit_completeness`（每次工具尝试都有审计 + operation_id 绑定）、`step_latency_decomposition`（event timestamp 差分，当前完全可用但没人算）。

---

## 7. 对重做 20 条评测集的具体建议

1. **分层抽样而非按比例**：现有 80 条中 `escalated` 仅 1 条、`medium` 仅 2 条。20 条里应硬性保证 escalated ≥1、medium ≥2、每类 intent ≥3。
2. **剔除或重复 flaky 的 8 条 refund case**：RFD-001/002/004/005/006/010/013/016 三 run bucket 均变动过，单跑结论不可信。
3. **先修 3 个 bug 再出数**：`_retrieved_document_ids_from_audits`（§5.1）、`knowledge_refs` 截断（§5.2）、`actual_tools` 审批项（§5.3）。否则 retrieval 与 tool 两个维度不可信。
4. **重标 rejected case 的 handoff**：把 `reject_request` 纳入词表，或把期望写成 `supervisor->action_propose->finalize`（§5.7），否则该维度恒定扣分。
5. **bucket 词表要扩**：`other` 占 19%，需补充 D1–D3、D11–D12 这些高频类型，并规定 precedence（避免 `high-risk-finalized-without-action` 抢在 handoff 判定之前，见 §3.2 D5 的 3 例其实工具集全对）。
6. **每 case 至少 3 次重复取多数**：这是由 30/80 的 flaky 率直接推出的成本下限。
7. **trace 导出需补齐字段**：approval 的 `args_hash`/`status` 终值、search_knowledge 的 query 文本、result_summary 稳定 schema（`document_ids` 必须恒在）、8b run 也要产出 `*-traces.json`。
