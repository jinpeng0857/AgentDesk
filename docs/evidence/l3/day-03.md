# Day 03 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: 节点闭包与只读 LangGraph
- Commit SHA: pending commit
- Changed files:
  - `backend/graph/nodes.py`
  - `backend/graph/builder.py`
  - `tests/integration/test_graph_read_only.py`
  - `pyproject.toml`
  - `docs/evidence/l3/day-03.md`
- Contract/permission/state/eval impact:
  - 依赖注入节点闭包：`make_supervisor_node`, `make_knowledge_node`, `make_supervisor_finalizer_node`, `make_dispatch_node`。
  - Supervisor 白名单工具集保持为空，严禁调用任何 Tool。
  - Knowledge 节点白名单仅限 `search_knowledge`，且仅追加具备完整五字段（`title`, `doc_id`, `snippet`, `url`, `confidence`）的验证引用。
  - 节点输出均通过 `validate_node_output_keys` 严格校验字段归属权。
  - Case A“VPN 无法连接”在只读 LangGraph 下端到端验证通过，无任何写操作副作用，步数严格有界。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run pytest tests/integration/test_graph_read_only.py -q` | 0 | `1 passed in 0.22s` |
| `uv run pytest tests/unit -q` | 0 | `140 passed in 2.24s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 59 source files` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Read-only LangGraph Vertical Slice (Case A: VPN Connection) | `resolved`, 1 citation with 5 complete fields, 0 write adapter calls, bounded steps (4 steps) | `tests/integration/test_graph_read_only.py` |

## Human review

- Authorization boundary: 确认 Supervisor 节点仅负责分发和终结，无工具调用权限；Knowledge 节点只能调用 `search_knowledge`，不可调用写工具或产生写副作用。
- Sensitive-data review: 知识检索与引用中无敏感凭据或个人隐私信息泄漏。
- Test-quality review: 针对 Case A 设计完整状态推进序列，覆盖 `supervisor` -> `knowledge` -> `supervisor` (finalizer) -> `resolved` 链路，验证引用完整性与步数限制。
- Documentation accuracy: 图结构与调度逻辑完全符合架构契约与 AGENTS.md 规范。

## Remaining gaps

- None for Day 03. Ready for Day 04 Worker exclusive execution and checkpoint recovery.
