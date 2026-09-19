# Day 17 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: F-01 Tool timeout 的重试、fallback 与 escalation
- Changed files:
  - backend/governance/executor.py
  - backend/graph/nodes.py
  - backend/graph/builder.py
  - tests/fakes/failures.py
  - tests/test_failure_modes.py
  - docs/evidence/l3/day-17.md
- Contract/permission/state/eval impact:
  - 有界重试机制（Bounded Retries）：
    - 在 `backend/governance/executor.py` 中定义 `MAX_TOOL_ATTEMPTS = 3`。
    - `ReadToolExecutor.execute` 对只读工具超时（`TimeoutError`）执行最多 3 次有界重试（`attempt_idx = 0, 1, 2`）。每次尝试均向审计接收器（AuditSink）记录一条 `ToolAttempt`，标记 `outcome=ToolOutcome.TIMED_OUT` 并精确记录当前 `retry_count`。3 次超时耗尽后返回 `ToolResult(outcome=ToolOutcome.TIMED_OUT, error_code=ERROR_TIMEOUT, retry_count=2)`。
    - `WriteToolExecutor.execute` 对写工具超时（`TimeoutError`）执行最多 3 次有界重试。每次超时向审计表写入 `outcome=ToolOutcome.TIMED_OUT` 与 `retry_count`。耗尽 3 次尝试后，将幂等操作台账 `tool_operations` 标记为 `status="failed_final"` 并附带 `{"error": "TOOL_TIMEOUT"}`，返回 `ToolResult(outcome=ToolOutcome.TIMED_OUT, error_code=ERROR_TIMEOUT, retry_count=2)`。
  - 终态推进与升级路由（Dispatch Escalation）：
    - 在 `backend/graph/nodes.py` 交付 `make_dispatch_action_execute_node` 闭包：检查 `state["tool_results"]` 的最近一条执行结果。若 `outcome` 处于 `(TIMED_OUT, FAILED_FINAL, REJECTED)`，则状态推进为 `TicketStatus.ESCALATED`；成功时推进为 `TicketStatus.RESOLVED`。
    - 在 `backend/graph/builder.py` 将 `dispatch_action_execute` 节点升级为动态调度节点 `make_dispatch_action_execute_node()`，当关键写操作超时失败时驱动工单流转至人工升级队列（`escalated`）。
  - F-01a / F-01b 确定性故障注入验证：
    - 交付 `tests/fakes/failures.py`，提供调用计数感知的 `FailingKnowledgeStore` 与 `FailingRefundService`，不使用随机概率、不使用真实 sleep。
    - F-01a（只读工具连续超时）：连续 3 次超时后，Knowledge 节点降级输出 `fallback=True`，Supervisor 生成保守回复，工单终态为 `resolved + fallback`，引用条目为空，写副作用严格为 0，审计记录恰好包含 3 条 `timed_out` 记录且 `retry_count` 分别为 0, 1, 2。
    - F-01b（关键写工具连续超时）：连续 3 次退款超时后，工单终态推进至 `escalated`，退款记录行数严格为 0，订单可退款额度和版本完全不变，台账状态为 `failed_final`（非 `succeeded`），审计记录包含 3 条 `timed_out` 尝试。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check agents tools backend eval scripts tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 66 source files` |
| `uv run pytest tests/test_failure_modes.py -v` | 0 | `2 passed in 4.02s` |
| `uv run pytest tests/unit tests/integration -q` | 0 | `287 passed in 29.64s` |
| `git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts` | 0 | `Zero contract drift` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| F-01a: Optional Read Timeout Fallback | Resolved with fallback, 0 citations, 0 write calls, exactly 3 timeout audits (retry_count: 0, 1, 2) | `tests/test_failure_modes.py::test_f01a_optional_read_tool_consecutive_timeouts_falls_back_to_conservative_response` |
| F-01b: Critical Write Timeout Escalation | Escalated status, ledger status `failed_final`, 0 refunds inserted, order untouched, exactly 3 timeout audits (retry_count: 0, 1, 2) | `tests/test_failure_modes.py::test_f01b_critical_write_tool_consecutive_timeouts_escalates` |

## Human review

- Authorization boundary: 网关重试行为仅限于已通过白名单与权限校验的请求，超时重试不改变操作鉴权主体。
- Sensitive-data review: 所有重试与超时审计事件均经过 `redact_mapping` 脱敏，不泄漏请求参数中的敏感凭据。
- Bounded Retries & No Thundering Herd: 重试上限固定为 3 次，超时立即失败转由工单状态机进行降级或人工升级，不产生无界循环。
- Idempotency & Side Effects: 写操作超时过程中与耗尽后，业务写副作用严格为 0，底层订单状态与金额完全未被污染。

## Remaining gaps

- Day 17 完成。进入 Week 4 / Day 18（F-02 非法模型结构与 F-03 图循环）。
