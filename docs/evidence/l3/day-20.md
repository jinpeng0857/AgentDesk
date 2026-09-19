# Day 20 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: F-05 审批过期与 F-08 重复决定/恢复
- Changed files:
  - backend/db/repositories/approvals.py
  - backend/jobs/tickets.py
  - tests/test_failure_modes.py
  - docs/evidence/l3/day-20.md
- Contract/permission/state/eval impact:
  - F-05 审批单 TTL 过期边界与 Worker 绕过阻断（Approval Expiry & Worker Gate）：
    - 在 `backend/db/repositories/approvals.py` 的 `decide` 方法中支持注入固定 UTC 时钟（`now: datetime | None = None`），在原子 SQL 条件更新中严格校验 `expires_at > now`。
    - 验证精确时间边界：
      1. 在到期前 1 秒（`expires_at - 1s`）：审批决定成功，版本升为 2，状态推进为 `approved`。
      2. 在到期后 1 秒（`expires_at + 1s`）：审批决定被底层 SQL 条件更新拒绝，抛出 `ApprovalConflictError`，数据库记录保持为 `pending`。
      3. 后台过期任务：`expire_pending(cutoff=t_after)` 批量将过期审批单流转至 `expired` 终态。
    - Worker 防绕过校验：在 `backend/jobs/tickets.py` 的 `GraphTicketRunner.resume` 中，增加前置审批单有效性核对。如果审批单已过期或状态为 `expired`，立即抛出 `ApprovalConflictError`，彻底阻断绕过 API 直接向 Worker 注入过期 Resume 载荷的非法路径，副作用严格为 0。
  - F-08 并发决定乐观锁与重复 Resume 业务幂等（OCC Concurrency & Replay Idempotency）：
    - 并发决定互斥保证：
      1. 并发 approve/approve 竞争：针对 `expected_version=1` 的审批单并发提交，严格只有一个协程更新成功（`status="approved"`, `version=2`），另一个协程遭遇冲突并抛出 `ApprovalConflictError`。
      2. 并发 approve/reject 竞争：同意与拒绝并发提交，严格只有一个成功推进，另一个捕获版本冲突失败。
    - 重复 Resume 与重放幂等：
      1. 对已批准工单连续调用 3 次 `runner.resume`（模拟消息重复投递或 Worker 故障重试）。
      2. 每次调用均安全返回终态 `TicketStatus.RESOLVED`。
      3. 数据库退款记录（`RefundRecord`）行数严格为 1，订单扣款严格执行且仅执行 1 次。
      4. 证明系统正确性完全由数据库 ACID 事务、条件更新与幂等键保障，不依赖 Redis 锁作为正确性来源。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check agents tools backend eval scripts tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 66 source files` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run pytest tests/test_failure_modes.py -v` | 0 | `8 passed in 9.91s` |
| `uv run pytest tests/unit tests/integration -q` | 0 | `287 passed in 33.98s` |
| `git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts` | 0 | `Zero contract drift` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| F-05: TTL Expiration Boundaries & Direct Worker Bypass Prevention | Boundary t-1s succeeds, t+1s raises ApprovalConflictError, expire_pending moves to expired, direct worker resume rejected with ApprovalConflictError, 0 refunds inserted | `tests/test_failure_modes.py::test_f05_approval_ttl_expiry_boundaries_and_worker_bypass_prevention` |
| F-08: Concurrency and Duplicate Resumes Idempotency | Concurrent approve/approve and approve/reject guarantee exactly 1 winner and 1 ApprovalConflictError, 3 repeated resumes return resolved, exactly 1 refund record, balance deducted once | `tests/test_failure_modes.py::test_f08_concurrent_approval_decisions_and_duplicate_resumes_idempotency` |

## Human review

- Authorization boundary: 审批恢复必须校验有效且未过期的审批记录，过期审批无论从 API 还是 Worker 入口均无法恢复执行。
- Concurrency & Race Conditions: 基于 `status == 'pending' AND version == expected_version AND expires_at > now` 原子更新，彻底消除并发决定产生双重生效的竞态条件。
- Replay Safety & Idempotency: 多次投递与重复恢复场景下，下游写操作与余额变动次数严格等于 1。
- Single Source of Truth: 幂等与重试保证根植于 PostgreSQL 关系存储约束与事务，而非临时分布式锁。

## Remaining gaps

- Day 20 目标圆满达成！Delivery Plan Day 01 至 Day 20 全部完成并通过验证。