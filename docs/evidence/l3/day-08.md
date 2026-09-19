# Day 08 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Phase 2 数据模型与空库迁移（操作账本、审批表与业务写结果表）
- Commit SHA: `PENDING_COMMIT_SHA`
- Changed files:
  - `eval/artifacts/phase2-before.json` (baseline frozen at `c44a5e7` before Phase 2 code changes)
  - `backend/db/models.py`
  - `backend/governance/idempotency.py`
  - `backend/db/repositories/operations.py`
  - `backend/db/repositories/approvals.py`
  - `alembic/versions/20260916_0002_phase2_governance_hitl.py`
  - `tests/unit/test_operation_identity.py`
  - `tests/integration/test_migrations.py`
  - `docs/evidence/l3/day-08.md`
- Contract/permission/state/eval impact:
  - 严格保持在开始 Phase 2 任何实现前，于干净工作区记录并提交了 live baseline `eval/artifacts/phase2-before.json`。
  - 规范并实现了幂等键派生契约：`canonical_json`、`compute_args_hash`（SHA-256）与 `derive_idempotency_key`（`tool_name:operation_id:args_hash` 的稳定 SHA-256 派生）。
  - 规范并实现了审批绑定不变量校验（architecture §5.3）：`approved.operation_id == pending_operation.operation_id`，`approved.args_hash == pending_operation.args_hash`，任何不匹配确定性抛出异常阻断执行。
  - 新增 PostgreSQL 治理与写结果数据表定义与 Alembic 迁移脚本 `20260916_0002_phase2_governance_hitl.py`：
    - `tool_operations`：持久化 `operation_id`、`tool_name`、`args_hash`、`idempotency_key`（唯一索引）、`status`、`lease_holder`、`lease_expires_at`、`encrypted_result`（仅允许加密密文）和脱敏 `result_summary`。
    - `approvals`：绑定操作的审批事实、`operation_id`（唯一索引）、`version`（乐观锁版本，初始为 1）、`status`、`args_hash`、`args_summary`、`expires_at`、`decided_at`。
    - `refunds`、`access_requests`、`downstream_tickets`：结构化业务写结果表，采用 Decimal 金额、UTC 带时区时间戳与数据库约束。
    - `users`：增加 `version` 列与非负正整数校验约束。
  - 交付 `OperationRepository`（操作创建、基于 idempotency_key 查询、原子租约获取与防并发排他机制、成功/失败记录）与 `ApprovalRepository`（审批创建、列表与详情、基于 version 与 pending 条件更新的乐观并发控制、过期自动批量失效）。
  - 空库升级与已有数据库升级全部通过 `alembic upgrade head`，应用运行不自动 `create_all`。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 62 source files` |
| `uv run pytest tests/unit/test_operation_identity.py -q` | 0 | `6 passed in 0.03s` |
| `uv run pytest tests/unit -q` | 0 | `147 passed in 2.95s` |
| `uv run pytest tests/integration/test_migrations.py -q` | 0 | `6 passed in 2.06s` |
| `uv run pytest tests/integration -q` | 0 | `57 passed in 14.93s` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Frozen Phase 2 Model Baseline | Evaluated on golden dataset (20 cases) with deepseek-chat live model before any Phase 2 modifications | `eval/artifacts/phase2-before.json` |
| Database Migration & Concurrency Control | Clean database upgrade builds all tables, unique indexes, and optimistic locking check constraints; OperationRepository and ApprovalRepository pass live integration tests against PostgreSQL | `tests/integration/test_migrations.py` |
| Idempotency & Operation Identity Isolation | Distinct operation IDs per write; stable deterministic args hash and idempotency keys; approval binding verification | `tests/unit/test_operation_identity.py` |

## Human review

- Authorization boundary: 审批只允许由 approver 处理，审批结果通过版本控制严格与指定 operation_id 和 args_hash 绑定；业务写结果只在通过 Policy 与治理网关后产生。
- Sensitive-data review: 操作账本明确使用 `encrypted_result` 存储潜在敏感结果，脱敏摘要保存于 `result_summary`，无临时密码、token 或卡号明文存入未加密列。
- Concurrency & Optimistic Lock: 验证 `ApprovalRepository.decide` 条件更新机制：两个并发决定只有一个成功，冲突方抛出 `ApprovalConflictError`，版本严格从 1 递增至 2。
- Test quality: 覆盖操作身份、幂等键派生、空库迁移、真实表结构与索引存在性、租约获取排他性与审批乐观锁生命周期测试。

## Remaining gaps

- Day 08 数据模型与迁移基建完成。进入 Day 09（确定性 Policy 风险矩阵）。
