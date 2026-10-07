# Day 05 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: 工单查询与脱敏 Trace API
- Commit SHA: `a654b1c`
- Changed files:
  - `backend/api/schemas.py`
  - `backend/api/tickets.py`
  - `backend/db/repositories/tickets.py`
  - `specs/openapi/openapi.json`
  - `frontend/src/types/api.d.ts`
  - `tests/integration/test_ticket_queries.py`
  - `tests/integration/test_trace_api.py`
  - `docs/evidence/l3/day-05.md`
- Contract/permission/state/eval impact:
  - `GET /api/tickets` 实现 requester 隔离查询列表，按更新时间倒序排序。
  - `GET /api/tickets/{ticket_id}` 实现单个工单详情查询，越权访问或不存在均统一返回 404（防止探测存在性）；返回结果携带 `knowledge_refs` 包含完整 5 个引用证据字段（`document_id`, `chunk_id`, `title`, `content_excerpt`, `score`）。
  - `GET /api/tickets/{ticket_id}/trace` 合并数据库 `TraceEventRecord` 与 `ToolCallRecord`，按时间戳正序排列，并严格脱敏参数及结果中的敏感键（password, secret, token, key 等），越权同样返回 404。
  - 通过 `scripts/export_openapi.py` 导出最新 OpenAPI 规范至 `specs/openapi/openapi.json`，并通过 `npm run generate:api-types` 重新生成前端 TypeScript 类型 `frontend/src/types/api.d.ts`，验证零 drift。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run pytest tests/integration/test_ticket_queries.py tests/integration/test_trace_api.py -q` | 0 | `2 passed in 1.96s` |
| `uv run python scripts/export_openapi.py --check` | 0 | `OpenAPI snapshot is current` |
| `uv run pytest tests/unit/test_openapi_contract.py -q` | 0 | `7 passed in 1.23s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 59 source files` |
| `npx tsc --noEmit` (frontend) | 0 | `Clean compile with no errors` |
| `uv run pytest tests/unit tests/integration -q` | 0 | `195 passed in 15.47s` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Requester Data Isolation | Approver receives 404 (not 403) when requesting requester's ticket or trace, preventing ticket existence probing | `tests/integration/test_ticket_queries.py`, `tests/integration/test_trace_api.py` |
| Trace Event Ordering & Redaction | Unified trace events returned in chronological order; sensitive values (passwords, tokens, secret fields) masked/redacted | `tests/integration/test_trace_api.py` |
| Citations Structure | Knowledge references contain full 5-field schema (`document_id`, `chunk_id`, `title`, `content_excerpt`, `score`) | `tests/integration/test_ticket_queries.py` |

## Human review

- Authorization boundary: 确认只有工单所有者能够查询到其本人工单及 trace，非本人访问均返回统一 404 错误响应。
- Sensitive-data review: Trace API 输出中的 args_summary 与 result_summary 针对敏感键已做掩码处理，且测试验证了绝不泄露用户口令或 bearer token。
- Test-quality review: 集成测试基于隔离的 PostgreSQL 和真实数据模型，对详情字段、越权防探测、按时间正序、脱敏完整性均有精确断言。
- Documentation accuracy: OpenAPI 规范与前端 TypeScript 类型定义完全同步。

## Remaining gaps

- None for Day 05. Ready for Day 06 Frontend Login & Ticket Submission minimal UI.
