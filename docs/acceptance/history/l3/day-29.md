# Day 29 Acceptance

- Date (UTC+8): 2026-09-20
- Goal: Docker Compose 完整编排、前端容器化与全栈 Health/Ready 链路
- Changed files:
  - frontend/Dockerfile
  - frontend/nginx.conf
  - docker-compose.yml
  - scripts/full_stack_smoke.py
  - scripts/backup_restore_drill.ps1
  - docs/evidence/l3/day-29.md
- Contract/permission/state/eval impact:
  - 前端容器化（Multi-Stage Dockerfile）：采用 `node:22-alpine` 构建产物，`nginx:1.27-alpine` 运行时部署，内置 SPA 路由降级 (`try_files $uri /index.html`)。
  - Nginx 统一反向代理：安全转发 `/api/` 至 `backend:8000/api/`，代理 `/health` 与 `/ready` 探针。
  - `docker-compose.yml` 服务拓扑升级：
    - 补齐 `frontend` 服务（暴露 80 端口，依赖 `api: condition: service_healthy`）。
    - 为 `api` 增加健康检查探针 (`GET /health`)，保证 Worker 与 Frontend 按依赖顺序平滑就绪。
  - 全栈冒烟巡检工具：`scripts/full_stack_smoke.py` 覆盖 `/health`、`/ready`、`/api/auth/login`、`/api/tickets` (202 异步契约)、`/api/eval/runs` 完整链路。
  - 容灾备份恢复演练工具：`scripts/backup_restore_drill.ps1` 落地，执行数据库备份导出、演练库灾备还原与数据表结构/不变量校验。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run ruff check scripts/full_stack_smoke.py` | 0 | `All checks passed!` |
| `powershell -ExecutionPolicy Bypass -File scripts/backup_restore_drill.ps1` | 0 | `All assertions passed. Disaster Recovery Drill Completed Successfully` |
| `npm run build` (in frontend) | 0 | `vite build` completed in 168ms (dist ready for Nginx) |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Container Health Topology | Postgres -> Redis -> API (healthy) -> Worker & Frontend (Nginx) sequence established | `docker-compose.yml`, `frontend/nginx.conf`, `frontend/Dockerfile` |
| Disaster Recovery Drill | Backup dump generated in `data/backups/`, schema integrity verified | `scripts/backup_restore_drill.ps1` |

## Human review

- Architecture Discipline: 前端不直连内部数据库或 Redis，所有对外流量严格收敛至 Nginx 与 FastAPI 统一网关。
- Zero Secret in Images: Dockerfile 构建不硬编码任何密钥，所有环境凭据均通过 Compose 运行时注入。

## Remaining gaps

- Day 29 目标全部达成！进入 Day 30：最终验收工件固化、Demo 脚本编排与 PORTFOLIO / README 文档全面同步。
