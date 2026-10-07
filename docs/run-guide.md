# AgentDesk 本地运行指南

本文说明开发模式和 Docker Compose 全栈两种启动方式。当前代码按 FastAPI API + ARQ Worker + PostgreSQL + Redis + Vue 前端运行；工单图只由 Worker 执行。

## 运行前准备

- Python 3.11 或更高版本与 `uv`
- Node.js 22.22.2（版本记录在根目录 `.nvmrc`）与 npm
- Docker Desktop（Compose 全栈方式）
- API 与 Worker 共用的 PostgreSQL、Redis
- Worker 处理真实模型请求还需要 OpenAI-compatible 接口及模型配置

先在仓库根目录执行：

```powershell
uv sync --all-extras --dev
Copy-Item .env.example .env
```

编辑 `.env`，设置至少 32 字节的随机 `JWT_SECRET`、有效 `APP_ENCRYPTION_KEY`、模型服务的 `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_KEY`。本地开发时将数据库地址设为 `postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk`，Redis 地址设为 `redis://localhost:6379/0`。不要将 `.env` 提交到版本库。

## 方式一：Docker Compose 全栈

Compose 会启动 PostgreSQL、Redis、FastAPI、ARQ Worker 与 Nginx 前端。Compose 内 API/Worker 使用服务名访问依赖，连接地址应为 `postgres` 和 `redis`，不要改成 `localhost`。

```powershell
docker compose up -d postgres redis
uv run alembic upgrade head
uv run python scripts/maintenance/seed_demo_data.py --reset-demo-data
docker compose up --build -d
docker compose ps
```

迁移命令使用主机侧 `.env` 中的 `DATABASE_URL`，所以这里应配置主机可访问的 `localhost:5432` 地址。种子脚本带有重置语义，会重建模拟演示数据；只对本地演示库运行。Compose 首次启动或修改镜像后使用 `docker compose up --build -d`。

访问：

- 前端：<http://localhost/>
- API 健康检查：<http://localhost/health>
- API 就绪检查：<http://localhost/ready>
- OpenAPI：<http://localhost/docs>

查看服务日志：`docker compose logs -f api worker frontend`。停止服务：`docker compose down`。保留数据库数据卷；只有明确需要清空本机演示数据时才运行 `docker compose down -v`。

## 方式二：本机开发服务

先启动 PostgreSQL 和 Redis（可以用 Compose 只启动这两个依赖），执行数据库迁移与模拟数据初始化：

```powershell
docker compose up -d postgres redis
uv run alembic upgrade head
uv run python scripts/maintenance/seed_demo_data.py --reset-demo-data
```

在三个独立终端、仓库根目录分别启动 API、Worker 和 Vue 开发服务器：

```powershell
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
uv run arq backend.jobs.worker.WorkerSettings
```

```powershell
Set-Location frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Vite 将 `/api` 请求代理到 `http://127.0.0.1:8000`。开发页面地址为 <http://localhost:5173/>。API 文档为 <http://localhost:8000/docs>。

## 演示账号与检查

初始化脚本创建模拟账号：`requester` / `requester-pass`、`requester2` / `requester2-pass` 和 `approver` / `approver-pass`。这些仅供本地模拟数据使用，不能用于生产环境。

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
```

`/health` 检查 API 进程；`/ready` 还检查 JWT 配置、PostgreSQL 与 Redis。真实工单处理依赖 Worker 与模型服务；没有设置模型时 Worker 会拒绝启动。前端页面能打开不代表 API/Worker 或模型已就绪。

## 常见问题

- Docker 命令提示无法连接引擎：启动 Docker Desktop，待 Linux Engine 就绪后再运行 Compose。
- 迁移或 API 报数据库连接失败：确认 PostgreSQL 健康，且本机 `.env` 的数据库主机是 `localhost`。
- `/ready` 返回 503：查看响应错误信息，并检查 `JWT_SECRET`、PostgreSQL 与 Redis。
- Worker 提示 `OPENAI_MODEL must be set`：在 `.env` 设置模型名、兼容接口地址和对应 API Key；连接本机 Ollama 的 Compose 容器时，地址使用 `http://host.docker.internal:11434/v1`。
- 端口冲突：本地默认使用 80（Compose 前端）、8000（API）、5173（Vite）、5432（PostgreSQL）和 6379（Redis）；先停止占用服务或调整 Compose/Vite 配置。

## 停止本机开发进程

在各终端按 `Ctrl+C` 停止 API、Worker 和 Vite。若用 Compose 启动依赖，可执行 `docker compose stop postgres redis`；需要停止全栈则使用 `docker compose down`。
