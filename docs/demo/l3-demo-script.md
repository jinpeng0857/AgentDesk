# AgentDesk L3 技术面试演练与演示脚本 (Demo Script)

> **时长**：3–5 分钟技术面试展示路线图  
> **核心目标**：向面试官证明系统具备生产级 Multi-Agent 协作、严格工具治理网关、确定性安全防线与真实容灾恢复链路。

---

## 0. 架构全景与职责边界（30 秒）

- **四角色隔离 (`AGENTS.md` H-01 ~ H-04)**：
  - **Supervisor**：0 工具白名单（`whitelist == []`），纯分类汇总与下一角色推荐。
  - **Knowledge**：仅允许只读检索（`search_knowledge`），输出带可回查元数据的引用引用（`knowledge_refs`）。
  - **Action**：仅提出写操作提案（`PendingOperation`），必须由执行网关审查后方可执行。
  - **Policy**：**确定性纯函数**判定（非 LLM 判定，零幻觉漏洞），判定 `allow/deny`、风险等级与是否需要人工审批。
  - **Dispatcher**：图外确定性代码控制转移拓扑，硬性约束 `MAX_STEPS = 15`。

---

## 1. 场景 A：只读自助检索与引用溯源（45 秒）

- **测试用例**：`NET-001` - *"公司 VPN 无法连接，请查询当前服务状态并给出恢复 SOP。"*
- **执行流转**：
  ```text
  Supervisor (classify: network)
  → Dispatcher
  → Knowledge (tool: search_knowledge)
  → Dispatcher
  → Supervisor (synthesize response with citations)
  → Ticket Status: RESOLVED
  ```
- **演示看点**：
  1. 打开前端页面提交工单，获取 HTTP 202 Accepted 异步响应。
  2. 点击顶部 **"Inspect Trace"** 跳转至 `TraceViewerView`。
  3. 展示 Knowledge 节点返回的结构化引用（Document ID、Chunk ID、置信度得分与内容摘要）。

---

## 2. 场景 B：确定性业务技能与敏感信息脱敏（45 秒）

- **测试用例**：`ACC-001` - *"请为我本人的 active 账号重置密码。"*
- **执行流转**：
  ```text
  Supervisor (classify: account)
  → Action (propose Skill: password_reset)
  → Policy (assess_risk: low, require_human=False)
  → Execution Gateway (whitelist check -> execute -> redact)
  → Dispatcher
  → Supervisor
  → Ticket Status: RESOLVED
  ```
- **演示看点**：
  1. 密码重置 Skill 步骤在 Python 代码中硬编码固化，模型无法越权篡改步骤。
  2. 观察 TraceViewer 与 PostgreSQL 审计表：临时密码在离开执行网关前已被掩码为 `***`，**零明文凭据泄漏**。

---

## 3. 场景 C：高风险人机协同与参数防篡改（60 秒）

- **测试用例**：`RFD-001` - *"订单 ORD-1001 服务不可用，申请退款 250 美元。"*
- **执行流转**：
  ```text
  Supervisor (classify: high_risk)
  → Action (propose: process_refund, amount=250.00)
  → Policy (assess_risk: high, require_human=True, reason: REFUND_EXCEEDS_200_USD)
  → Graph Interrupt (Thread suspended)
  → ApprovalRecord Created (Bound to operation_id + SHA-256 args_hash)
  → Human Approver Review -> Approve / Reject (Optimistic locking: version=1 -> 2)
  → Worker Resumes from PostgreSQL Checkpoint
  → Verify args_hash unchanged
  → Gateway Executes process_refund
  → Ticket Status: RESOLVED
  ```
- **演示看点**：
  1. 切换至审批人视图（`/approvals`），展示待审批卡片与不可编辑的冻结参数。
  2. 强调 **`args_hash` 强绑定**：审批人仅可决定通过/拒绝，严禁在前端随意修改退款金额或收款账户。
  3. 审批通过后，Worker 通过消息队列从 Checkpoint 精确恢复，执行真正转账。

---

## 4. 容灾与幂等演练：Worker 宕机恢复（45 秒）

- **测试用例**：`F-07` / `scripts/acceptance/recovery_drill.py`
- **故障模拟**：
  - 关键写操作写入账本后，在 Worker 提交下一步状态前强制杀进程崩溃（SIGKILL / 异常注入）。
- **演示看点**：
  - Worker 重启后读取 PostgreSQL Checkpoint 恢复执行。
  - 执行网关检测到相同的 `idempotency_key`，命中操作账本缓存（`outcome=cache_hit`），**零重复副作用，零重复扣款**。

---

## 5. 确定性评测看板与质量门槛（45 秒）

- **页面入口**：`/eval` (EvalDashboardView)
- **演示看点**：
  1. **门槛 (Threshold) vs 实测 (Observed)**：看板顶部以醒目色彩区分“设计门槛”与“实测成绩”，向面试官展示极度严谨的工程诚实度。
  2. **80-Case 完整 Golden Dataset**：覆盖 5 类 Intent（Network、Account、Permission、Query、High Risk）及 F-01~F-08 异常失败模式。
  3. **确定性指标计算**：
     - Intent Accuracy: ≥ 90%
     - Handoff Accuracy: ≥ 90%
     - Tool Selection Jaccard: ≥ 85%
     - Policy Decision Accuracy: **100% (一票否决门禁)**
     - High-risk Bypass Count: **0 (硬性安全底线)**
     - Task Success Rate: ≥ 80%
