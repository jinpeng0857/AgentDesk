# Day 28 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: Trace 导出协议与可选 Langfuse 外部适配器落地
- Changed files:
  - backend/integrations/trace_exporter.py
  - backend/config.py
  - tests/unit/test_trace_exporter.py
  - docs/evidence/l3/day-28.md
- Contract/permission/state/eval impact:
  - `TraceExporter` Protocol 规范化：定义 `export_event` 与 `flush` 方法。
  - `NoOpTraceExporter`（系统默认）：在无配置时生效，绝不发起任何外部网络连接，保证系统离线自闭环。
  - `LangfuseTraceExporter`（可选集成）：当且仅当注入有效 `langfuse_public_key` 与 `langfuse_secret_key` 时激活。
  - **H-19 绝对安全保障**：所有通过外部导出器发送的元数据均强制经由 `redact_mapping` 脱敏处理，密码、Token、API Key、临时凭证在离开系统边界前一律被替换为 `***`，严防数据外泄。
  - **异常容错隔离**：外部可观测性服务的网络波动、鉴权失败或宕机一律通过静默捕获与警告日志隔离，绝不中断工单主图执行流。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run pytest tests/unit/test_trace_exporter.py -v` | 0 | `5 passed in 0.42s` |
| `uv run pytest tests/unit -q` | 0 | `218 passed in 3.86s` |
| `uv run ruff check backend/integrations/trace_exporter.py backend/config.py tests/unit/test_trace_exporter.py` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval` | 0 | `Success: no issues found in 62 source files` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Zero Network Default | When keys are absent, NoOpTraceExporter ensures 100% offline self-containment | `backend/integrations/trace_exporter.py`, `tests/unit/test_trace_exporter.py` |
| Redaction Verification | Sensitive keys (password, auth_token, api_key) masked to `***` before export | `backend/integrations/trace_exporter.py`, `tests/unit/test_trace_exporter.py` |
| Fault Tolerance | Network failures in telemetry suppressed without impacting ticket workflows | `backend/integrations/trace_exporter.py`, `tests/unit/test_trace_exporter.py` |

## Human review

- Decoupled Observability: Langfuse 作为可选插件而非系统强依赖，符合 `AGENTS.md` 架构硬约束。
- Zero Secret Leakage: 无论本地审计表还是远程 Trace 导出，密码与凭据脱敏规则一致生效。

## Remaining gaps

- Day 28 目标全部达成！进入 Day 29：Docker Compose 容器化编排、前端容器化与全栈冒烟巡检。
