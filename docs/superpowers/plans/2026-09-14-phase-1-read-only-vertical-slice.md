# Phase 1 Read-Only Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付第一条真实可运行的只读工单纵切：认证用户提交网络问题后，API 以 HTTP 202 接收，ARQ Worker 从 PostgreSQL outbox 获取任务并运行 PostgreSQL-backed LangGraph，最终返回带可回查知识引用的 `resolved` 工单。

**Architecture:** 延续已批准的模块化单体与独立 Worker 方案。FastAPI 只负责认证、事务写入初始工单/outbox 和查询 projection；Worker 是唯一图运行者。领域层只依赖 Protocol，PostgreSQL、Redis、Chroma 和 OpenAI-compatible API 都由 `backend/` Adapter 注入；Phase 1 的执行网关只允许四个 read Tool，四个 write Tool 在调用实现前确定性拒绝。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic v2、SQLAlchemy 2 async、asyncpg、Alembic、PostgreSQL 17、Redis 7、ARQ、LangGraph 1.x、langgraph-checkpoint-postgres、Chroma 1.x、OpenAI-compatible API、PyJWT、pwdlib/Argon2、Vue 3、Pinia、Vue Router、TypeScript、Vitest、Docker Compose。

## Global Constraints

- `AGENTS.md` v2.0 是最高约束；本计划不得修改它，发现冲突时停止对应工作并请求人工确认。
- 固定 `supervisor`、`knowledge`、`action`、`policy` 四个 Agent；Worker、Dispatcher 和 Adapter 都不是 Agent。
- Tool Registry 仍固定八项；Phase 1 唯一可执行集合是 `search_knowledge`、`query_user`、`query_order`、`check_service`。
- `create_ticket`、`request_access`、`reset_password`、`process_refund` 只能保留 Schema，不得绑定可产生副作用的实现。
- API 可以创建 AgentDesk 自身的工单和 outbox 记录；“写操作为零”特指四个业务 write Tool 的副作用次数必须为零。
- API 不运行 Agent 图；只有 ARQ Worker 可以创建、启动或恢复 LangGraph。
- PostgreSQL 是工单、outbox、Trace、Tool 审计和 checkpoint 的事实源；Redis 只承载队列与短期协调。
- 所有数据库 Schema 由 Alembic 管理。应用、API、Worker 和测试导入时不得调用 `create_all()` 或 `AsyncPostgresSaver.setup()`。
- ARQ 必须显式使用 JSON serializer/deserializer；队列参数只含 `ticket_id`，不得使用 pickle。
- `start_ticket` 的 `_job_id` 必须严格等于 `start:{ticket_id}`；重复发布或执行不得重新运行终态工单。
- `thread_id` 必须等于 `ticket_id`；LangGraph checkpoint 必须使用同一 `thread_id` 恢复。
- Dispatcher 是唯一可以写 `status`、`agent_history` 和 `step_count` 的代码；达到 `MAX_STEPS = 15` 后禁止继续调用模型或 Tool。
- Agent 节点只返回 `AGENTS.md` §2.1 中归属自己的字段；不得向 `TicketState` 塞入临时键。
- 模型默认使用 OpenAI-compatible API；Agent 不得实例化 SDK，测试和离线 CI 必须注入确定性 `FakeModelClient`。
- Knowledge 引用必须包含 `document_id`、`chunk_id`、`title`、`content_excerpt`、`score`，并持久化到可查询 projection。
- 每次 read Tool 尝试都必须写 `ToolCallRecord`；参数错误、拒绝、超时和成功都要审计，摘要必须脱敏。
- Prompt、Agent、Tool、Graph 行为合并前必须生成可追溯 live eval 前后产物；Phase 1 冒烟结果不得转写成简历百分比。
- Live eval 只能在工作区干净的提交上运行，artifact 中的 Git SHA 必须等于被评估代码的 `HEAD`。
- API Schema 变化后必须重新导出 `specs/openapi/openapi.json` 并生成 `frontend/src/types/api.d.ts`，禁止手写 API DTO。
- 每个子步遵循 red → green → refactor：先写确定性失败测试，确认失败原因，再做最小实现和提交。

---

## Decision Review

### 采用方案：模块化单体 + 独立 Worker

API、Worker、领域模块共享一个代码仓库和数据库契约，但运行在不同进程。API 用事务 outbox 接受请求，Worker 通过 ARQ 执行 LangGraph。该方案与 checkpoint、跨请求恢复、HTTP 202 和作品集可展示性直接匹配。

### 不采用：FastAPI `BackgroundTasks`

后台任务跟随 API 进程生命周期，无法提供可靠投递、独立重启或跨进程 checkpoint 恢复，也会违反“API 不运行 Agent 图”。

### 不采用：每个 Agent 独立服务

Phase 1 的难点是状态、幂等边界和可恢复执行，不是独立扩缩容。拆成多个服务会增加网络协议、部署和一致性成本，并违反项目负面清单。

---

## Current-State Evidence

- `main` 已包含 Phase 0，当前 HEAD 基线为 `7feace4`。
- Pydantic 领域 Schema、四份 Prompt、四份 Agent contract、八个 Tool Schema 和 OpenAPI 草案已经存在。
- `backend/api/auth.py` 与 `backend/api/tickets.py` 仍返回稳定 501；`backend/main.py` 仍标记 `contract-only`。
- PostgreSQL/Redis Compose 已可健康启动，但尚无业务表、migration、API/Worker 服务。
- 当前实测有 17 个受 Git 跟踪的空文件。Phase 1 填写四个 Agent、两个 Graph、三个 Tool、read audit 和三个 CI workflow；`backend/governance/idempotency.py` 与 Password Reset Skill 三个文件仍属于 Phase 2。
- `backend/graph/state.py`、`backend/graph/builder.py`、`backend/db/`、`backend/jobs/`、`backend/auth/` 和 `backend/integrations/` 需要在本阶段创建。

---

## File and Interface Map

| 单元 | 主要文件 | 单一职责 |
|---|---|---|
| 配置 | `backend/config.py` | 无副作用地解析环境配置并执行 readiness 校验 |
| 持久化 | `backend/db/base.py`、`models.py`、`session.py` | SQLAlchemy metadata、ORM 模型、async session factory |
| Repository | `backend/db/repositories/*.py` | 用户、工单 projection、outbox、Trace、Tool 审计的事务接口 |
| Migration | `alembic/`、`alembic.ini` | 从空 PostgreSQL 创建业务表和 LangGraph checkpoint 表 |
| 认证 | `backend/auth/passwords.py`、`jwt.py`、`dependencies.py` | Argon2 校验、JWT 签发/解析、当前身份依赖 |
| 队列 | `backend/jobs/serialization.py`、`outbox.py`、`tickets.py`、`worker.py` | JSON ARQ、outbox 发布、工单 Job、Worker 生命周期 |
| 模型 | `backend/integrations/model_client.py`、`prompt_loader.py` | OpenAI-compatible 结构化输出和 Prompt 加载 |
| Tool Adapter | `backend/integrations/tool_adapters.py`、`knowledge_store.py` | 实现八个领域 Protocol，其中四个写 Protocol 明确拒绝 |
| 只读网关 | `backend/governance/executor.py`、`audit.py` | Schema、白名单、只读能力门、超时、脱敏、审计 |
| Agent | `agents/{supervisor,knowledge,action,policy}.py` | 结构化模型调用与纯只读 Policy 判定 |
| Graph | `backend/graph/state.py`、`context.py`、`nodes.py`、`edges.py`、`builder.py` | 状态初始化、注入、节点、确定性路由、图编译 |
| API | `backend/api/auth.py`、`tickets.py`、`health.py`、`dependencies.py` | 真实认证、HTTP 202、projection 查询、readiness |
| 前端 | `frontend/src/api/`、`stores/`、`views/`、`router/` | 内存 JWT、登录、工单提交/列表/详情和轮询 |
| 评估 | `eval/run_eval.py`、`eval/artifacts/` | Phase 1 live 前后轨迹产物，不更新人工 baseline |

核心接口在各任务中冻结；后续任务不得自行改名。

---

## Task 1: Persistent Foundation and Alembic Migration

**Files:**

- Create: `backend/config.py`
- Create: `backend/db/base.py`
- Create: `backend/db/models.py`
- Create: `backend/db/session.py`
- Create: `backend/db/repositories/users.py`
- Create: `backend/db/repositories/tickets.py`
- Create: `backend/db/repositories/outbox.py`
- Create: `backend/db/repositories/audit.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/20260914_0001_phase1_foundation.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/unit/test_settings.py`
- Test: `tests/integration/conftest.py`
- Test: `tests/integration/test_migrations.py`

**Interfaces:**

- Produces: `AppSettings`, `create_async_engine_from_settings(settings)`, `async_session_factory(engine)`.
- Produces: `UserRepository`, `TicketRepository`, `OutboxRepository`, `AuditRepository` backend classes; these are infrastructure implementations, not new domain Protocols.
- Produces tables: `users`, `orders`, `service_statuses`, `tickets`, `ticket_knowledge_refs`, `trace_events`, `tool_call_records`, `job_outbox`, `checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`.
- `tickets.thread_id == tickets.id` and `job_outbox.dedupe_key` is unique.

- [ ] **Step 1: Write configuration and import-safety failures**

```python
def test_settings_normalize_checkpoint_url() -> None:
    settings = AppSettings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/agentdesk_test",
        redis_url="redis://localhost:6379/15",
        jwt_secret="test-secret-with-at-least-32-bytes",
    )
    assert settings.checkpoint_database_url == (
        "postgresql://u:p@localhost:5432/agentdesk_test"
    )


def test_importing_db_session_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncpg, "connect", _fail_if_called)
    importlib.import_module("backend.db.session")
```

- [ ] **Step 2: Write the empty-database migration test**

The integration fixture must reject destructive setup unless the database name ends with `_test`. It drops and recreates only that dedicated test database, then invokes Alembic as a subprocess.

```python
async def test_upgrade_head_builds_business_and_checkpoint_schema(
    migrated_connection: AsyncConnection,
) -> None:
    tables = await table_names(migrated_connection)
    assert {
        "users", "orders", "service_statuses", "tickets",
        "ticket_knowledge_refs", "trace_events", "tool_call_records",
        "job_outbox", "checkpoint_migrations", "checkpoints",
        "checkpoint_blobs", "checkpoint_writes",
    } <= tables
    version = await migrated_connection.scalar(
        text("select max(v) from checkpoint_migrations")
    )
    assert version == len(BasePostgresSaver.MIGRATIONS) - 1
```

- [ ] **Step 3: Run the tests and confirm the intended failures**

```powershell
uv run pytest tests/unit/test_settings.py tests/integration/test_migrations.py -q
```

Expected: missing `backend.config` and Alembic configuration; not a Docker connection or unrelated import error.

- [ ] **Step 4: Implement settings and SQLAlchemy lifecycle**

`AppSettings` uses `pydantic-settings`, exposes UTC-safe configuration, and derives the psycopg URL without adding a second database secret:

```python
class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    database_url: str = "postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "agentdesk"
    jwt_audience: str = "agentdesk-api"
    jwt_ttl_s: int = 900
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = ""
    openai_timeout_s: float = 30.0
    openai_max_retries: int = 2
    chroma_path: Path = Path("data/chroma")

    @property
    def checkpoint_database_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
```

`backend/db/session.py` only defines factories; no engine is constructed at import.

Because runtime code directly imports `psycopg_pool`, add `psycopg[binary,pool]>=3.2,<4` as a direct dependency and refresh `uv.lock`; do not rely on a transitive package remaining available.

Add `JWT_ISSUER`, `JWT_AUDIENCE` and `JWT_TTL_S` to `.env.example`. `validate_readiness()` must reject an empty/placeholder JWT secret and an empty model name when the Worker is started; the module-level FastAPI app remains importable for OpenAPI generation without contacting infrastructure.

- [ ] **Step 5: Implement ORM models and repository transactions**

Use `Mapped[...]`, UTC-aware timestamps, string values plus `CheckConstraint` for domain enums, PostgreSQL `UUID` and `JSONB`, and named uniqueness constraints. `TicketRepository.sync_projection(state)` must upsert ticket fields, knowledge refs and newly observed `AgentStep` rows in one transaction.

```python
class TicketRecord(Base):
    __tablename__ = "tickets"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(36), unique=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    user_query: Mapped[str] = mapped_column(String(2_000))
    trace_id: Mapped[UUID] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    intent: Mapped[str | None] = mapped_column(String(32))
    resolution_mode: Mapped[str | None] = mapped_column(String(32))
    final_response: Mapped[str | None] = mapped_column(String(4_000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 6: Encode LangGraph checkpoint DDL in Alembic**

Copy the SQL represented by the installed `BasePostgresSaver.MIGRATIONS` into the revision. Apply concurrent indexes through Alembic `autocommit_block()`, insert each applied version into `checkpoint_migrations`, and never call `AsyncPostgresSaver.setup()` in runtime code. The integration assertion above is the dependency-upgrade drift alarm.

- [ ] **Step 7: Run migration and static gates**

```powershell
docker compose up -d postgres redis
$env:TEST_DATABASE_URL = "postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk_test"
uv run alembic upgrade head
uv run pytest tests/unit/test_settings.py tests/integration/test_migrations.py -q
uv run ruff check backend/config.py backend/db alembic tests/integration tests/unit/test_settings.py
uv run mypy backend/config.py backend/db
```

- [ ] **Step 8: Commit the persistent foundation**

```powershell
git add .env.example pyproject.toml uv.lock backend/config.py backend/db alembic.ini alembic tests/unit/test_settings.py tests/integration
git commit -m "feat(db): add phase one persistence foundation"
```

---

## Task 2: JWT Authentication and Real Auth API

**Files:**

- Create: `backend/auth/passwords.py`
- Create: `backend/auth/jwt.py`
- Create: `backend/auth/dependencies.py`
- Create: `backend/api/dependencies.py`
- Create: `backend/api/errors.py`
- Modify: `backend/api/auth.py`
- Modify: `backend/main.py`
- Modify: `backend/api/schemas.py`
- Modify: `scripts/export_openapi.py`
- Test: `tests/unit/test_auth.py`
- Test: `tests/integration/test_auth_api.py`
- Test: `tests/unit/test_openapi_contract.py`

**Interfaces:**

- Produces: `hash_password(password: SecretStr) -> str`, `verify_password(...) -> bool`.
- Produces: `issue_access_token(actor: ActorContext, settings: AppSettings) -> TokenResponse` and `decode_access_token(token, settings) -> ActorContext`.
- Produces FastAPI dependency `get_current_actor() -> ActorContext`.
- `/api/auth/login` and `/api/auth/me` become real; approval and eval endpoints remain 501.

- [ ] **Step 1: Write JWT and password failures**

```python
def test_expired_token_is_rejected(settings: AppSettings) -> None:
    token = issue_test_token(settings, expires_at=utc_now() - timedelta(seconds=1))
    with pytest.raises(AuthenticationError, match="TOKEN_EXPIRED"):
        decode_access_token(token, settings)


def test_password_hash_never_contains_plaintext() -> None:
    password = SecretStr("correct-horse-battery-staple")
    encoded = hash_password(password)
    assert password.get_secret_value() not in encoded
    assert verify_password(password, encoded)
```

- [ ] **Step 2: Write real API authentication tests**

Test valid login/me, wrong password, unknown username, expired token, invalid issuer/audience, inactive user and missing bearer token. Wrong username and wrong password must return the same `INVALID_CREDENTIALS` response.

```python
async def test_login_then_me_returns_fixed_actor(api_client: AsyncClient) -> None:
    login = await api_client.post(
        "/api/auth/login",
        json={"username": "requester", "password": "requester-pass"},
    )
    token = login.json()["access_token"]
    me = await api_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json() == {"user_id": "user-requester-001", "role": "requester"}
```

- [ ] **Step 3: Confirm the tests fail because routes are still stubs**

```powershell
uv run pytest tests/unit/test_auth.py tests/integration/test_auth_api.py -q
```

- [ ] **Step 4: Implement Argon2 and strict JWT claims**

Use `pwdlib.PasswordHash.recommended()` and PyJWT `HS256`. Required claims are `sub`, `role`, `iss`, `aud`, `iat`, `exp`, `jti`; decoded roles are restricted to `requester | approver`. Never log the password, token or JWT secret.

- [ ] **Step 5: Replace only the auth stubs**

`login` loads the user by username through `UserRepository`, verifies `active` and the password hash, then issues a token. `current_actor` resolves the token and rechecks that the user still exists and is active. Use `ApiError` with the existing `ErrorEnvelope` and a per-request `request_id`.

- [ ] **Step 6: Preserve import safety with an app factory**

```python
def create_app(settings: AppSettings | None = None) -> FastAPI:
    resolved = settings or AppSettings()
    app = FastAPI(title="AgentDesk API", version="0.3.0")
    app.state.settings = resolved
    app.include_router(auth.router)
    # Other routers remain registered.
    return app


app = create_app()
```

The lifespan may open/close pools when the process starts; importing `backend.main` must not connect to PostgreSQL, Redis or OpenAI.

- [ ] **Step 7: Regenerate and verify OpenAPI**

```powershell
uv run python scripts/export_openapi.py
Set-Location frontend
npm run generate:api-types
Set-Location ..
uv run pytest tests/unit/test_auth.py tests/integration/test_auth_api.py tests/unit/test_openapi_contract.py -q
git diff --check
```

Update the OpenAPI status extension to `phase-1-partial`; the tests must assert auth no longer advertises 501 while approvals/eval still do.

- [ ] **Step 8: Commit authentication**

```powershell
git add backend/auth backend/api backend/main.py scripts/export_openapi.py specs/openapi/openapi.json frontend/src/types/api.d.ts tests
git commit -m "feat(auth): implement jwt authentication"
```

---

## Task 3: JSON ARQ Queue, Transactional Outbox, and HTTP 202

**Files:**

- Create: `backend/jobs/serialization.py`
- Create: `backend/jobs/context.py`
- Create: `backend/jobs/outbox.py`
- Create: `backend/jobs/tickets.py`
- Create: `backend/jobs/worker.py`
- Modify: `backend/db/repositories/outbox.py`
- Modify: `backend/db/repositories/tickets.py`
- Modify: `backend/api/tickets.py`
- Modify: `backend/api/health.py`
- Modify: `backend/main.py`
- Test: `tests/unit/test_job_serialization.py`
- Test: `tests/unit/test_job_ids.py`
- Test: `tests/integration/test_ticket_submission.py`
- Test: `tests/integration/test_outbox_dispatch.py`

**Interfaces:**

- Produces: `json_job_serializer(value: object) -> bytes` and `json_job_deserializer(payload: bytes) -> object`.
- Produces: `start_job_id(ticket_id: str) -> str`, exactly `start:{ticket_id}`.
- Produces: `dispatch_outbox(ctx: WorkerContext) -> int` and `start_ticket(ctx, ticket_id) -> None`.
- `POST /api/tickets` writes `tickets + job_outbox` in one transaction and returns 202; it never invokes `start_ticket` or the graph.

- [ ] **Step 1: Write JSON serialization and job-ID tests**

```python
def test_arq_payload_is_json_and_contains_only_ticket_id() -> None:
    payload = json_job_serializer(
        {"f": "start_ticket", "a": ("ticket-123",), "k": {}, "t": None, "et": 1}
    )
    assert payload.startswith(b"{")
    assert b"pickle" not in payload
    assert json_job_deserializer(payload)["a"] == ["ticket-123"]


def test_start_job_id_is_deterministic() -> None:
    assert start_job_id("ticket-123") == "start:ticket-123"
```

- [ ] **Step 2: Write transaction and API-boundary failures**

Assert that a successful POST creates exactly one ticket and one outbox row, a forced outbox insert failure rolls back the ticket, repeated dispatch uses one logical ARQ job, and monkeypatching `backend.graph.builder` to fail does not affect the API request.

- [ ] **Step 3: Run the focused tests and confirm failure**

```powershell
uv run pytest tests/unit/test_job_serialization.py tests/unit/test_job_ids.py tests/integration/test_ticket_submission.py tests/integration/test_outbox_dispatch.py -q
```

- [ ] **Step 4: Implement strict JSON ARQ configuration**

`json_job_serializer` must reject unsupported values instead of using `default=str`. Both `create_pool(..., job_serializer=..., job_deserializer=...)` and `WorkerSettings.job_serializer/job_deserializer` use the same functions.

```python
def start_job_id(ticket_id: str) -> str:
    return f"start:{ticket_id}"


class WorkerSettings:
    functions = [start_ticket]
    cron_jobs = [cron(dispatch_outbox, second=set(range(0, 60, 5)), unique=True)]
    job_serializer = staticmethod(json_job_serializer)
    job_deserializer = staticmethod(json_job_deserializer)
    max_tries = 3
```

- [ ] **Step 5: Implement transaction outbox publishing**

`OutboxRepository.claim_batch()` uses `FOR UPDATE SKIP LOCKED`. For each `start_ticket` row call:

```python
await redis.enqueue_job(
    "start_ticket",
    str(row.ticket_id),
    _job_id=start_job_id(str(row.ticket_id)),
)
```

Mark `published_at` only after enqueue returns a Job or returns `None` for the same existing deterministic ID. On Redis failure, persist `attempts`, `last_error` and `next_attempt_at`; do not alter ticket status.

- [ ] **Step 6: Implement authenticated ticket submission**

Generate UUID `ticket_id` and `trace_id`, set `thread_id=str(ticket_id)`, status `queued`, and insert an outbox payload containing only `{"ticket_id": "..."}`. Ignore any requester identity outside the JWT. Return `TicketAcceptedResponse` with HTTP 202 after transaction commit.

- [ ] **Step 7: Implement readiness and an injectable Worker context**

`/ready` executes `SELECT 1`, Redis `PING`, and validates JWT configuration. `backend/jobs/context.py` defines a typed context containing the SQLAlchemy session factory, ARQ Redis pool and a `TicketRunner` Protocol; integration tests inject a deterministic fake runner. The real LangGraph runner and process containers are connected in Task 6, so this commit remains runnable on its own.

- [ ] **Step 8: Verify real PostgreSQL/Redis behavior and commit**

```powershell
docker compose up -d postgres redis
uv run pytest tests/unit/test_job_serialization.py tests/unit/test_job_ids.py tests/integration/test_ticket_submission.py tests/integration/test_outbox_dispatch.py -q
uv run ruff check backend/jobs backend/api backend/db tests
uv run mypy backend/jobs backend/api backend/db
git add backend/jobs backend/api backend/db tests
git commit -m "feat(jobs): add transactional arq outbox"
```

---

## Task 4: OpenAI-Compatible Model Adapter and Deterministic Fake

**Files:**

- Create: `backend/integrations/model_client.py`
- Create: `backend/integrations/prompt_loader.py`
- Create: `tests/fakes/model_client.py`
- Create: `tests/unit/test_model_client.py`
- Create: `tests/unit/test_prompt_loader.py`
- Create: `eval/run_eval.py`
- Create: `eval/artifacts/README.md`
- Modify: `.gitignore`

**Interfaces:**

- Produces: `OpenAICompatibleModelClient.complete_structured(...)` implementing `agents.context.ModelClient`.
- Produces: `FakeModelClient(responses: Sequence[BaseModel | Mapping[str, object] | Exception])` with a recorded call list.
- Produces: `load_prompt(agent_id: Literal[...]) -> LoadedPrompt`, including prompt version.
- Produces: `ModelTransportError` and `ModelOutputInvalid`; only transport errors are Worker-retryable.

- [ ] **Step 1: Write deterministic Fake tests**

```python
async def test_fake_returns_in_declared_order() -> None:
    fake = FakeModelClient([
        RoutingDecision(
            intent=Intent.NETWORK,
            next_agent="knowledge",
            confidence=0.95,
            escalate=False,
            reason_code=RoutingReason.NEEDS_KNOWLEDGE,
        )
    ])
    result = await fake.complete_structured(
        messages=[ModelMessage(role="user", content="VPN 无法连接")],
        output_model=RoutingDecision,
        config=model_config,
    )
    assert result.intent is Intent.NETWORK
    assert len(fake.calls) == 1
```

Also assert exhausted sequences fail deterministically and a response for the wrong Pydantic type is rejected.

- [ ] **Step 2: Write adapter tests with `httpx.MockTransport`**

Cover server-side JSON Schema, JSON-object fallback only on an explicit unsupported-format response, two repair attempts for malformed JSON, timeout classification, Pydantic extra-field rejection and absence of raw model output in exception messages.

- [ ] **Step 3: Confirm missing adapter failures**

```powershell
uv run pytest tests/unit/test_model_client.py tests/unit/test_prompt_loader.py -q
```

- [ ] **Step 4: Implement the OpenAI-compatible boundary**

Construct `AsyncOpenAI` only inside the Adapter constructor supplied by the Worker container. Submit `output_model.model_json_schema()` as strict `response_format`; parse returned content with `output_model.model_validate_json`. A provider 400 explicitly identifying unsupported `json_schema` may switch to `json_object`; authentication, rate-limit and server failures must retain their own classification.

Malformed structured content gets at most `config.max_retries` repair requests. The repair message identifies schema validation errors but never echoes secrets or an unbounded raw response.

- [ ] **Step 5: Implement strict Prompt loading**

Validate exactly `version`, `role`, `responsibility`, `constraints`, `output_schema`. Map each agent to one known file, reject path input, and render system content with clear delimiters separating untrusted user and retrieval text.

- [ ] **Step 6: Add the Phase 1 eval artifact writer**

`eval/run_eval.py` accepts `--mode offline|live`, `--stage model-baseline|e2e-candidate`, `--output`, and records dataset version, model, base URL host label, all Prompt versions, UTC time, Git SHA, token/cost fields when available, per-case structured output and failure code. It must not update `eval/baseline.json` and must not print percentages as project achievements.

- [ ] **Step 7: Run the unit gates and optional live baseline**

```powershell
uv run pytest tests/unit/test_model_client.py tests/unit/test_prompt_loader.py -q
uv run ruff check backend/integrations tests/fakes eval/run_eval.py
uv run mypy backend/integrations tests/fakes eval/run_eval.py
uv run python eval/run_eval.py --mode offline --stage model-baseline --output eval/artifacts/phase1-before.json
```

Before merging Phase 1, run the same stage with `--mode live` using the configured OpenAI-compatible endpoint. If credentials are absent, record the gate as blocked; do not fabricate an artifact.

- [ ] **Step 8: Commit the model boundary**

```powershell
git add .gitignore backend/integrations tests/fakes tests/unit eval/run_eval.py eval/artifacts/README.md
git commit -m "feat(agents): add openai compatible model boundary"
```

---

## Task 5: Read-Only Agents, Tools, Governance, and Eight Adapters

**Files:**

- Modify: `agents/supervisor.py`
- Modify: `agents/knowledge.py`
- Modify: `agents/action.py`
- Modify: `agents/policy.py`
- Modify: `agents/context.py`
- Modify: `tools/account.py`
- Modify: `tools/service.py`
- Modify: `tools/ticket.py`
- Modify: `tools/context.py`
- Create: `backend/governance/executor.py`
- Modify: `backend/governance/audit.py`
- Create: `backend/integrations/tool_adapters.py`
- Create: `backend/integrations/knowledge_store.py`
- Create: `backend/fixtures/knowledge.json`
- Create: `scripts/seed_demo_data.py`
- Test: `tests/unit/test_agents.py`
- Test: `tests/unit/test_read_executor.py`
- Test: `tests/unit/test_read_tools.py`
- Test: `tests/integration/test_tool_adapters.py`
- Test: `tests/integration/test_chroma_knowledge.py`

**Interfaces:**

- Produces: `run_supervisor(...) -> SupervisorOutput`, `ground_knowledge(...) -> KnowledgeAgentOutput`, `select_action(...) -> ActionProposal`.
- Produces: `assess_phase1_read(tool_name: ToolName) -> RiskDecision`; it raises `WriteCapabilityDisabled` for write Tool names and never pretends to implement Phase 2 risk rules.
- Produces domain functions `query_user`, `query_order`, `check_service`, `search_knowledge` that only depend on `tools.context` Protocols.
- Produces `ReadToolExecutor.execute(...) -> ToolResult` with Schema, whitelist, timeout, redaction and audit.
- Produces eight backend Adapter objects: four real read adapters and four fail-closed write adapters.

- [ ] **Step 1: Write Agent contract tests**

Assert Supervisor passes no Tool schemas, low confidence escalates, Knowledge cannot invent refs, Action sees only Phase 1 read schemas, and each function returns only its declared Pydantic output.

```python
async def test_action_receives_only_phase1_read_tools(fake_model: FakeModelClient) -> None:
    await select_action(state, model_client=fake_model, config=config)
    content = fake_model.calls[0].messages[-1].content
    assert "check_service" in content
    assert "process_refund" not in content
    assert "reset_password" not in content
```

- [ ] **Step 2: Write gateway zero-write tests**

Parameterize all four write Tool names. Assert the executor returns `REJECTED`, records one audit row and never invokes the corresponding Adapter method. Also test unknown Tool, wrong Agent whitelist, invalid args, timeout and successful read.

```python
@pytest.mark.parametrize(
    "tool_name", ["create_ticket", "request_access", "reset_password", "process_refund"]
)
async def test_phase1_rejects_every_write_before_implementation(tool_name: str) -> None:
    result = await executor.execute(agent_id="action", tool_name=tool_name, raw_args={})
    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == "WRITE_CAPABILITY_DISABLED"
    assert side_effect_spy.count == 0
```

- [ ] **Step 3: Write Adapter and Chroma failures**

Use real PostgreSQL fixture rows for user/order/service reads and a temporary Chroma directory for knowledge. Assert a VPN query returns the exact seeded `document_id`, `chunk_id`, title, excerpt and bounded score.

- [ ] **Step 4: Run focused tests and confirm failures**

```powershell
uv run pytest tests/unit/test_agents.py tests/unit/test_read_executor.py tests/unit/test_read_tools.py tests/integration/test_tool_adapters.py tests/integration/test_chroma_knowledge.py -q
```

- [ ] **Step 5: Implement domain functions without backend imports**

```python
async def query_user(args: QueryUserArgs, repository: UserRepository) -> QueryUserResult:
    return await repository.query_user(args)


async def search_knowledge(
    args: SearchKnowledgeArgs, store: KnowledgeStore
) -> SearchKnowledgeResult:
    return await store.search(args)
```

Implement equivalent wrappers for `query_order` and `check_service`. Do not define Phase 1 functions for the four write Tool implementations.

- [ ] **Step 6: Implement the read-only executor**

Execution order is Registry lookup → input Pydantic validation → Agent whitelist → Phase 1 read capability → `asyncio.timeout(spec.timeout_s)` → Tool function → output Pydantic validation → redaction → audit → `ToolResult`. The whitelist and read capability checks occur before resolving a callable. Audit both rejected and attempted calls.

- [ ] **Step 7: Implement all eight Protocol adapters**

`SqlUserRepository`, `SqlOrderRepository`, `SqlServiceRepository` and `ChromaKnowledgeStore` implement read behavior. `DisabledCredentialService`, `DisabledRefundService`, `DisabledTicketService` and `DisabledAccessRequestService` each raise `WriteCapabilityDisabled` immediately and contain no mutation code.

Use a deterministic, local hash embedding function for the small demo collection so tests and offline CI never download an embedding model. `asyncio.to_thread` wraps synchronous Chroma calls. Seed data uses invented identities and redacted documents only.

- [ ] **Step 8: Verify boundaries, seed deterministically, and commit the code**

```powershell
uv run python scripts/seed_demo_data.py --reset-demo-data
uv run pytest tests/unit/test_agents.py tests/unit/test_read_executor.py tests/unit/test_read_tools.py tests/integration/test_tool_adapters.py tests/integration/test_chroma_knowledge.py -q
uv run pytest tests/unit/test_dependency_boundaries.py -q
uv run ruff check agents tools backend/governance backend/integrations scripts tests
uv run mypy agents tools backend/governance backend/integrations scripts
git add agents tools backend/governance backend/integrations backend/fixtures scripts tests
git commit -m "feat(tools): implement governed read only capabilities"
```

- [ ] **Step 9: Capture the live pre-graph baseline on the clean commit**

```powershell
git status --short
uv run python eval/run_eval.py --mode live --stage model-baseline --output eval/artifacts/phase1-before.json
git add eval/artifacts/phase1-before.json
git commit -m "eval(eval): record phase one model baseline"
```

The runner must refuse a dirty worktree. If the OpenAI-compatible credentials are unavailable, stop Phase 1 execution here and request them; do not continue to graph integration while claiming the mandatory before/after gate can be reconstructed afterward.

---

## Task 6: LangGraph Runtime, Dispatcher, Worker Execution, and Recovery

**Files:**

- Create: `backend/graph/state.py`
- Create: `backend/graph/context.py`
- Modify: `backend/graph/nodes.py`
- Modify: `backend/graph/edges.py`
- Create: `backend/graph/builder.py`
- Modify: `backend/jobs/context.py`
- Modify: `backend/jobs/tickets.py`
- Modify: `backend/jobs/worker.py`
- Modify: `docker-compose.yml`
- Create: `backend/Dockerfile`
- Test: `tests/unit/test_routing.py`
- Test: `tests/unit/test_state_ownership.py`
- Test: `tests/integration/test_graph_read_only.py`
- Test: `tests/integration/test_worker_recovery.py`

**Interfaces:**

- Produces: `initial_ticket_state(record: TicketRecord) -> TicketState`.
- Produces: `advance_dispatch(state, *, actor, action) -> DispatcherUpdate` and route functions with no LLM/Tool calls.
- Produces: `build_graph(context: GraphContext, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph`.
- Produces: `TicketRunner.run(ticket_id: str) -> TicketStatus`; `start_ticket` delegates only to this runner.

- [ ] **Step 1: Write routing and ownership failures**

Cover every allowed transition, illegal targets, low confidence, terminal immutability and exact step 15 escalation. Node tests assert returned key sets:

```python
assert set(await supervisor_node(state)) <= {"intent", "routing_decision", "resolution_mode", "final_response"}
assert set(await knowledge_node(state)) == {"knowledge_refs"}
assert set(await action_node(state)) == {"tool_results"}
assert set(dispatch_update) <= {"status", "agent_history", "step_count"}
```

- [ ] **Step 2: Write the read-only graph test**

Use a deterministic Fake sequence: Supervisor routes to Knowledge, Knowledge returns a grounded result, Supervisor returns `FinalResponse(automated, ...)`. Assert final `resolved`, exact references, no pending operation, no risk/approval, no write Adapter calls and a bounded step count.

- [ ] **Step 3: Write checkpoint recovery with a fixed failure point**

Inject a failure on the first Knowledge model call after the Supervisor checkpoint. Close the first Worker context, create a new PostgreSQL connection pool/checkpointer and rerun the same `ticket_id`. Assert Supervisor classification is not repeated, the graph reaches `resolved`, projection remains queryable during failure, and write side-effect count remains zero.

- [ ] **Step 4: Confirm missing runtime failures**

```powershell
uv run pytest tests/unit/test_routing.py tests/unit/test_state_ownership.py tests/integration/test_graph_read_only.py tests/integration/test_worker_recovery.py -q
```

- [ ] **Step 5: Implement state initialization and deterministic Dispatcher**

`backend/graph/state.py` imports the canonical `TicketState` and named models; it does not redeclare fields. `initial_ticket_state` fills every field explicitly. `edges.py` defines `MAX_STEPS = 15`, allowed transitions and `advance_dispatch`; it never imports a model client or Tool callable.

- [ ] **Step 6: Implement nodes as dependency-injected closures**

`GraphContext` contains the ModelClient, immutable model config, Prompt loader, read executor and projection repository. Supervisor, Knowledge and Action nodes call domain functions; Dispatcher wrappers append the standardized `AgentStep`. Knowledge replaces its owned list with the old list plus validated new refs, preserving append-only behavior.

- [ ] **Step 7: Build and run the graph only in Worker**

Compile with `AsyncPostgresSaver` created from a long-lived psycopg pool using `autocommit=True` and `prepare_threshold=0`. Do not call `.setup()`. Add `api` and `worker` Compose services from the same backend image with different commands and PostgreSQL/Redis health dependencies. Run with:

```python
config = {"configurable": {"thread_id": ticket_id}}
async for state in graph.astream(input_state, config=config, stream_mode="values"):
    await ticket_repository.sync_projection(state)
```

On retry, load the latest checkpoint; pass `None` to resume when a checkpoint exists. Return immediately for terminal ticket status. Catch deterministic model/schema failures into `escalated`; raise only classified infrastructure failures for ARQ retry.

- [ ] **Step 8: Verify real recovery and commit**

```powershell
docker compose up -d postgres redis
uv run alembic upgrade head
uv run pytest tests/unit/test_routing.py tests/unit/test_state_ownership.py tests/integration/test_graph_read_only.py tests/integration/test_worker_recovery.py -q
uv run ruff check backend/graph backend/jobs agents tests
uv run mypy backend/graph backend/jobs agents
git add backend/graph backend/jobs backend/Dockerfile docker-compose.yml tests
git commit -m "feat(graph): run recoverable read only workflow"
```

---

## Task 7: Ticket Query and Trace API

**Files:**

- Modify: `backend/api/schemas.py`
- Modify: `backend/api/tickets.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/db/repositories/tickets.py`
- Modify: `backend/main.py`
- Modify: `scripts/export_openapi.py`
- Modify: `specs/openapi/openapi.json`
- Modify: `frontend/src/types/api.d.ts`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Test: `tests/integration/test_ticket_queries.py`
- Test: `tests/integration/test_trace_api.py`
- Test: `tests/unit/test_openapi_contract.py`

**Interfaces:**

- `GET /api/tickets` returns only the authenticated requester's tickets, newest first.
- `GET /api/tickets/{ticket_id}` returns the projection plus `knowledge_refs`.
- `GET /api/tickets/{ticket_id}/trace` returns redacted Agent/Dispatcher/Tool events in timestamp order.
- Produces `KnowledgeRefResponse`; fields exactly mirror the five required citation fields.

- [ ] **Step 1: Write ownership and citation API failures**

```python
async def test_ticket_detail_exposes_traceable_references(
    requester_client: AsyncClient, resolved_ticket: TicketRecord
) -> None:
    response = await requester_client.get(f"/api/tickets/{resolved_ticket.id}")
    assert response.status_code == 200
    assert set(response.json()["knowledge_refs"][0]) == {
        "document_id", "chunk_id", "title", "content_excerpt", "score"
    }
```

Also assert another requester receives 404 rather than ownership disclosure; unauthenticated requests receive 401; list order is deterministic; trace summaries never contain fixture passwords, bearer tokens or unmasked email.

- [ ] **Step 2: Run the API tests and confirm 501 failures**

```powershell
uv run pytest tests/integration/test_ticket_queries.py tests/integration/test_trace_api.py tests/unit/test_openapi_contract.py -q
```

- [ ] **Step 3: Extend the API DTO without exposing checkpoint data**

```python
class KnowledgeRefResponse(ApiModel):
    document_id: Identifier
    chunk_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content_excerpt: Annotated[str, Field(min_length=1, max_length=1_000)]
    score: float = Field(ge=0.0, le=1.0)


class TicketDetailResponse(TicketSummaryResponse):
    requester_id: Identifier
    resolution_mode: ResolutionMode | None
    final_response: Annotated[str, Field(max_length=4_000)] | None
    knowledge_refs: list[KnowledgeRefResponse]
```

- [ ] **Step 4: Implement projection queries and redacted trace composition**

Repositories accept `actor.user_id` in every ticket query. Trace composition merges persisted `trace_events` and `tool_call_records` through response mappers; it never reads arbitrary checkpoint blobs for API output.

- [ ] **Step 5: Regenerate contract and prove zero drift**

```powershell
uv run python scripts/export_openapi.py
Set-Location frontend
npm run generate:api-types
Set-Location ..
uv run python scripts/export_openapi.py --check
git diff --exit-code -- specs/openapi/openapi.json frontend/src/types/api.d.ts
```

- [ ] **Step 6: Run API, security and import-safety suites**

```powershell
uv run pytest tests/integration/test_ticket_queries.py tests/integration/test_trace_api.py tests/unit/test_openapi_contract.py tests/unit/test_dependency_boundaries.py -q
uv run ruff check backend/api backend/db scripts tests
uv run mypy backend/api backend/db scripts
```

- [ ] **Step 7: Synchronize current-state documentation**

README must state auth/ticket read endpoints are real, approvals/eval remain 501, business write Tools remain disabled, and frontend is not yet complete. Architecture §11 documents the projection and citation response; no Phase 2 capability is marked complete.

- [ ] **Step 8: Commit query APIs and generated types**

```powershell
git add backend/api backend/db scripts/export_openapi.py specs/openapi/openapi.json frontend/src/types/api.d.ts README.md docs/architecture.md tests
git commit -m "feat(api): expose ticket projections and traces"
```

---

## Task 8: Minimal Vue Slice, CI, Live Comparison, and Phase 1 Acceptance

**Files:**

- Create: `frontend/index.html`
- Create: `frontend/vite.config.ts`
- Create: `frontend/eslint.config.js`
- Create: `frontend/src/main.ts`
- Create: `frontend/src/App.vue`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/stores/auth.ts`
- Create: `frontend/src/stores/tickets.ts`
- Create: `frontend/src/router/index.ts`
- Create: `frontend/src/views/LoginView.vue`
- Create: `frontend/src/views/TicketsView.vue`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/views/LoginView.test.ts`
- Create: `frontend/src/stores/tickets.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `.github/workflows/backend-ci.yml`
- Modify: `.github/workflows/frontend-ci.yml`
- Modify: `.github/workflows/eval.yml`
- Create: `scripts/phase1_smoke.py`
- Modify: `eval/run_eval.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `PORTFOLIO.md`

**Interfaces:**

- Routes: `/login` and `/tickets`; no approval, trace-dashboard or eval pages in Phase 1.
- `ApiClient` receives a token getter and never persists JWT.
- `TicketStore.startPolling(ticketId)` polls every 2 seconds, pauses while `document.hidden`, backs off on network failure and stops on every terminal status.
- CI runs backend unit/integration/migration checks, frontend lint/type/test/build/schema drift, and offline eval; live eval is protected/manual and uploads artifacts.

- [ ] **Step 1: Add the frontend test dependency and write failing tests**

Add `@vue/test-utils` to dev dependencies using npm so the lockfile is authoritative.

```powershell
Set-Location frontend
npm install --save-dev @vue/test-utils
Set-Location ..
```

Tests must verify successful login stores the token only in Pinia memory, `localStorage` remains empty, ticket submit renders queued state, polling stops on `resolved`, and visibility-hidden pauses requests.

```typescript
expect(window.localStorage.length).toBe(0)
expect(fetchMock).toHaveBeenCalledTimes(2)
expect(store.activePolls.size).toBe(0)
```

- [ ] **Step 2: Confirm the frontend tests fail because the app files are absent**

```powershell
Set-Location frontend
npm run test
Set-Location ..
```

- [ ] **Step 3: Implement typed API and in-memory authentication**

`frontend/src/api/types.ts` may alias generated `components["schemas"]` types but may not redeclare fields. `ApiClient` maps non-2xx bodies through `ErrorEnvelope`, adds Bearer token when present, and never logs response bodies.

- [ ] **Step 4: Implement Login and Ticket views**

Login posts username/password, clears the password input immediately, stores JWT in Pinia memory and routes to `/tickets`. Ticket view provides one query textarea, submit button, current ticket status, final response, five-field citation cards and the authenticated user's ticket list. Use plain scoped CSS; do not introduce a design system.

- [ ] **Step 5: Implement robust polling**

Use a per-ticket `AbortController`, 2-second base interval and capped exponential network backoff. Listen to `visibilitychange`; abort all polling on logout/unmount and stop for `resolved`, `escalated`, `rejected`, `failed` or `expired`.

- [ ] **Step 6: Enable the three fixed CI workflows**

`backend-ci.yml` starts PostgreSQL/Redis services, runs `alembic upgrade head`, Ruff, mypy, unit/integration tests and generated-contract checks. `frontend-ci.yml` runs `npm ci`, lint, type-check, Vitest, build and OpenAPI type drift. `eval.yml` runs Fake Model smoke on PR and exposes a protected `workflow_dispatch` live job using repository secrets; ordinary CI never updates baseline.

- [ ] **Step 7: Create and run the automated Phase 1 smoke**

`scripts/phase1_smoke.py` logs in, submits a seeded VPN/SOP query, asserts HTTP 202, polls until `resolved`, verifies every citation has the five required fields, and exits nonzero on timeout or any business write audit.

```powershell
docker compose up -d --build postgres redis api worker
uv run alembic upgrade head
uv run python scripts/seed_demo_data.py --reset-demo-data
uv run python scripts/phase1_smoke.py
docker compose restart worker
uv run python scripts/phase1_smoke.py --assert-existing-ticket-queryable
```

- [ ] **Step 8: Commit the frontend, CI, smoke runner, and eval-runner changes**

```powershell
git add frontend .github/workflows scripts/phase1_smoke.py eval/run_eval.py
git commit -m "feat(frontend): deliver phase one read only slice"
git status --short
```

- [ ] **Step 9: Run the live candidate against the committed baseline**

Use the same OpenAI-compatible model/base URL, Prompt versions and dataset version recorded in `phase1-before.json`. The runner must reject unequal comparison metadata:

```powershell
uv run python eval/run_eval.py --mode live --stage e2e-candidate --compare eval/artifacts/phase1-before.json --output eval/artifacts/phase1-after.json
```

The comparison scores intent on all 20 reviewed cases and scores handoff/tool selection/task success only on the same read-only subset used by both artifacts. Write-case execution and write-tool selection are marked `out_of_scope_phase1` rather than changed to a false success. The report remains a smoke result. Missing credentials, unequal model configuration or missing metadata blocks merge.

- [ ] **Step 10: Run the complete acceptance gate**

```powershell
uv lock --check
uv run ruff check agents tools skills backend eval scripts tests
uv run mypy agents tools backend eval scripts
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run python scripts/export_tool_schemas.py --check
uv run python scripts/export_openapi.py --check
uv run python scripts/validate_agent_contracts.py
uv run python scripts/validate_golden_dataset.py

Set-Location frontend
npm ci
npm run lint
npm run generate:api-types
npm run type-check
npm run test
npm run build
Set-Location ..

git diff --exit-code -- specs/tool_schemas/tools.json specs/openapi/openapi.json frontend/src/types/api.d.ts
git diff --check
```

Acceptance evidence must show: real PostgreSQL/Redis integration, empty-database migration, 202/outbox/ARQ flow, Worker-only graph execution, checkpoint recovery, resolved read query, traceable references, zero business write side effects and `AGENTS.md` unchanged.

- [ ] **Step 11: Synchronize evidence without resume inflation**

README receives actual startup/test commands and precise endpoint status. Architecture §19 marks Phase 1 complete only after every command above passes. PORTFOLIO may mark orchestration/Worker/RAG/front-end rows “partially satisfied” with artifact links, but must not add a completed-project bullet or publish smoke percentages.

- [ ] **Step 12: Commit the verified artifact and evidence documents**

```powershell
git add eval/artifacts/phase1-after.json README.md docs/architecture.md PORTFOLIO.md
git commit -m "docs(docs): record phase one acceptance evidence"
```

- [ ] **Step 13: Record the final clean state**

```powershell
git status --short
git log --oneline --decorate -12
git rev-parse HEAD
docker compose down
```

Expected: clean worktree; containers removed without deleting PostgreSQL or Chroma volumes.

---

## Phase 1 Definition of Done

Phase 1 is complete only when all conditions hold simultaneously:

1. `alembic upgrade head` creates business and LangGraph checkpoint tables from an empty PostgreSQL database; no runtime path creates tables implicitly.
2. Login and `/me` use Argon2-backed users and validated JWT issuer/audience/expiry claims.
3. `POST /api/tickets` returns HTTP 202 after atomically storing ticket and outbox; API does not import or invoke graph execution.
4. ARQ payload is JSON, contains only IDs, and uses deterministic `start:{ticket_id}` job IDs.
5. Worker alone runs the graph with `thread_id=ticket_id`; a fixed crash test resumes from PostgreSQL checkpoint without repeating completed model steps.
6. Supervisor, Knowledge and Action complete a real read-only path; Dispatcher owns status/history/step count and stops at 15.
7. The four read Tools pass Schema, whitelist, timeout, redaction and audit; the four write Tools have zero side effects and fail before implementation lookup.
8. Chroma returns references with all five required fields, and ticket detail exposes those persisted references without exposing checkpoint blobs.
9. Ticket list/detail/trace enforce requester isolation and return stable typed errors.
10. Vue login and ticket views use generated OpenAPI types, keep JWT only in memory and stop/pause polling correctly.
11. Backend/frontend CI gates and offline Fake Model eval pass; protected live before/after artifacts contain dataset/model/Prompt/time/Git metadata.
12. README、architecture、PORTFOLIO 与实际能力一致，`AGENTS.md` 没有修改，且没有把 Phase 2/3 或冒烟指标写成已完成成果。

---

## Deferred Explicitly to Phase 2 or Phase 3

- 四个业务 write Tool 的真实实现、operation ledger、Redis execution lock、幂等键和加密结果。
- 完整 Policy 风险矩阵、退款阈值审批、Approval API、interrupt/resume 和审批过期。
- Password Reset Skill 与通知模板执行。
- F-01～F-08 完整失败模式矩阵；Phase 1 只实现固定 checkpoint recovery 和只读失败测试。
- 80+ 完整 Golden Dataset、人工 baseline、完整指标门禁、Trace/Eval 展示页、Nginx 与生产备份恢复。
