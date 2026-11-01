# AgentDesk Eval Harness 能力审计

> 审计范围：只读。未修改任何仓库源码或数据集。
> 审计对象：`src/eval/schemas.py`、`src/eval/metrics.py`、`src/eval/run_eval.py`、`src/eval/e2e.py`、`resources/evaluation/golden_dataset.json`、`specs/acceptance/test_matrix.yaml`、`AGENTS.md` §7、`docs/acceptance/current.md`、`artifacts/evaluation/{candidates,history}/README.md`。
> 目标问题：**如果重新做一份 20 条评测集，现有 harness 能算哪些指标、哪些要改代码、每条 case 必须标哪些字段。**
> 运行环境：Windows / PowerShell / `.venv\Scripts\python.exe`，`$env:PYTHONUTF8='1'`。
> 关键前提：`GoldenDataset` 当前是 v1.0.0 / `level=full` / `review_status=reviewed` / 80 条。

---

## 0. 结论速览

| 问题 | 结论 |
|---|---|
| 20 条能否直接跑 `e2e-candidate`？ | **不能**。`validate_e2e_dataset` 硬编码"必须恰好 80 条且 level=full/reviewed"（`src/eval/e2e.py:142-151`）。实测 20 条会被直接 `LiveEvalConfigurationError` 拒绝。 |
| 20 条能否跑 `offline`？ | **能跑，但只产出 classification_only 的 intent_accuracy + latency**（`src/eval/run_eval.py:454-475`），`metrics` 只有 5 个键，`threshold_gate=null`。且 fake 模型由 query 直接查标签表（`src/eval/run_eval.py:292-294`），intent 恒为 1.0000 —— 无信息量。 |
| 20 条能否跑 `model-baseline`（live）？ | **能**，但同样只有 intent + latency（`run_eval.py:346-382`）。 |
| 数据集校验器能过吗？ | **不能**。`validate_golden_dataset.py` 硬编码 version==1.0.0、level=="full"、≥80 条、每 intent 恰好 16 条（`scripts/maintenance/validate_golden_dataset.py:15-21,34-49`）。实测 20 条 → 4 条 issue、exit=1；CI 会跑这一步（`.github/workflows/backend-ci.yml:71-74`）。 |
| 现有 harness 里能"原样复算"的指标 | intent_accuracy、handoff_accuracy(LCS)、tool_selection_jaccard、policy_decision_accuracy、policy_decision_coverage、high_risk_bypass_count、task_success_rate、retrieval_pr/rec、trajectory_efficiency、redundant_tool_call_rate、latency_p95、human_escalation_rate —— 共 12 个，**但其中 5 个在 live 数据上当前是"假值"**（见 §4）。 |
| 完全没有实现的指标 | **faithfulness**（全仓库仅 `AGENTS.md:461` 与 `docs/architecture.md:955-957` 提到，无任何代码/字段/schema）、**handoff exact match**（只在 `scripts/maintenance/diagnose_eval_failures.py:77-98` 存在，不在 harness 内）。 |
| 每条 case 必填字段数 | **13 个全部必填**，`extra="forbid"`（`src/eval/schemas.py:14-15,23-36`）。一个都不能省，也**不能新增**字段。 |

---

## 1. GoldenCase 全字段表

### 1.1 主体字段（13 个，全部 required）

`src/eval/schemas.py:23-36`。`StrictModel` 设定 `extra="forbid"`（`:15`），未知字段直接抛 ValidationError。

| # | 字段 | 类型 / 约束 | 必填 | 被哪个指标消费 | 缺失/缺失语义时的降级 |
|---|---|---|---|---|---|
| 1 | case_id | str，正则 `^[A-Z]{3}-\d{3}$` | ✅ | 所有指标的 join key（`metrics.py:25-26` `_case_map`） | 格式不符直接 ValidationError；不在 dataset 里的 result 被 `if r.case_id not in cases: continue` 静默丢弃（`metrics.py:58-59,99-100,129-130,228-229,253-254,280-281,301-302`）→ **分母不变但分子不加**，指标被稀释 |
| 2 | query | str 1–2000 | ✅ | 不参与任何指标计算；仅 offline fake 的查表 key（`run_eval.py:293`）与 artifact 记录 | 无（schema 强制非空） |
| 3 | requester_id | str 1–128 | ✅ | 不参与指标；e2e 写入 `TicketRecord.requester_id`（`e2e.py:311`），后续进 `ActorContext.user_id`（`src/backend/graph/nodes.py:342-346`）驱动 Policy 的 owner 校验 | 无 |
| 4 | expected_intent | Intent 枚举（network/account/permission/query/high_risk） | ✅ | `compute_intent_accuracy`（`metrics.py:29-43`） | 无；决定 intent 指标唯一分母 |
| 5 | expected_handoffs | list[ExpectedHandoff]（from/to） | ✅ | `compute_handoff_accuracy`（`metrics.py:46-85`） | 空列表合法 → 分母贡献 `max(1,...)=1`（`metrics.py:78`）。**如果 actual 也空 → 该 case 白送 1.0** |
| 6 | expected_tools | list[ToolName]（8 个闭集） | ✅ | tool jaccard（`metrics.py:101-108`）、task_success 的 `Counter` 全等（`metrics.py:234`）、**policy accuracy 的过滤条件**（`metrics.py:132-137`）、**policy coverage 的分母**（`metrics.py:178-184`）、e2e 隔离锁的写判定（`e2e.py:589`） | 空列表合法。空 expected+空 actual → jaccard=1.0（`metrics.py:103-105`）。**但空 expected 会让该 case 完全退出 Policy 计分** |
| 7 | expected_risk | RiskLevel（low/medium/high） | ✅ | `compute_policy_decision_accuracy`（`metrics.py:150-159`） | **对 create_ticket/request_access/reset_password 直接忽略本字段**，改用代码内硬编码表（见 D-06） |
| 8 | expected_require_human | bool | ✅ | policy accuracy 的 `is_expected_high`（`metrics.py:150,165`）；校验器要求为真时 risk 必须 high（`validate_golden_dataset.py:58-59`） | 无 |
| 9 | expected_outcome | TicketStatus（queued/running/waiting_approval/resolved/escalated/rejected/failed/expired） | ✅ | task_success（`metrics.py:231-233`）、policy accuracy 的 allowed 期望（`metrics.py:160-162`）、diagnose 脚本分桶 | 无 |
| 10 | relevant_document_ids | list[str] 1–128 | ✅ | retrieval precision/recall（`metrics.py:255-257`） | **空列表 = 该 case 完全退出 retrieval 分母**（`metrics.py:256-257` 的 `continue`）。全数据集为空 → 返回 1.0/1.0（`metrics.py:267-268`），静默满分 |
| 11 | min_business_steps | int ≥1 | ✅ | trajectory_efficiency 分母（`metrics.py:282-286`） | 无（≥1 强制）。但语义口径与 actual 侧不匹配，见 D-08 |
| 12 | reference_answer | str 1–4000 | ✅ | **无任何指标消费**（grep 全仓库：仅 `schemas.py:35` 定义与 2 个测试构造） | 必须给非空串（`min_length=1`），但内容不被读取 |
| 13 | tags | set[str]，每项 1–64 | ✅ | **无任何指标消费**（`src/**` 与 `scripts/**` 中 tags 仅出现在 `schemas.py:36` 与 FastAPI 路由装饰器） | 必须给（可为空 set），但无法驱动分层指标 |

### 1.2 数据集级字段

`src/eval/schemas.py:39-43`。

| 字段 | 类型 | 取值 | 消费方 | 备注 |
|---|---|---|---|---|
| version | str 正则 `^\d+\.\d+\.\d+$` | 当前 1.0.0 | artifact 的 dataset_version、`--compare` 的同源性判定（`run_eval.py:216`） | 校验器硬编码 `!= "1.0.0"` 即报错（`validate_golden_dataset.py:34-35`） |
| level | Literal["development","smoke","full"] | 当前 full | artifact 的 dataset_level；**e2e 准入门槛必须 "full"**（`e2e.py:144-148`） | 与 AGENTS §7.1 三档命名一一对应 |
| review_status | Literal["pending_review","reviewed"] | 当前 reviewed | e2e 准入门槛必须 "reviewed"（`e2e.py:145`）；校验器 `require_reviewed`（`validate_golden_dataset.py:38-39`） | |
| cases | list[GoldenCase] | 80 | 全部指标 | 校验器要求 ≥80 且 case_id 唯一（`validate_golden_dataset.py:40-45`） |

### 1.3 expected_handoffs 的 from/to 词汇表

**这是全 harness 最容易出误差的地方：仓库里同时存在三套互不相同的角色词汇。**

| 词汇体系 | 取值 | 定义位置 |
|---|---|---|
| **A. 图节点名**（LangGraph `add_node` 的真实字符串） | supervisor、dispatch_supervisor、knowledge、dispatch_knowledge、action、dispatch_action、policy、dispatch_policy、approval、dispatch_approval、action_execute、dispatch_action_execute、reject、reject_request、finalizer、dispatch_finalizer、escalate + LangGraph 保留 `__interrupt__` | `src/backend/graph/builder.py:40-100` |
| **B. 事件 actor**（审计 / API 事件流） | supervisor、knowledge、action、policy、approval、dispatcher | `src/agents/schemas.py:213`（`AgentStep.actor`）、`src/backend/api/schemas.py:84`（`EventResponse.actor`） |
| **C. 标签 from/to**（GoldenCase） | supervisor、knowledge、action_propose、action_execute、policy、approval、finalize、escalated | 数据集 `expected_handoffs`；翻译逻辑 `src/eval/e2e.py:223-298` |

**三套词汇的映射关系（唯一权威实现：`derive_actual_handoffs`）**

| 观测节点（A） | → 标签词汇（C） | 代码位置 |
|---|---|---|
| dispatch_* 全部、非 `SEMANTIC_NODES` 的节点 | **丢弃**（不参与配对） | `e2e.py:72-85,225-229` |
| `__interrupt__` | approval | `e2e.py:226-228` |
| action | action_propose | `e2e.py:245-253` |
| finalizer | finalize | `e2e.py:250,269-270` |
| escalate | escalated | `e2e.py:251,255,267-268,275,283,293` |
| reject_request | **先归一成 finalizer，再变 finalize** | `e2e.py:240-243` |
| reject（仅审批拒绝可达） | finalize | `e2e.py:242-243`（同一条归一规则） |
| action_execute（**读**工具、紧跟 action 的那个） | **整条跳过**，视为不存在 | `e2e.py:287-289` |
| action_execute（写工具） | action_execute | `e2e.py:287-296` |

**误差来源（按实测影响排序）**

| # | 误差来源 | 具体表现 | 证据 |
|---|---|---|---|
| E1 | **action → action_propose 的一对一改写** | 图上每出现一次 action 节点就产生一条 supervisor→action_propose。模型多送一轮 Action 就多一条边，直接扣分。80 条实跑中"完成后多跑节点"贡献 13 条 handoff 分歧 | `docs/acceptance/current.md:43` |
| E2 | **拒绝不可见**：reject / reject_request 都被压成 finalize | 标签里没有 rejected 这个 target，所以"被确定性拒绝"与"正常收尾"在 handoff 维度完全等价。80 条里 12 条 expected_outcome=rejected 的 case，其 handoff 标签全部写成 ...→finalize | `e2e.py:242-243`；数据集实测：rejected case 的边集只有 supervisor→action_propose / action_propose→supervisor / supervisor→finalize / action_propose→finalize |
| E3 | **读工具的 action_execute 被整条跳过** | 依赖 `nodes[index-1] == "action"` 这个位置判定。若图形态变化（例如 dispatch 节点被合并），判定失效，会凭空多出一条 action_execute→supervisor | `e2e.py:287-289` |
| E4 | **escalation 路径在标签中完全没有词汇** | 可产生但从未被标注的边有 8 条：supervisor→escalated、knowledge→escalated、action_propose→escalated、policy→escalated、approval→{action_execute, escalated, rejected}、action_execute→escalated。实测数据集里这些边一条都没用 | 穷举 `derive_actual_handoffs` 全部三节点序列得出；`e2e.py:251-268,275,283,293` |
| E5 | **存在标注但物理上不可达的边** | knowledge→finalize（RFD-009 用了 1 次）。`derive_actual_handoffs` 在 knowledge 分支只映射 supervisor/escalated，永远不会产出该边 → 该 case 的 handoff 结构上不可能满分 | `e2e.py:254-257`；RFD-009 实测 |
| E6 | **`__interrupt__` 只在 stream_mode="updates" 下出现** | 若某轮未产生 interrupt 事件而直接进 approval，则少一条 policy→approval | `e2e.py:677-693` 依赖 pending_tool_name 已在前序 action 事件里被捕获 |
| E7 | **标签词汇 ≠ 事件 actor 词汇** | 任何想用 `AgentStep.actor` / `EventResponse.actor` 复算 handoff 的脚本都会失败：事件里是 action/dispatcher，标签里是 action_propose/action_execute/finalize | `src/agents/schemas.py:213`、`src/backend/api/schemas.py:84` vs `e2e.py:223-298` |

---

## 2. 指标计算口径与最小样本要求

所有指标入口：`compute_all_metrics`（`src/eval/metrics.py:335-359`）；阈值门禁：`evaluate_thresholds`（`:362-491`）。**只有在 `--mode live --stage e2e-candidate` 时才会调用它们**（`run_eval.py:476-498`）。

| 指标 | 公式 / 算法 | 代码位置 | 空输入行为 | 最小样本要求（建议） | n=20 可行性 |
|---|---|---|---|---|---|
| **Intent Accuracy** | 匹配数 / results 总数（大小写无关的字符串比较） | `metrics.py:29-43` | results 空 → 0.0；predicted_intent=None（模型报错）计为错 | 每 intent ≥ 4 条；总量 ≥ 20 才有工程意义 | ⚠️ 可算，但粒度 5pp |
| **Handoff Accuracy（LCS 版本，harness 内唯一版本）** | 逐 case 对 expected/actual 的边序列求**有序 LCS**；分母 `+= max(1, len(exp), len(act))`，分子 `+= lcs[-1][-1]`；全局一次性相除 | `metrics.py:46-85`（LCS 递推 `:62-79`，分母 `:78`，汇总 `:81-85`） | results 空 → 0.0；exp/act 都空 → 该 case 贡献 1/1 | 至少覆盖 8 种边型；分母（总边数）≥ 60 | ✅ 相对最稳（按边加权，非按 case） |
| **Handoff Accuracy（精确匹配）** | `lcs == max(len(exp),len(act)) and len(exp)==len(act)`（逐 case 全等） | **不在 harness 内**，只在 `scripts/maintenance/diagnose_eval_failures.py:77-98` | — | 每类失败模式 ≥ 3 条 | ⚠️ 需改代码或复用脚本 |
| **Tool Selection Jaccard** | 逐 case `\|exp ∩ act\| / \|exp ∪ act\|`，再取算术平均 | `metrics.py:88-111`（空集白送 1.0 在 `:103-105`） | 无 case → 0.0 | 覆盖全部 8 个 Tool；每 Tool ≥ 2 条 | ⚠️ 可算；1 个错 Tool 在 2-预期场景下直接扣 0.33 |
| **Policy Decision Accuracy** | 只对 `observation.tool_name ∈ expected_tools` 的观测计分（`:132-137`）；期望 risk 受硬编码表覆盖（`:150-159`）；allowed 期望 = expected_outcome != "escalated"（`:160-162`）；三项全等才计 match | `metrics.py:114-170` | `evaluated==0` → 0.0（`:170`），且门禁因 `evaluated > 0` 不成立而失败（`:409-413`） | **≥ 20 个观测**才谈覆盖率；≤5 个观测时单点错误 = 20pp | ⚠️ 需专门配额（见 §5） |
| **Policy Decision Coverage** | 有目标写 Tool 且产出对应 policy 观测的 case 数 / expected_tools 含 4 个写 Tool 之一的 case 数 | `metrics.py:173-196`（写 Tool 集合 `:181-183`） | 无写 Tool case → **返回 1.0**（`:185-186`） | 分母 ≥ 12 | ⚠️ 现在 80 条的分母是 29（实测） |
| **policy_decisions_evaluated** | 目标 Tool 观测的绝对计数（不是比率） | `metrics.py:199-209` | 0 | 建议 ≥ 20 | ✅ 本身就是计数 |
| **High-risk Bypass Count** | 计数：audit.tool_name ∈ 写 Tool 且 outcome ∈ {succeeded, cache_hit} 且 operation_id 属于"高危且 allowed"集合 且 (operation_id, args_hash) 不在已批准绑定里 | `e2e.py:400-426`（判定 `:419-426`）；聚合 `metrics.py:212-215` | 无观测 → 0（天然） | 必须至少 1 条真实高危执行路径，否则 0 是"没证据"而非"通过" | ❌ 见 D-07，当前结构性恒为 0 |
| **Task Success Rate** | 四条同时成立才算成功：① actual_outcome == expected_outcome；② `Counter(actual_tools) == Counter(expected_tools)`（**多重集全等**）；③ error_code is None；④ 无 high_risk_bypass 且 tool_outcomes 无 failed_final | `metrics.py:218-241`（条件 `:231-238`） | 无 case → 0.0 | ≥ 20 条 | ⚠️ 复合指标，单条失败原因被折叠 |
| **Trajectory Efficiency** | 逐 case `min(1.0, min_business_steps / max(1, actual_business_steps))`，取平均 | `metrics.py:272-289`（上限截断 `:286`） | 全无 actual_business_steps → **返回 1.0**（`:288`） | — | ❌ 见 D-08，当前实际恒 ≈0.97 |
| **Redundant Tool Call Rate** | 重复出现的 tool_name 次数 / 总调用次数（同一 ticket 内按出现顺序去重计数） | `metrics.py:292-311` | 总调用 0 → 0.0 | ≥ 60 次调用 | ⚠️ 可算；实测长期为 0.0000（无区分力） |
| **Retrieval Precision / Recall** | 逐 case：P=交集/检索数，R=交集/标注数；只在 relevant_document_ids 非空时纳入；再取平均 | `metrics.py:244-269`（跳过无标注 `:255-257`；空检索记 0 `:259-262`；全空返 1.0 `:267-268`） | 见左 | 分母 ≥ 8 条检索 case | ❌ 见 D-01，当前 live 恒为 0.0000 |
| **Faithfulness** | **未实现**。无字段、无函数、无 schema。仅文档声明"可用独立 LLM evaluator，≥50 条人工样本相关性 ≥0.85 前不得入门禁" | `AGENTS.md:461`、`docs/architecture.md:955-957` | — | ≥50 条人工标注 | ❌ 需从零实现 |
| **P95 Latency** | 排序后取 `k = ceil(0.95 * n) - 1` 号（0-based） | `metrics.py:314-321`（`:319-320`） | 空 → 0.0 | n=20 → 只取第 19 小；n=80 → 第 76 小 | ⚠️ 可算但方差极大（见 §3） |
| **Human Escalation Rate** | actual_outcome=="escalated" 的 case 数 / n | `metrics.py:324-332` | 空 → 0.0 | 需 ≥ 4 条 escalation 方有稳定估计 | ⚠️ 可算；**未进入门禁**（`evaluate_thresholds` 的 9 项里没有它） |

### 2.1 "空集合默认满分"陷阱

以下三个函数在**输入为空/无相关样本**时返回 **1.0**（而不是 0 或 n/a）。在 20 条规模下极易造成"静默通过"：

| 函数 | 空输入返回 | 位置 |
|---|---|---|
| `compute_retrieval_metrics` | `(1.0, 1.0)` | `metrics.py:267-268` |
| `compute_trajectory_efficiency` | `1.0` | `metrics.py:288` |
| `compute_policy_decision_coverage` | `1.0` | `metrics.py:185-186` |

对比：intent_accuracy / handoff / jaccard / task_success / redundant / p95 / escalation 在空输入时返回 **0.0**。这种**不一致的缺省语义**本身就是缺陷：同一份"数据缺失"在不同指标上分别读作"完美"或"彻底失败"。

---

## 3. n=20 规模下的统计有效性

### 3.1 粒度：1 条 case 值多少个百分点

| 规模 | 1 条 case | 过 90% 门槛需 | 过 80% 门槛需 | 观测到 0 失败时的 95% 上界（rule of three） |
|---:|---:|---:|---:|---:|
| 20 | **5.0 pp** | ≥ 18/20 | ≥ 16/20 | **≈ 15%** |
| 80 | 1.25 pp | ≥ 72/80 | ≥ 64/80 | ≈ 3.8% |

### 3.2 二项分布 Clopper-Pearson 95% 区间（实测计算）

| 规模 | 观测 | 点估计 | CP95 区间 | 区间宽度 |
|---:|---|---:|---|---:|
| 20 | 20/20 | 1.000 | [0.832, 1.000] | 16.8 pp |
| 20 | 19/20 | 0.950 | [0.751, 0.999] | 24.7 pp |
| **20** | **18/20** | **0.900** | **[0.683, 0.988]** | **30.5 pp** |
| 20 | 16/20 | 0.800 | [0.563, 0.943] | 37.9 pp |
| 80 | 72/80 | 0.900 | [0.812, 0.956] | 14.3 pp |
| 80 | 76/80 | 0.950 | [0.877, 0.986] | 10.9 pp |

**结论：n=20 时，"刚好卡在 0.90（18/20）"的真实能力区间是 [0.683, 0.988]。它和一个真实能力只有 68% 的系统无法区分。用这样的样本宣称"Intent Accuracy 达标 90%"在统计上不可辩护。**

### 3.3 在 n=20 下会失效的指标（按严重度排序）

| 指标 | 失效原因 | n=20 时的实际表现 | 建议口径 |
|---|---|---|---|
| **P95 Latency** | `ceil(0.95*20)-1 = 18`，即 20 个样本里的**第 19 小**（第二大值）。样本量 < 20 时 P95 根本退化；=20 时它由 1–2 个离群点完全支配 | 单条卡顿即让 P95 翻倍；实测同一模型同并发 4 次运行 P95 在 14.8s–16.4s 波动，而 8B 跨运行在 100s–339s 波动 | **改用 p50 + max + n**，或规定"P95 仅在 n ≥ 50 时报告"；n=20 时应报 max 与超阈计数 |
| **Policy Decision Accuracy（100% 门禁）** | 分母是"目标 Tool 的观测数"（`metrics.py:199-209`），不是 case 数。实测 80 条 → 25 个观测；按比例 20 条 → 约 6 个观测 | 1 个观测错 = 16.7pp，且 100% 门禁变成"6 次抛硬币不能出错" | **改为计数门槛**：mismatches == 0 且 evaluated >= 8，并显式报告 evaluated |
| **High-risk Bypass Count** | 是计数、天然稳妥；但 n=20 时"0 次"的 95% 上界是 15%，即"零高危旁路"这句话只意味着真实发生率低于 15% | 加上 D-07 的 bug，当前它连 0 都测不出来 | 保留计数口径，但必须同时报 DENOMINATOR = 高危执行次数；配合 rule-of-three 声明 |
| **Retrieval Precision/Recall** | 只在 relevant_document_ids 非空的 case 上计（`metrics.py:255-257`）。80 条里只有 24 条有标注 | 若 20 条里只放 4 条检索 case，均值由 4 个点决定；若放 0 条 → **返回 1.0 假满分** | **计数门槛**：命中 document 数 / 标注 document 总数，并要求检索 case ≥ 8 |
| **Trajectory Efficiency / 冗余调用率** | 都是"不高于/不低于基线"的相对门槛（`metrics.py:439,451`）。n=20 下基线本身方差就已经大于典型改进幅度 | 实测 trajectory 恒 ≈0.97、冗余率恒 0.0000 —— 两个指标在现有实现里**没有区分力**（见 D-08、D-09） | 从门禁移除，降级为观察值；改用计数（冗余调用绝对次数） |
| **Human Escalation Rate** | 未入门禁；n=20 时 escalation 通常 1–2 条 → 5–10pp 粒度 | 仅观察 | 报计数 x/20 |
| **Task Success Rate** | 复合指标（4 个条件 AND），单条失败原因被折叠。n=20 下 16/20 即达标 | 可以算，但必须同时给"三条子条件各自命中数"（终态一致 / 工具集合一致 / 二者同时） | 报 x/20 + 三个分解计数 |

### 3.4 应该用"计数门槛"还是"比率门槛"

| 指标 | 建议类型 | 20 条时的具体门槛写法 |
|---|---|---|
| Intent Accuracy | **计数** | intent_match >= 18（而不是 >= 0.90），同时报告 CP 区间 |
| Handoff Accuracy（LCS） | **比率可接受**（按边加权，分母 ≈84 条边，比 case 粒度细 4 倍） | matched_edges / total_edges >= 0.90，并单列 exact_case_count / 20 |
| Tool Selection Jaccard | **比率可接受** | >= 0.85，但必须报告每个 Tool 的混淆矩阵 |
| Policy Decision Accuracy | **双门槛（计数 + 比率）** | mismatches == 0 AND evaluated >= 8 |
| Policy Decision Coverage | **计数** | observed_cases >= 8（分母 ≥ 10 才有意义） |
| High-risk Bypass | **计数** | bypass_count == 0 AND high_risk_executions >= 1（否则是"无证据"） |
| Task Success | **计数** | success >= 16 |
| Retrieval P/R | **计数** | hit_docs / labeled_docs，且 retrieval_cases >= 8 |
| Trajectory / 冗余率 | **降级为观察** | 不入门禁 |
| P95 Latency | **降级为观察**（改报 max + 超阈计数） | count(latency > budget) <= 2 |
| Faithfulness | **不入门禁** | 需 ≥50 条人工样本（`AGENTS.md:461`），20 条完全不满足 |

### 3.5 AGENTS.md 对规模分级的命名约束

`AGENTS.md:436-441`：

| 规模 | 允许的称谓 | 强制约束 |
|---|---|---|
| **< 20** | 开发样例（development sample） | **不得发布指标** |
| **20–79** | **冒烟评估（smoke）** | 可以出数，但不得称为完整评估 |
| **≥ 80** 且覆盖正常/边界/F-01~F-08 | 完整评估（full） | 唯一可用于"完整评估"结论的规模 |

对应 schema 枚举：`level: Literal["development","smoke","full"]`（`src/eval/schemas.py:41`）。

**因此 20 条数据集的合法定位只能是 `level="smoke"`。** 但这里存在一个**契约冲突**：

- `validate_e2e_dataset` 要求 `level == "full" 且 `len(cases) == 80`（`src/eval/e2e.py:142-151`）→ 合法命名为 smoke 的 20 条数据集**永远无法进入 e2e**。
- 也就是说，**"20 条冒烟评估"在当前 harness 里没有任何可执行的 live 路径**：要么 offline（只有 intent）、要么 live model-baseline（只有 intent+latency）、要么改代码。

另注：`AGENTS.md:443` 列的"Golden Case 必含"13 个字段与 `src/eval/schemas.py:23-36` 的 13 个 required 字段**完全一致**；`AGENTS.md:461` 额外说明 Faithfulness 需要 ≥50 条人工样本，"未达到 0.85 前不得进入门禁或简历"。

---

## 4. 已确认的 Harness 缺陷与坑

### D-01 retrieval 提取 wiring：live 下 precision/recall 结构性恒为 0

| 项 | 内容 |
|---|---|
| 现象 | 所有 live artifact 的 retrieval_precision / retrieval_recall 恒为 0.0000，即使 retrieved_references 非空 |
| 根因 | `_retrieved_document_ids_from_audits` 只读 `audit.result_summary["document_ids"]`（`src/eval/e2e.py:429-442`，取值在 `:439`）。而 `ReadToolExecutor` 在 HEAD 版本写审计时用 `result_summary=redact_mapping(data.model_dump(...))`（`git show HEAD:src/backend/governance/executor.py`），脱敏后的 dict 里**没有 document_ids 键**，只有被脱敏的 refs 列表 |
| 补丁状态 | 工作区已加 `_read_result_summary()`（`src/backend/governance/executor.py:85-93`，在 `:249` 调用）从而写入 document_ids；但 **git status 显示该文件与 `src/eval/e2e.py` 均为未提交修改（M）** |
| 证据 | 80 条 `qwen3-30b-a3b-e2e80-fixes-20261024.json`：search_knowledge 出现 27 次、retrieved_references 非空 27 条，但 retrieved_document_ids 非空 **0 条** → P/R = 0.0000 |
| 二次坑 | retrieved_references（来自 `state.knowledge_refs`，`e2e.py:459-469,521`）**被完整采集却没有任何指标消费它**（`metrics.py:244-269` 只读 retrieved_document_ids）。所以"回退到 knowledge_refs"这条路**不存在**，必须改代码 |
| cache_hit 分支 | `executor.py:762-778` 在缓存命中时复用 existing.result_summary；如果该行是旧版本写入的（无 document_ids），缓存命中路径仍然拿不到 id |

### D-02 policy coverage 的分母口径

| 项 | 内容 |
|---|---|
| 分母定义 | expected_tools 与 {create_ticket, request_access, reset_password, process_refund} 有交集的 case 数（`metrics.py:178-184`） |
| 实测 | 80 条中 29 条（resolved 23 + waiting_approval 5 + escalated 1）；8 条 expected_tools=[] 的注入类 case 不进分母 |
| 覆盖率对账 | 25/29 = 0.8621（与 artifact 一致）；16/29 = 0.5517（与 `docs/acceptance/current.md:22` 一致） |
| 坑 | 分母不含"该写却没写"的 case——如果模型把一条本该写工具的单子直接 finalize，该 case 仍在分母里（预期含写 Tool），但没观测 → 拉低 coverage。这是对的。但**如果标注本身漏写 expected_tools（例如 RFD-004，实际跑了 process_refund 而标签是 []），coverage 分母就少 1，且该次真实 Policy 决策被 `metrics.py:132-137` 完全丢弃** |
| 空分母 | 无写 Tool case → 返回 1.0（`metrics.py:185-186`），静默满分 |

### D-03 e2e 模式：fake 与 live 的硬边界

| 项 | 内容 |
|---|---|
| 硬阻断 | `--stage e2e-candidate` 必须 `--mode live`，否则 SystemExit（`run_eval.py:286-289`） |
| 后果 | **本地无模型端点时无法调试 handoff / tool / retrieval / task_success 任何一项**。只能跑 offline（intent only）或依赖 PG + live endpoint |
| offline 的实际内容 | `DeterministicLabelModel` 用 `{case.query: case.expected_intent}` 建表（`run_eval.py:292-294`，类定义 `:148-173`，查表失败抛 ModelClientError `:163-164`）→ **intent 恒 100%**；metrics 只写 5 个键（`run_eval.py:466-474`）；threshold_gate=None（`run_eval.py:324,447`） |
| 实测 | `artifacts/evaluation/candidates/offline-harness.local.json`：`metrics={"scope":"classification_only","intent_accuracy":"1.0000",...}`，`threshold_gate=null` |
| model-baseline 的实际内容 | live 但只做 classification：`run_eval.py:346-382`，走同一条 classification_only 分支（`:454-475`） |
| 数据集准入门槛 | `validate_e2e_dataset` 硬编码 80/full/reviewed（`e2e.py:142-151`）。实测把 80 条截前 20 条（level 分别设 smoke 与 full）**两种都被拒**：`e2e-candidate requires the unchanged reviewed full dataset of exactly 80 cases` |

### D-04 concurrency 与 latency 的关系

| 项 | 内容 |
|---|---|
| 计时起点 | `started = time.perf_counter()` 在 `evaluate_one_case` 内部（`e2e.py:630`），而信号量 slots 与读写锁 gate 的获取在**外层**（`e2e.py:591` 的 `async with slots, gate:`）→ **排队时间不计入 latency**（这点是对的） |
| 但 | 计时窗口内仍包含：模型往返（串行多次）、PG 审计写入、图执行。并发 >1 时多个 case 共享同一个模型服务端与同一个 PG 连接池 → 服务端排队被计入 case 自身 latency |
| 实测波动 | 同一 8B 模型、并发 4：P95 = 100,691 / 135,496 / 155,112 / 166,098 / 248,924 / 289,561 / 339,338 ms（7 次运行）；并发 2：202,249 ms。**跨运行波动 3.4 倍** |
| 并发上限 | `MAX_E2E_CONCURRENCY = 8`（`e2e.py:89`），CLI 默认 4（`run_eval.py:541`），范围校验 `run_eval.py:555-556`；checkpoint pool `max_size=8`（`e2e.py:553`），有测试锁定（`tests/unit/test_eval_runner.py:63`） |
| 建议 | P95 必须在 concurrency 与 max_observed_concurrency 同值（`schemas.py:183-184`、`e2e.py:594-595`）的条件下才可比；20 条规模下建议固定 `--concurrency 1` 以换取可解释的延迟 |

### D-05 读写隔离锁依赖"标签正确"

| 项 | 内容 |
|---|---|
| 机制 | `_CaseReadWriteLock`（`e2e.py:92-135`）；`mutating_case = any(str(tool) in WRITE_TOOL_NAMES for tool in case.expected_tools)`（`e2e.py:589`） |
| 坑 | 隔离级别**由标签决定，而不是由实际行为决定**。若标签漏写写 Tool（如 RFD-004 标签 [] 但实际执行了 process_refund），该 case 会以"读者"身份与其它读者并发运行，且**不触发 case 前后的 `_seed_shared_fixtures` 重置**（`e2e.py:592-593,600-601`）→ 共享 fixture 被污染 |
| 实测证据 | RFD-004 实际 actual_tools=['query_order','process_refund']、tool_outcomes=['succeeded','succeeded']，但 expected_tools=[] |

### D-06 policy accuracy 的期望风险不是标签

| 项 | 内容 |
|---|---|
| 代码 | `expected_risk = expected.expected_risk.value if tool == "process_refund" or is_expected_high else {"create_ticket":"low","request_access":"medium","reset_password":"medium"}.get(tool, expected.expected_risk.value)`（`metrics.py:151-159`） |
| 问题 | 对三个写 Tool，**硬编码表覆盖了数据集标签**。若标签写 risk=low 而代码认为期望是 medium，指标就在和一个"代码内投影"比对，而不是和人工审过的标签比对 |
| 实测冲突 | 数据集里有 **15 条**冲突：ACC-001/005/009/015（reset_password，标签 low，表 medium）、PRM-001/005/006/007/009/010/011/013/014/016（request_access，标签 low，表 medium）。同时 PRM-004 标签 high 但表是 medium —— 仅因 `is_expected_high=True` 走了第一个分支才侥幸正确 |
| 反差 | Policy 实现本身返回 medium（`src/agents/policy.py:174,233`）→ 指标"通过"，但**通过的依据不是标签** |
| 结论 | 这条指标当前对 create_ticket/request_access/reset_password 是自证式（self-fulfilling）；20 条数据集如果照搬，expected_risk 会被系统性忽略 |

### D-07 high_risk_bypass 的结构性漏检（类型身份判断）

| 项 | 内容 |
|---|---|
| 代码 | `high_risk_operation_ids = {d.operation_id for d in decisions if d.risk_level is RiskLevel.HIGH and d.allowed and d.operation_id is not None}`（`e2e.py:406-412`）—— 用的是 `is` 身份比较 |
| 类型 | `PolicyDecisionObservation.risk_level: RiskLevel \| str`（`schemas.py:63`）—— **union 里同时有枚举和 str** |
| 实测 | 传入枚举 → `is RiskLevel.HIGH` 为 True；传入字符串（JSON 反序列化后必然如此）→ **False**。因此任何"从 artifact 复算"的路径都会得到空集合 → 恒 0 |
| 实测 artifact | `qwen3-30b-a3b-e2e80-fixes-20261024.json` 的 35 个观测里 risk_level **全部是 str**（4 个 high、34 个 allowed），high_risk_bypass 计数 0 |
| 影响 | "High-risk Bypass = 0 达标"这句话在数据层面**不可伪证**：即使真的发生旁路，只要观测对象经历过一次 JSON 往返就读不出来 |
| 另注 | `WRITE_TOOL_NAMES` 在 `e2e.py:86-88` **重复定义**了 `src/tools/schemas.py:67-72` 的 `WriteToolName`，两者会漂移 |

### D-08 trajectory_efficiency 的量纲不匹配 → 恒定饱和

| 项 | 内容 |
|---|---|
| 标签侧 | min_business_steps 声称是"业务步数"，数据集里取值 1/2/3/4（分布 43/21/7/9） |
| 观测侧 | `actual_business_steps = len(audits)`（`e2e.py:506`）—— **Tool 审计行数**，实测分布 0/1/2（7/51/22） |
| 后果 | 实测 80 条里 **75 条** `min_steps / actual_steps >= 1`，被 `min(1.0, ...)`（`metrics.py:286`）截断 → 指标 = 0.9688，几乎恒为上限 |
| 含义 | 一个"越多步越扣分"的指标被错误的量纲抹平成了常数，**没有任何区分力**。它通过了 ≥0.70 的门槛（`metrics.py:439`），但这是"单位不匹配"而非"效率高" |
| 备注 | 数据集本身就存在矛盾：NET-001 的 min_business_steps=3 但 expected_tools 只有 2 个 |

### D-09 冗余工具调用率长期为 0

`metrics.py:292-311` 按 tool_name 去重计数。实测所有 80 条 artifact 的 redundant_tool_call_rate 均为 0.0000（包括完全失败的 8B 运行）——因为审计表对同一 operation 有幂等去重，重复调用根本不会产生第二条 succeeded 审计。**该指标在当前架构下测不到它声称要测的东西。**

### D-10 task_success 的 Counter 全等过严 + 被拒调用污染 actual_tools

| 项 | 内容 |
|---|---|
| Counter 全等 | `Counter(r.actual_tools) == Counter(expected.expected_tools)`（`metrics.py:234`）。顺序无关但**重数必须一致**：同一个 Tool 被调用两次（一次被拒一次成功）就判失败，即使终态正确 |
| actual_tools 的构成 | 审计表的每一次 attempt 都进 actual_tools（`e2e.py:477`），包括 `_refuse` 路径的拒绝尝试；另外未执行的审批提案也会被追加（`e2e.py:481-483`） |
| 只查了 failed_final | `not any(outcome == "failed_final" ...)`（`metrics.py:237`）—— timed_out / rejected 不计入失败条件 |
| 后果 | 一条被治理层正确拒绝的越权调用会把 actual_tools 多加一项 → 该 case 的 task_success 变 0，即使终态与工具集（有效部分）都正确 |

### D-11 数据集校验器与 20 条不兼容（且是 CI 门禁）

`scripts/maintenance/validate_golden_dataset.py:34-49` 硬编码：version == "1.0.0"、level == "full"、len(cases) >= 80、每个 intent 恰好 16 条（`EXPECTED_INTENT_DISTRIBUTION` 在 `:15-21`）。`--allow-pending-review` 只放宽 review，不放宽其它三项。

实测（20 条子集，level=development、version=0.9.0）：

    - dataset level must be full
    - dataset version must be 1.0.0
    - each intent must have exactly 16 cases
    - full evaluation dataset must contain at least 80 cases
    exit=1

CI 会在 `.github/workflows/backend-ci.yml:71-74` 执行它；`tests/unit/test_golden_dataset.py:19-38` 也硬断言 80/16。

### D-12 已采集但无人消费的字段（无效观测成本）

| 字段 | 采集位置 | 消费方 |
|---|---|---|
| retrieved_references | `e2e.py:459-469,521` | **无**（仅 `diagnose_eval_failures.py:161` 计数） |
| routing_decisions | `e2e.py:665-676,512` | **无**（指标从不读） |
| tool_error_codes | `e2e.py:513-519` | **无**（仅 diagnose 脚本分桶） |
| graph_step_count | `e2e.py:497` | **无** |
| observed_graph_nodes | `e2e.py:496` | **无**（仅 diagnose 脚本） |
| reference_answer | 数据集字段 | **无** |
| tags | 数据集字段 | **无** |

### D-13 cost_usd 恒为 0

`run_eval.py:449` `cost_usd=Decimal("0")`，cost_basis 固定为 `local_ollama_no_provider_billing`（`:380,411`）。若 20 条改为调用付费 API，成本维度需要新代码。

### D-14 --compare 的基线门槛默认值会伪装成"实测基线"

`evaluate_thresholds` 在无 baseline 时用设计门槛：trajectory ≥ 0.70（`metrics.py:439`）、冗余 ≤ 0.20（`:451`）、P95 ≤ 10000ms（`:463`）。但 details 里会标注 `design_gate`（`:478-487`）。artifact 里 `qwen3-30b-a3b-e2e80-fixes-20261024.json` 的 details 确实全是 design_gate。**风险在解读环节**：这三个"设计门槛"数字容易被误读成实测基线，与 `AGENTS.md:447`（"以下数字是设计门槛，不是当前成绩"）冲突。

---

## 5. 20 条数据集的"最小标签模板"

### 5.1 硬结论：13 个字段一个都不能删

`GoldenCase` 的 13 个字段**全部 required**（实测 `model_fields` 中 optional=[]），且 `extra="forbid"`：

    required: ['case_id','query','requester_id','expected_intent','expected_handoffs',
               'expected_tools','expected_risk','expected_require_human','expected_outcome',
               'relevant_document_ids','min_business_steps','reference_answer','tags']
    optional: []
    extra policy: forbid

只给 case_id+query 会得到 **11 条 missing-field 错误**。因此在**不改 schema 的前提下，"可删字段"为零**。下表区分的是"语义上必须精确标"与"可填占位值"：

| 字段 | 标注等级 | 为什么 |
|---|---|---|
| case_id | 🔴 必须精确 | 主键 + 所有指标的 join key；前缀必须是 3 个大写字母 |
| query | 🔴 必须精确 | 唯一驱动 live 模型的输入；长度 ≤2000（数据集现状 ≤48） |
| requester_id | 🟠 必须有真实值 | 进 ActorContext.user_id（`nodes.py:342-346`），决定 Policy 的 owner 校验与自服务判定。种子用户只有 user-requester-001/002、user-approver-001、user-inactive-001（`scripts/maintenance/seed_demo_data.py:35-38`）。**现在 80 条全用 user-requester-001** |
| expected_intent | 🔴 必须精确 | intent 指标唯一来源 |
| expected_handoffs | 🔴 必须精确 | handoff 指标唯一来源；词汇见 §1.3 |
| expected_tools | 🔴 必须精确 | 同时驱动 4 个指标 + 并发隔离锁 |
| expected_risk | 🟠 精确（但对 3 个写 Tool 会被覆盖） | 见 D-06 |
| expected_require_human | 🔴 必须精确 | policy accuracy 的 require_human 期望；且校验器要求 true ⇒ risk=high |
| expected_outcome | 🔴 必须精确 | task_success + policy 的 allowed 期望 |
| relevant_document_ids | 🔴 必须精确（检索类必填） | retrieval 唯一来源；**空 = 退出分母** |
| min_business_steps | 🟡 可给默认值 1 | 只喂 trajectory（已失效，见 D-08）；校验器只要求 >0 |
| reference_answer | 🟡 可给短占位 | 无指标消费；但 schema 要求非空 |
| tags | 🟡 可给空 set 或粗粒度值 | 无指标消费；但若要做分层报告需先改代码 |

### 5.2 20 条的配额建议（保证每个指标都有分母）

| 配额项 | 建议条数 | 理由 |
|---|---:|---|
| 总计 | 20 | 5 intent × 4 = 20（对齐现有 16×5 的结构，等比缩小） |
| 每 intent | ≥ 4 | 否则 intent 指标方差无法解释 |
| expected_tools 含写 Tool | **≥ 10** | policy coverage 分母；否则 coverage 进入"空分母 1.0"陷阱 |
| 其中 refund ≥ 200（high） | ≥ 2 | 唯一能产出真实 approval 分支的路径（`policy.py:323-331`） |
| 其中 refund < 200（medium） | ≥ 1 | 边界覆盖（数据集现有 199.99/200.00 两条） |
| 非 refund 写 Tool（request_access/reset_password/create_ticket） | ≥ 4 | 覆盖 `policy.py:174/233` 的 medium 分支 |
| relevant_document_ids 非空 | **≥ 8** | retrieval 分母；否则 P/R 假满分 |
| expected_outcome=rejected | ≥ 3 | 覆盖确定性拒绝路径（`edges.py:144,158,197`） |
| expected_outcome=escalated | ≥ 2 | 覆盖 escalation 终态 |
| expected_outcome=waiting_approval | ≥ 2 | 覆盖 HITL 中断（`e2e.py:677-693`） |
| 纯读只走 Knowledge | ≥ 2 | 覆盖 supervisor→knowledge→supervisor→finalize |
| 空 expected_tools（注入/越权） | ≥ 2 | F-04/F-06 对应；**注意这些 case 不进 policy 分母** |

### 5.3 每条 case 的标注要点（写错就会静默扣分的地方）

| 场景 | 正确写法 | 常见错法及其后果 |
|---|---|---|
| 纯读 + 检索 | expected_tools=["search_knowledge"]，handoffs = supervisor→knowledge / knowledge→supervisor / supervisor→finalize，relevant_document_ids=["doc-xxx"] | 漏掉 knowledge→supervisor → LCS 分母仍按 3 算，matched 只有 2 → 白丢 1/3 |
| 读 Tool（如 query_order） | handoffs 必须含 supervisor→action_propose / action_propose→supervisor / supervisor→finalize（**没有 action_execute 边**，因为读路径被 `e2e.py:287-289` 跳过） | 写成含 action_propose→action_execute → 结构上不可达，永远失分 |
| 写 Tool 且低/中风险 | handoffs 含 supervisor→action_propose / action_propose→policy / policy→action_execute / action_execute→supervisor / supervisor→finalize | 漏 action_execute→supervisor |
| 写 Tool 且高风险待审批 | handoffs 止于 policy→approval，expected_outcome=waiting_approval，expected_require_human=true，expected_risk=high | expected_require_human=true 但 risk≠high → 校验器直接报错（`validate_golden_dataset.py:58-59`） |
| 确定性拒绝 | handoffs 以 action_propose→finalize（或 supervisor→finalize）收尾，expected_outcome=rejected | 想写 →rejected 这个 target → 词汇表里不存在；想写 knowledge→finalize → **物理不可达**（见 E5） |
| escalation | 只能用 action_execute→finalize / ...→escalated 等已有词；注意 escalated 作为 target 目前**无任何数据集样本** | 直接写 policy→escalated 需要 new label，虽然 `derive_actual_handoffs` 能产出它，但没有先例可参考 |

### 5.4 最小 JSON 骨架

    {
      "version": "0.1.0",
      "level": "smoke",
      "review_status": "reviewed",
      "cases": [
        {
          "case_id": "NET-001",
          "query": "公司 VPN 无法连接，请查询当前服务状态并给出恢复 SOP。",
          "requester_id": "user-requester-001",
          "expected_intent": "network",
          "expected_handoffs": [
            {"from": "supervisor", "to": "action_propose"},
            {"from": "action_propose", "to": "supervisor"},
            {"from": "supervisor", "to": "knowledge"},
            {"from": "knowledge", "to": "supervisor"},
            {"from": "supervisor", "to": "finalize"}
          ],
          "expected_tools": ["check_service", "search_knowledge"],
          "expected_risk": "low",
          "expected_require_human": false,
          "expected_outcome": "resolved",
          "relevant_document_ids": ["doc-network-vpn"],
          "min_business_steps": 3,
          "reference_answer": "VPN 服务降级；按 SOP 重新认证重连，仍失败则建跟进工单。",
          "tags": ["normal", "retrieval", "read_only"]
        }
      ]
    }

### 5.5 落盘前自检清单

| # | 检查项 | 判定依据 |
|---|---|---|
| 1 | 每个 case_id 匹配 `^[A-Z]{3}-\d{3}$` 且唯一 | schema + `validate_golden_dataset.py:43-45` |
| 2 | 每条 expected_tools 都在 8 个 ToolName 闭集内 | `src/tools/schemas.py:48-57` |
| 3 | 所有 from/to 都在 8 词闭集内：supervisor/knowledge/action_propose/action_execute/policy/approval/finalize/escalated | `e2e.py:245-296` 可达集 |
| 4 | expected_require_human=true ⇒ expected_risk="high" | `validate_golden_dataset.py:58-59` |
| 5 | expected_outcome="waiting_approval" ⇒ expected_require_human=true | `validate_golden_dataset.py:60-64` |
| 6 | 含写 Tool ⇒ handoffs 里出现 policy | `validate_golden_dataset.py:69-70` |
| 7 | relevant_document_ids 非空 ⇒ expected_tools 含 search_knowledge | `validate_golden_dataset.py:71-72` |
| 8 | 检索类 case 的 doc id 必须真实存在于知识 fixture | `src/backend/fixtures/knowledge.json`（当前 25 篇 / 51 chunk） |
| 9 | 写 Tool case ⇒ requester_id 必须是种子用户，且资源归属匹配 | `scripts/maintenance/seed_demo_data.py:35-45` |
| 10 | 同一 intent 的 case 不要全用同一个 requester_id | 否则 Policy 的 owner/self-service 分支覆盖不足 |

---

## 6. 要跑 20 条，必须改哪些代码

| 优先级 | 改动点 | 文件:行 | 性质 |
|---|---|---|---|
| P0 | 放开 e2e 的硬编码 80 条门槛 | `src/eval/e2e.py:142-151` | 阻塞：否则 20 条完全无法进 live e2e |
| P0 | 数据集校验器的 version/level/80/16 硬编码 | `scripts/maintenance/validate_golden_dataset.py:15-21,34-41,48-49` + `tests/unit/test_golden_dataset.py:19-38` | 阻塞：CI 会红 |
| P1 | retrieval 提取：HEAD 缺 document_ids 写入 + 工作区未提交 | `src/backend/governance/executor.py:85-93,249`（当前为未提交改动）、`src/eval/e2e.py:429-442` | 正确性：否则 P/R 恒 0 |
| P1 | high_risk_bypass 的 `is` 身份比较 → 值比较 | `src/eval/e2e.py:409`；或把 `schemas.py:63` 的 `RiskLevel \| str` 收窄成 RiskLevel | 正确性：否则该指标不可伪证 |
| P1 | policy accuracy 去掉硬编码 risk 表，改用 expected_risk | `src/eval/metrics.py:151-159` | 正确性：否则 15 条标签被忽略 |
| P2 | trajectory 的 actual_business_steps 量纲对齐 | `src/eval/e2e.py:506`（现在是 `len(audits)`）+ `src/eval/metrics.py:282-286` | 有效性：否则恒 ≈0.97 |
| P2 | 冗余调用率改用"同 tool 在成功之后再次发起"的事件级判定 | `src/eval/metrics.py:292-311` | 有效性：否则恒 0 |
| P2 | P95 在 n<50 时改报 max / p90 + 超阈计数 | `src/eval/metrics.py:314-321,463` | 有效性 |
| P3 | 增加 exact-match handoff 指标（把 diagnose 脚本的逻辑搬进 harness） | `scripts/maintenance/diagnose_eval_failures.py:77-98` → `src/eval/metrics.py` | 新指标 |
| P3 | Faithfulness：需要新 schema 字段 + 独立 evaluator + ≥50 条人工校准 | 无现有实现 | 新指标（20 条规模下不可用） |
| P3 | tags 分层报告 | `src/eval/metrics.py` | 新能力 |
| P3 | cost_usd 真实计费 | `src/eval/run_eval.py:449,380,411` | 元数据 |

---

## 附录 A：审计中实际执行的验证命令与结果

| 验证 | 命令要点 | 结果 |
|---|---|---|
| 20 条 dataset 能否过 e2e 准入 | `validate_e2e_dataset(20-cases, level=full/smoke)` | 两种都被拒：`e2e-candidate requires the unchanged reviewed full dataset of exactly 80 cases` |
| 20 条能否跑 offline | `python -m eval.run_eval --mode offline --stage model-baseline --dataset ds20.json` | 成功，metrics=`{"scope":"classification_only","intent_accuracy":"1.0000","evaluated_cases":20,...}`，threshold_gate=null |
| 20 条能否过校验器 | `python -m scripts.maintenance.validate_golden_dataset --path ds20.json --allow-pending-review` | 4 条 issue，exit=1 |
| RiskLevel \| str 的 union 行为 | 传枚举 → `is` 为 True；传字符串 → False；`model_dump(mode="json")` 往返后变 str |
| `_is_high_risk_bypass` 对两种输入的返回值 | 枚举 → True；字符串 → **False** | 证明 D-07 |
| handoff 可达边穷举 | 遍历 `derive_actual_handoffs` 的三节点序列 | 可达 19 条边；标签用了 12 条；knowledge→finalize（RFD-009）不可达；8 条 escalation 边从未被标注 |
| trajectory 饱和验证 | 用 80 条 artifact + 数据集 min_steps | 75/80 的原始比值 ≥1.0，截断后均值 0.9688 |
| retrieval 空值验证 | 统计 search_knowledge 命中数 vs retrieved_document_ids 非空数 | 27 vs **0** |
| policy risk 冲突 | 标签 expected_risk vs `metrics.py:151-159` 硬编码表 | 15 条冲突 |
| CP95 区间 | Clopper-Pearson 精确解 | 见 §3.2 |
