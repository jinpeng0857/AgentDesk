# Day 04 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Worker 独占执行与 PostgreSQL checkpoint 恢复
- Commit SHA: `1e5018b`
- Changed files:
  - `backend/jobs/context.py`
  - `backend/jobs/tickets.py`
  - `backend/jobs/worker.py`
  - `backend/Dockerfile`
  - `docker-compose.yml`
  - `backend/graph/edges.py`
  - `tests/integration/conftest.py`
  - `tests/integration/test_worker_recovery.py`
  - `tests/unit/test_dependency_boundaries.py`
  - `scripts/setup_wsl_services.sh`
  - `docs/evidence/l3/day-04.md`
- Contract/permission/state/eval impact:
  - Worker 是启动与恢复 LangGraph 的唯一进程入口；API 模块零 Graph 引用（经 AST 依赖扫描测试保障）。
  - `GraphTicketRunner.run(ticket_id)` 实现确定性图执行：已有 checkpoint 时传递 `input_state = None` 恢复断点，终态（`resolved`, `escalated`, `failed`）直接返回。
  - 使用长生命周期 psycopg pool（`autocommit=True`, `prepare_threshold=0`）组装 `AsyncPostgresSaver`，运行时零 DDL 建表。
  - 故障恢复测试：在 Supervisor checkpoint 后注入 Knowledge 节点故障，重启 Worker 并换用新连接池/saver 后成功恢复。断言 Supervisor 分类步骤不被重复执行，且全程零写操作副作用。
  - Docker Compose 增加 `api` 与 `worker` 服务，共享同一 `backend/Dockerfile`，以独立命令启动并依赖 PostgreSQL 与 Redis 健康检查。
  - 任务调度 payload 严格为纯 ID，`start_job_id(ticket_id)` 保持 `start:{ticket_id}` 确定性。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run pytest tests/integration/test_worker_recovery.py -q` | 0 | `1 passed in 2.22s` |
| `uv run pytest tests/unit tests/integration/test_graph_read_only.py tests/integration/test_worker_recovery.py tests/integration/test_migrations.py tests/integration/test_auth_api.py -q` | 0 | `155 passed in 4.82s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 59 source files` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| PostgreSQL Checkpoint Recovery with fixed failure point | Supervisor checkpointed, worker restarted with fresh pool, resumed with input=None, Supervisor not repeated, final status resolved, 0 write calls | `tests/integration/test_worker_recovery.py` |
| API & Worker Container Architecture | Compose config verified: 4 services, healthy dependencies, separate commands | `docker-compose.yml`, `backend/Dockerfile`, `tests/unit/test_dependency_boundaries.py` |

## Human review

- Authorization boundary: 确认 API 层与 Worker 层的进程边界，API 层不 import Graph，Worker 是唯一图引擎入口；Supervisor 绝不调用 Tool。
- Sensitive-data review: 数据库 checkpoint blob 与 projection 中无明文密钥或未脱敏敏感凭据。
- Test-quality review: 故障注入测试真实模拟了跨进程重启与断点续跑，严格校验了 Supervisor 分类不重复运行这一幂等关键属性。
- Documentation accuracy: 完全符合 `AGENTS.md` 进程边界与恢复设计。

## Remaining gaps

- None for Day 04. Ready for Day 05 Ticket Query and Redacted Trace API.
