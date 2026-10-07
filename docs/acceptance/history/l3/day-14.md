# Day 14 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Approval API、乐观并发控制与 TTL
- Commit SHA: `d1a26cf`
- Changed files:
  - `backend/auth/dependencies.py`
  - `backend/api/approvals.py`
  - `backend/db/repositories/approvals.py`
  - `backend/db/repositories/outbox.py`
  - `backend/jobs/tickets.py`
  - `specs/openapi/openapi.json`
  - `frontend/src/types/api.d.ts`
  - `tests/integration/test_approval_api.py`
  - `tests/integration/test_app_lifespan.py`
  - `tests/unit/test_openapi_contract.py`
  - `docs/evidence/l3/day-14.md`
- Contract/permission/state/eval impact:
  - 交付审批 API 端点与角色权限守卫：
    - 在 `backend/auth/dependencies.py` 实现 `require_approver` / `ApproverActor` 依赖：非 `approver` 角色（如 `requester`）访问审批路由直接返回 403 `FORBIDDEN`。
    - 实现 `GET /api/approvals`：支持通过 `status`（`pending`/`approved`/`rejected`/`expired`）过滤审批列表，自动在查询前触发已到期审批的惰性 TTL 标记。
    - 实现 `GET /api/approvals/{approval_id}`：校验 UUID 格式，返回脱敏后的参数列表（`args_summary: list[RedactedArgument]`）、不可篡改的 `args_hash` 及原因代码。不存在或非法 UUID 返回 404 `APPROVAL_NOT_FOUND`。
    - 实现 `POST /api/approvals/{approval_id}/decision`：严格受控的审批决定接口，只接受 `decision`（`approve`/`reject`）、`version` 和可选 `reason`，禁止在载荷中传递或修改任何业务参数（Schema 级 `extra="forbid"`，多余字段返回 422）。
  - 乐观并发控制（OCC）与幂等防重：
    - `ApprovalRepository.decide` 通过 `WHERE id = :id AND version = :version AND status = 'pending' AND expires_at > now()` 条件更新。
    - 两个或多个并发决定基于相同初始版本发起时，PostgreSQL 行级锁与版本条件确保恰好一个执行成功，后续并发请求捕获 409 `APPROVAL_CONFLICT`。
    - 重复决定已决定的审批返回 409 `APPROVAL_CONFLICT`，杜绝状态回退或二次决定。
  - 参数防篡改核对（Args Hash Guard）：
    - 在决定生效前核对 `operation_id + args_hash`；若底层操作记录的参数哈希与审批记录不一致，坚决拒绝并返回 409 `ARGS_HASH_MISMATCH`，防止投毒或漂移。
  - 事务性恢复入队（Transactional Resume Outbox）：
    - 仅当审批结论为 `approved` 时，在同一数据库事务中通过 `OutboxRepository.enqueue_resume_ticket` 向 `job_outbox` 表写入 `resume_ticket` 任务行。
    - `dedupe_key` 格式由 `resume_job_id(approval_id, version)` 确定性派生（如 `resume:{approval_id}:{version}`），强保证重试或重放不会生成第二条恢复事件。
    - 若决定为 `reject`，审批状态流转为 `rejected`，严格不生成任何恢复 outbox 记录。
  - TTL 过期处理：
    - 已过 `expires_at` 的审批无论通过列表查询还是决定调用，均安全流转为 `expired` 终态。
    - 针对过期审批的决定请求直接返回 409 `APPROVAL_EXPIRED`，绝不恢复执行。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy --python-version 3.12 agents tools backend eval scripts` | 0 | `Success: no issues found in 63 source files` |
| `uv run pytest tests/integration/test_approval_api.py -v` | 0 | `10 passed in 5.17s` |
| `uv run pytest tests/unit -q` | 0 | `200 passed in 3.65s` |
| `uv run pytest tests/integration -q` | 0 | `83 passed in 25.65s` |
| `uv run python scripts/export_openapi.py` | 0 | `Wrote OpenAPI snapshot: specs/openapi/openapi.json` |
| `npm run generate:api-types` | 0 | `openapi-typescript specs/openapi/openapi.json -> src/types/api.d.ts` |
| `npm run type-check` | 0 | `vue-tsc --noEmit -p tsconfig.json (passed)` |
| `npm run test` | 0 | `5 passed (2 test files)` |
| `uv run python scripts/validate_agent_contracts.py` | 0 | `Agent and Prompt contracts are valid.` |
| `uv run python scripts/validate_golden_dataset.py` | 0 | `20 cases valid across 5 intents and 8 tools` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Non-approver Access Control | Requester gets 403 `FORBIDDEN` on GET/POST approvals; anonymous gets 401 | `tests/integration/test_approval_api.py::test_non_approver_cannot_access_approval_routes` |
| List & Filter Approvals | Approver lists pending/approved records with active filtering | `tests/integration/test_approval_api.py::test_approver_can_list_and_filter_approvals` |
| Approval Detail & Redacted Args | Approval detail returns full redacted args list and args_hash | `tests/integration/test_approval_api.py::test_approver_can_view_approval_detail` |
| Approve & Transactional Outbox | Approved approval increments version to 2; exactly 1 `resume_ticket` outbox row created | `tests/integration/test_approval_api.py::test_approver_can_approve_and_creates_resume_outbox` |
| Reject & Zero Resume Outbox | Rejected approval transitions to `rejected`; strictly 0 resume outbox rows created | `tests/integration/test_approval_api.py::test_approver_can_reject_without_resume_outbox` |
| Concurrent Decision Race (OCC) | 2 concurrent approve calls with version 1: exactly 1 succeeds (200), 1 fails (409); exactly 1 outbox row | `tests/integration/test_approval_api.py::test_concurrent_decisions_race_condition_occ` |
| Repeat Decision Conflict | Second decision on resolved approval fails closed with 409 `APPROVAL_CONFLICT` | `tests/integration/test_approval_api.py::test_repeat_decision_on_decided_approval_fails` |
| TTL Expired Guard | Decision on expired approval rejected with 409 `APPROVAL_EXPIRED`; marked `expired` in DB | `tests/integration/test_approval_api.py::test_expired_approval_cannot_be_decided` |
| Forbidden Argument Modification | Decision payload with extra/modified args rejected with 422 | `tests/integration/test_approval_api.py::test_cannot_edit_parameters_in_decision_body` |
| Args Hash Tamper Guard | Mismatched operation args hash triggers 409 `ARGS_HASH_MISMATCH`; 0 resume outbox | `tests/integration/test_approval_api.py::test_args_hash_mismatch_prevents_resume` |

## Human review

- Approver RBAC Boundary: 确认仅 approver 角色可查询与裁决审批，requester 角色严格被 403 阻断。
- Optimistic Concurrency Control: 确认依赖 PostgreSQL 原子 UPDATE 条件匹配，两并发决定绝对只会有 1 次成功，且严格只产生 1 条恢复 outbox。
- Argument Immutability: 确认决定接口禁止修改任何业务参数，杜绝“批准 A 参数却执行 B 参数”的安全漏洞。
- Args Hash Double Check: 确认决定时核验 `operation_id + args_hash` 一致性，防范并发竞争下的参数篡改。

## Remaining gaps

- Day 14 完成。进入 Day 15（LangGraph interrupt/resume 与审批节点）。
