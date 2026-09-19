# Day 15 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: LangGraph interrupt/resume 与审批节点
- Commit SHA: pending
- Changed files:
  - backend/graph/nodes.py
  - backend/graph/builder.py
  - backend/graph/edges.py
  - backend/graph/context.py
  - backend/jobs/context.py
  - backend/jobs/tickets.py
  - backend/jobs/worker.py
  - backend/jobs/outbox.py
  - tests/integration/test_graph_hitl.py
  - tests/integration/test_resume_idempotency.py
  - docs/evidence/l3/day-15.md
- Contract/permission/state/eval impact:
  - Human-in-the-Loop (HITL) 审批节点与 LangGraph 挂起/恢复：
    - 在 `backend/graph/nodes.py` 交付完整节点闭包：
      - `make_action_propose_node`：Action Agent 生成写操作提案，通过 `create_pending_operation` 确定性工厂转换为 `PendingOperation`。
      - `make_policy_node`：基于只读纯函数 `assess_risk` 确定性评定风险等级，高风险操作命中 `RiskLevel.HIGH` 且强制 `require_human=True`。
      - `make_dispatch_policy_node`：当判定为高风险时，Dispatcher 将工单状态流转至 `waiting_approval`，同步数据库投影。
      - `make_approval_node`：检查底层 `ApprovalRecord` 是否存在，若无则持久化待决记录；调用 `langgraph.types.interrupt` 挂起图执行，等待人工决策；恢复时读取决议记录，生成 `ApprovalSnapshot`。
      - `make_action_execute_node`：当审批决议为 `approved` 时，网关加载 `WriteToolExecutor` 校验操作绑定与哈希，执行实际写工具（如 `process_refund`），记录操作账本与审计日志，更新工单结果与数据库。
    - 在 `backend/graph/edges.py` 与 `builder.py` 完善路由闭环：
      - `route_from_approval` 支持根据审批决议流转：`approved` 路由至 `action_execute`；`rejected` 路由至 `reject` 终态节点并终止图。
      - `route_from_action` 与 `route_from_policy` 构成严格的 `action_propose -> policy -> approval -> action_execute` 递进链条。
  - 图恢复（Resume）与 ARQ 任务调度体系：
    - 在 `backend/jobs/tickets.py` 实现 `GraphTicketRunner.resume(ticket_id, approval_id)`：通过 `Command(resume={"approval_id": approval_id})` 恢复指定 thread 的中断状态。
    - 增加终态检查守护（Terminal Status Guard）：若工单在数据库中已处于终态（`resolved`/`rejected`/`escalated`/`failed`/`expired`），`run` 与 `resume` 均直接返回该终态，绝不重复唤醒图执行。
    - 在 `backend/jobs/tickets.py` 实现 `resume_ticket(ctx, ticket_id, approval_id)`。
    - 在 `backend/jobs/worker.py` 注册 `resume_ticket` 到 `WorkerSettings.functions`。
    - 在 `backend/jobs/outbox.py` 实现对 `resume_ticket` 事务性任务行的分发，将 `ticket_id` 与 `approval_id` 入队 Redis。
  - 幂等性与重放攻击防护（Resume Idempotency）：
    - 针对同一已批准工单连续调用多次 `runner.resume(ticket_id, approval_id)`，底层写操作账本与工单终态检查共同确保实际写工具（如退款）严格只执行一次，退款金额、订单可退金额与版本号保持单次变更后的不可变状态。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy --python-version 3.12 agents tools backend eval scripts` | 0 | `Success: no issues found in 63 source files` |
| `uv run pytest tests/integration/test_graph_hitl.py tests/integration/test_resume_idempotency.py -v` | 0 | `4 passed in 5.76s` |
| `uv run pytest tests/unit -q` | 0 | `200 passed in 3.52s` |
| `uv run pytest tests/integration -q` | 0 | `87 passed in 28.07s` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run python scripts/validate_golden_dataset.py` | 0 | `20 cases valid across 5 intents and 8 tools` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| High-Risk Interrupt at Waiting Approval | High-risk refund triggers interrupt, ticket status in DB becomes `waiting_approval`, ApprovalRecord created with `pending`, side effects strictly 0 | `tests/integration/test_graph_hitl.py::test_high_risk_refund_interrupts_at_waiting_approval` |
| Approve Resume & Side Effects | Deciding `approve` and calling `runner.resume` executes write tool, resolves ticket, creates exactly 1 refund, and updates order refundable amount | `tests/integration/test_graph_hitl.py::test_approve_resume_executes_write_tool_and_resolves` |
| Reject Resume & Terminal State | Deciding `reject` and calling `runner.resume` routes to rejected terminal status, updates DB to `rejected`, 0 refunds executed | `tests/integration/test_graph_hitl.py::test_reject_resume_transitions_to_rejected_terminal` |
| Repeated Resume Idempotency | Consecutive duplicate resume calls return `resolved` while write side effects remain strictly executed once (1 refund, exactly decremented once) | `tests/integration/test_resume_idempotency.py::test_repeated_resume_calls_execute_write_tool_strictly_once` |

## Human review

- LangGraph State Machine Invariants: 严格遵守 AGENTS.md §2.1 状态所有权。Action propose 写入 `pending_operation`，Policy 写入 `risk_decision`，Approval 写入 `approval`，Action execute 写入 `tool_results`，Dispatcher 唯一写入 `status`/`agent_history`/`step_count`。
- Zero Side Effect Prior to Approval: 高风险写操作在审批完成前被图中断挂起，订单状态与退款账本均未产生任何写入，副作用严格为 0。
- Multi-Delivery & Replay Defense: 即使因为网络超时或消息重试导致多次交付 `resume_ticket` 任务，工单终态幂等门禁确保图不重复运行，写操作网关幂等租约确保工具不重复执行。

## Remaining gaps

- Day 15 完成。进入 Day 16（审批前端视图与三用例演练脚本）。
