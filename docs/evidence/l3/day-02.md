# Day 02 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: 图状态、Dispatcher 与字段所有权
- Commit SHA: pending commit
- Changed files:
  - `backend/graph/state.py`
  - `backend/graph/context.py`
  - `backend/graph/edges.py`
  - `tests/unit/test_routing.py`
  - `tests/unit/test_state_ownership.py`
  - `docs/evidence/l3/day-02.md`
- Contract/permission/state/eval impact:
  - 严格落实 AGENTS.md §2.1 状态字段所有权，Dispatcher 是 `status`、`agent_history`、`step_count` 的唯一写入方。
  - 强制 `MAX_STEPS = 15`，达到上限精确进入 `escalated` 终态并不再流转。
  - 终态（`resolved`、`escalated` 等）不可变。
  - `edges.py` 保持纯函数与确定性，零 import ModelClient 或 Tool callable。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run pytest tests/unit/test_routing.py tests/unit/test_state_ownership.py -q` | 0 | `10 passed in 0.35s` |
| `uv run pytest tests/unit -q` | 0 | `140 passed in 2.52s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 58 source files` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Routing & State Ownership Unit Suite | 10 unit tests covering legal/illegal routing, step limits, ownership validation | `tests/unit/test_routing.py`, `tests/unit/test_state_ownership.py` |

## Human review

- Authorization boundary: 确认 Dispatcher 拥有状态转移与步数递增的排他写入权，Supervisor 零 Tool 调用权限。
- Sensitive-data review: 状态与步骤记录中无敏感凭据或用户私密数据。
- Test-quality review: 单元测试先红后绿，覆盖低置信度降级、15 步强制截断与跨角色越权写状态字段拦截。
- Documentation accuracy: 严格对齐 AGENTS.md §2.1 所有权表格。

## Remaining gaps

- None for Day 02. Ready for Day 03 LangGraph node closures & read-only graph assembly.
