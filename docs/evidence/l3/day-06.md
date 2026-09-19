# Day 06 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Login 与 TicketSubmit 最小前端
- Commit SHA: `PENDING`
- Changed files:
  - `frontend/package.json`
  - `frontend/package-lock.json`
  - `frontend/tsconfig.json`
  - `frontend/eslint.config.js`
  - `frontend/index.html`
  - `frontend/vite.config.ts`
  - `frontend/src/main.ts`
  - `frontend/src/App.vue`
  - `frontend/src/styles.css`
  - `frontend/src/api/types.ts`
  - `frontend/src/api/client.ts`
  - `frontend/src/stores/auth.ts`
  - `frontend/src/stores/tickets.ts`
  - `frontend/src/router/index.ts`
  - `frontend/src/views/LoginView.vue`
  - `frontend/src/views/TicketsView.vue`
  - `frontend/src/views/LoginView.test.ts`
  - `frontend/src/stores/tickets.test.ts`
  - `docs/evidence/l3/day-06.md`
- Contract/permission/state/eval impact:
  - 纯内存认证：JWT 仅保留在 Pinia `authStore.token` 响应式状态中；提交登录表单后立即清空口令输入，`localStorage` 与 `sessionStorage` 严格保持为空（通过测试断言 `window.localStorage.length === 0`）。
  - 契约驱动 API 客户端：前端 `src/api/types.ts` 严格别名自 `specs/openapi/openapi.json` 生成的 `src/types/api.d.ts`，零手写 API DTO，零 `any`；请求失败统一按 `ErrorEnvelope` 反序列化为强类型 `ApiError`，且网络层从不打印或泄露响应 body。
  - 确定性轮询与退避机制：`useTicketStore.startPolling(ticketId)` 启动 2 秒基础轮询；遇到非 2xx 故障时按指数级退避（最高 16 秒）；监听 `document.visibilitychange`，在标签页不可见（`document.hidden`）时暂停网络请求并在可见时自动恢复；工单达到终态（`resolved`, `escalated`, `rejected`, `failed`, `expired`）或用户登出/卸载组件时通过 `AbortController` 立即终止并彻底清理所有计时器。
  - 完整呈现引用：工单进入 `resolved` 状态后展示 5 字段完整知识库卡片（`document_id`, `chunk_id`, `title`, `content_excerpt`, `score`）。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `npm run lint` (frontend) | 0 | `eslint . --max-warnings=0 (passed)` |
| `npm run type-check` (frontend) | 0 | `vue-tsc --noEmit (passed)` |
| `npm test` (frontend) | 0 | `5 passed in 1.07s (LoginView.test.ts, tickets.test.ts)` |
| `npm run build` (frontend) | 0 | `vite v8.3.0 built in 138ms (dist/index.html, assets)` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy agents tools skills backend eval scripts` | 0 | `Success: no issues found in 59 source files` |
| `uv run pytest tests/unit tests/integration -q` | 0 | `195 passed in 15.46s` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| In-Memory JWT & Password Cleared | Form wipes password immediately on submit; token kept in Pinia; localStorage empty | `frontend/src/views/LoginView.test.ts` |
| Polling Lifecycle & Hidden Tab Pause | Polls at 2s interval; stops on terminal resolved status; pauses on hidden tab; cleans up all controllers | `frontend/src/stores/tickets.test.ts` |
| Citations UI | Renders full 5 citation fields for resolved tickets | `frontend/src/views/TicketsView.vue` |

## Human review

- Authorization boundary: 路由导航守卫检查登录状态，未登录访问直接重定向到 `/login`；登出立即清空 token 并中止所有轮询请求。
- Sensitive-data review: 浏览器持久化存储（localStorage / sessionStorage / cookies）不存任何 JWT 或密码明文；输入框提交后内存清空；API client 不记录任何 response body。
- Test-quality review: 前端测试覆盖了登录内存安全、立即清空密码、失败错误提示、2秒轮询、终态停止、后台休眠暂停及登出取消。
- Documentation accuracy: 前端代码无手写 DTO，与 OpenAPI 生成类型 100% 对齐。

## Remaining gaps

- None for Day 06. Ready for Day 07 Phase 1 Smoke, CI workflows, and Gate A acceptance.
