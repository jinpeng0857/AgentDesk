# Day 21 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: F-07 Checkpoint 后崩溃恢复验证与演练
- Changed files:
  - tests/test_failure_modes.py
  - scripts/recovery_drill.py
  - docs/evidence/l3/day-21.md
- Contract/permission/state/eval impact:
  - F-07 Checkpoint 崩溃恢复与操作账本缓存（Worker Crash & Idempotent Recovery）：
    - 验证故障注入机制：模拟写工具执行完毕并向数据库操作账本（`ToolOperationRecord`）和业务领域表（`RefundRecord`）提交事务后，Worker 宿主进程遭遇致命异常（如 SIGKILL 或未捕获异常）并在图推进 Checkpoint 前终止。
    - 验证断点恢复与幂等保证：
      1. 第一阶段：Worker 1 崩溃，连接池彻底释放，验证此时操作账本记录状态为 `completed`，业务退款生成 1 笔，订单可退余额被扣除 $50.00，但工单尚未处于终态。
      2. 第二阶段：实例化独立的 Worker 2（全新长连接池、全新 Checkpointer 与全新 Runner），通过工单 ID 从 PostgreSQL Checkpoint 恢复执行。
      3. 第三阶段：网关层 `WriteToolExecutor` 根据 `tool + operation_id + args_hash` 生成的确定性幂等键命中账本缓存，直接提取已完成的输出结果，跳过底层领域适配器调用（Cache Hit）。
      4. 第四阶段：工单最终安全流转至 `resolved`。全生命周期内退款记录严格保持为 1 笔，订单金额毫无重复扣除，写副作用严格等于 1。
    - 演练脚本支持：落地 `scripts/recovery_drill.py`，支持独立执行全链路故障模拟、断点恢复与副作用无重复断言。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check agents tools backend eval scripts tests` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 67 source files` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `tests/test_failure_modes.py::test_f07_worker_crash_after_ledger_commit_recovers_without_duplicate_writes` | 0 | `F-07 crash recovery verified with 0 duplicate side effects` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| F-07: Worker Crash and Recovery Drill | Worker 1 simulated crash caught, ledger committed with 1 refund, Worker 2 resumes from checkpoint, cache hit on re-execution, final status resolved, exactly 1 refund record, 0 duplicate deductions | `scripts/recovery_drill.py`, `tests/test_failure_modes.py::test_f07_worker_crash_after_ledger_commit_recovers_without_duplicate_writes` |

## Human review

- Authorization boundary: 恢复执行严格基于原工单 ID 与存储于 PostgreSQL 的状态镜像，外部非法输入无法伪造恢复上下文。
- Idempotency & Crash Consistency: 业务写入与操作账本状态通过数据库 ACID 事务强绑定，进程重启或重试不会引起重复计费或重复写入。
- Fault Isolation: 恢复依赖持久化事实源（PostgreSQL Checkpoint + Ledger），不依赖临时内存态或脆弱的分布式锁。

## Remaining gaps

- Day 21 目标顺利达成！F-01 至 F-08 全部 8 类失败模式自动化测试与恢复脚本全量落地。
