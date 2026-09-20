# AgentDesk 系统架构与设计

> 可靠多 Agent 工单自动化系统的可实施技术方案。
>
> 设计版本：v2.0
> 目标形态：作品集级生产化系统
> 当前状态：Phase 1、Phase 2 与 Phase 3 全量通过验收（Gate A / Gate B / Gate C All Passed）
> 上级开发约束：[`AGENTS.md`](../AGENTS.md)

---

## 目录

1. 项目定位
2. 任务分析与 Agent 必要性
3. 目标、范围与非目标
4. 总体架构
5. Agent Graph 与数据流
6. 状态与领域模型
7. Agent 设计
8. 模型与 Prompt
9. Tool Governance
10. Memory 与 Skill
11. API、Worker 与持久化
12. 安全、审计与可观测
13. 失败模式
14. Evaluation
15. 测试与 CI
16. 前端
17. 部署
18. 目标 Repo 结构
19. 开发计划与验收
20. 决策记录

---

## 1. 项目定位

AgentDesk 模拟企业 IT Service Desk：用户提交账号、网络、权限、订单或高风险操作相关工单，系统负责分类、检索、提出操作、执行策略判断、必要时等待人工审批，并提供可回查的结果和轨迹。

它不是企业工单平台，也不是把 RAG、Tool Calling 和多个聊天机器人简单拼在一起。它展示的是一套可验证的 Agent Workflow Runtime：

- 不可信模型输出如何进入确定性控制流；
- 有副作用的工具如何被授权、幂等和审计；
- 长时间人工审批如何暂停和恢复；
- 模型、Prompt 和图变化如何被评估门禁约束；
- 系统如何以独立 API/Worker 进程部署和恢复。

系统使用模拟企业数据和模拟业务 Tool，但可靠性与安全机制必须真实运行。

---

## 2. 任务分析与 Agent 必要性

### 2.1 业务任务

一张工单可能包含以下步骤：

1. 判断用户意图和信息是否充分；
2. 检索 SOP、FAQ 或历史案例；
3. 查询模拟账号、订单或服务状态；
4. 提出一个可能有副作用的操作；
5. 根据用户、资源、操作和金额确定是否允许；
6. 对高风险操作暂停并等待审批；
7. 恢复后保证同一操作只执行一次；
8. 汇总带证据的结果并记录完整轨迹。

### 2.2 为什么不是单 Agent

如果由一个 Agent 同时拥有检索、执行和授权权限，Prompt 注入或错误规划可以直接变成业务副作用，而且难以分别评估“分类错误”“工具错误”和“策略错误”。

本系统使用多 Agent 的理由是**权限和评估边界**，不是角色扮演：

| 边界 | 角色 |
|---|---|
| 理解与路由 | Supervisor |
| 只读检索与有引用回答 | Knowledge |
| 工具选择、参数提案和执行结果消费 | Action |
| 确定性授权与风险门禁 | Policy |

前三个角色需要语言理解能力；Policy 使用确定性规则完成最终判定，至多在判定后调用模型润色解释。Dispatcher、Worker 和 Approval Node 是运行组件，不是额外 Agent。

### 2.3 为什么使用 LangGraph

选择 LangGraph 不是因为框架流行，而是因为需求需要：

- 显式、可测试的有向图；
- thread_id 下的持久化 checkpoint；
- 动态 `interrupt` 和 `Command(resume=...)`；
- 节点边界故障恢复；
- 可观察的状态快照和轨迹。

普通顺序函数可以处理短流程，但无法自然表达跨分钟或小时的审批暂停。完全自治的 Agent 框架又会削弱路由和失败路径的确定性。

---

## 3. 目标、范围与非目标

### 3.1 MVP 必须交付

下表表示**纳入设计范围**，不表示当前已经实现：

| 能力 | 验收证据 |
|---|---|
| 五类工单意图 | Golden Cases + intent accuracy |
| 多 Agent 路由 | 图测试 + handoff accuracy |
| RAG 检索 | 可回查引用 + retrieval metrics |
| 八个模拟 Tool | Schema、白名单、执行测试 |
| 写操作治理 | Policy、幂等账本、审计 |
| 高风险审批 | interrupt、持久化、resume 测试 |
| 独立 Worker | HTTP 202、队列、重启恢复 |
| 轻量身份 | JWT、requester/approver、审批人追踪 |
| Trace 与结构化日志 | 脱敏轨迹和关联 ID |
| Evaluation | 离线冒烟、Live Eval、人工审查基线 |
| Web UI | 提交、审批、Trace、Eval 四页 |
| 一键部署 | Docker Compose 健康检查通过 |

### 3.2 明确不做

- 完整 RBAC、权限配置后台、组织树
- 多租户和真实企业 SSO
- 真实企业、支付、邮件或短信接口
- Kubernetes、高可用、多区域部署
- 每个 Agent 独立微服务
- 第五个 Agent 或第二个 Skill
- 团队记忆、用户画像、跨工单对话记忆
- WebSocket
- 审批时编辑操作参数
- 自定义前端设计系统
- 默认自托管完整 Langfuse 基础设施

### 3.3 上线含义

“可上线”限定为单主机 Docker Compose：可配置真实 OpenAI-compatible 模型，能持久化、重启恢复、审计和备份，允许小规模演示用户使用。它不承诺高可用、水平扩展或真实企业合规认证。

---

## 4. 总体架构

### 4.1 采用方案

采用**模块化单体代码库 + 独立 API/Worker 进程**：

```text
┌──────────────────────────────────────────────────────────┐
│ Browser                                                  │
│ Ticket | Approval | Trace | Eval                         │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTPS/HTTP
                           v
┌──────────────────────────────────────────────────────────┐
│ Nginx + Vue 3                                            │
│ SPA fallback + /api reverse proxy                        │
└──────────────────────────┬───────────────────────────────┘
                           v
┌──────────────────────────────────────────────────────────┐
│ FastAPI API                                              │
│ JWT | validation | ticket/approval query | outbox write │
└───────────────┬───────────────────────────┬──────────────┘
                │ PostgreSQL               │ Redis/ARQ
                v                          v
┌──────────────────────────────┐   ┌────────────────────────┐
│ PostgreSQL                   │   │ ARQ Worker             │
│ tickets / approvals          │<--│ outbox dispatch        │
│ tool ledger / audit          │   │ start/resume graph     │
│ LangGraph checkpoints        │   └───────────┬────────────┘
└──────────────────────────────┘               v
                                    ┌───────────────────────┐
                                    │ LangGraph             │
                                    │ S/K/A/P + approval    │
                                    └───────┬───────────────┘
                                            │ injected ports
                       ┌────────────────────┼───────────────────┐
                       v                    v                   v
                 OpenAI-compatible   Chroma Persistent     Simulated Tools
                 Model API           Knowledge Store       and repositories
```

### 4.2 为什么不拆 Agent 微服务

四个 Agent 共享同一工单状态、图生命周期和版本化契约。拆成微服务会引入分布式事务、跨服务 Trace、版本兼容和部署成本，但没有独立伸缩或团队边界收益。API/Worker 进程分离已经解决请求延迟与任务可靠性问题。

### 4.3 存储职责

| 组件 | 职责 | 不是 |
|---|---|---|
| PostgreSQL | 工单、身份、审批、审计、操作账本、checkpoint、eval run | 临时锁 |
| Redis | ARQ 队列、并发锁、短期缓存 | 最终幂等事实源 |
| Chroma | SOP、FAQ、脱敏历史案例向量检索 | 用户画像或流程状态 |
| Langfuse（可选） | 模型与节点 Trace | 安全审计事实源 |

---

## 5. Agent Graph 与数据流

### 5.1 允许的主路径

```text
START
  ↓
Supervisor classify
  ├─ confidence low / unsupported ─────────────→ Supervisor finalize(escalated)
  ├─ knowledge ─→ Knowledge ───────────────────→ Supervisor review
  └─ action ────→ Action
                    ├─ read Tool ─→ Gateway execute ─→ Supervisor review
                    └─ write proposal
                           ↓
                         Policy
                    ┌──────┼──────────────┐
                    │deny  │allow         │high
                    v      v              v
                 rejected Action execute Approval interrupt
                                │              │
                                v       approve/reject/expire
                         Supervisor review     │
                                     ┌─────────┴─────────┐
                                     v                   v
                               Action execute      rejected/expired
                                     │
                                     v
                              Supervisor finalize
```

### 5.2 Action 的两个阶段

同一个 Action Agent 在图中可以有两个节点包装，但不是两个 Agent：

- `action_propose`：模型选择一个允许 Tool，并产生结构化参数。读取 Tool 可直接进入网关；写 Tool 只生成 `PendingOperation`。
- `action_execute`：只消费已经通过 Policy、且必要时通过审批的 `PendingOperation`。它不得再次让模型修改工具名或参数。

### 5.3 写操作审批不变量

```text
approved.operation_id == pending_operation.operation_id
approved.args_hash == sha256(canonical_json(validated_args))
```

任何不匹配都进入 `rejected` 并写安全审计。审批结果只允许 approve/reject；参数变化必须重新提案。

### 5.4 Dispatcher

Dispatcher 位于 `backend/graph/edges.py`，只做确定性工作：

- 校验 `next_agent` 是否属于允许集合；
- 校验当前节点到目标节点的转移是否合法；
- 追加标准化 `AgentStep`；
- 增加 `step_count`；
- 达到 15 步时进入 `escalated`；
- 将运行状态映射为合法 `TicketStatus`。

Dispatcher 不调用模型、不执行 Tool，也不负责 Tool 白名单。

### 5.5 工单状态机

```text
queued → running
running → waiting_approval | resolved | escalated | rejected | failed
waiting_approval → running | rejected | expired
```

终态不可逆：`resolved`、`escalated`、`rejected`、`failed`、`expired`。重复 start/resume 必须返回已有状态，不得重新运行终态工单。

---

## 6. 状态与领域模型

### 6.1 枚举

```python
class Intent(StrEnum):
    NETWORK = "network"
    ACCOUNT = "account"
    PERMISSION = "permission"
    QUERY = "query"
    HIGH_RISK = "high_risk"

class TicketStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
```

风险等级固定为 `low / medium / high`；审批状态固定为 `pending / approved / rejected / expired`。

### 6.2 TicketState

以下是概念结构，最终代码由具名 Pydantic 模型组成，不使用裸 `dict`：

```python
class TicketState(TypedDict):
    ticket_id: str
    thread_id: str
    requester_id: str
    user_query: str
    trace_id: str
    status: TicketStatus
    intent: Intent | None
    routing_decision: RoutingDecision | None
    knowledge_refs: list[KnowledgeRef]
    pending_operation: PendingOperation | None
    tool_results: list[ToolResult]
    risk_decision: RiskDecision | None
    approval: ApprovalSnapshot | None
    agent_history: list[AgentStep]
    step_count: int
    resolution_mode: ResolutionMode | None
    final_response: str | None
```

字段所有权以 [`AGENTS.md` §2.1](../AGENTS.md) 为准。当前节点、ARQ retry count 和 checkpoint id 属于运行元数据，不进入 State。

### 6.3 RoutingDecision

```python
class RoutingDecision(BaseModel):
    intent: Intent
    next_agent: Literal["knowledge", "action", "finalize"]
    confidence: float = Field(ge=0.0, le=1.0)
    escalate: bool
    reason_code: RoutingReason
```

Supervisor 不输出最终风险等级。`confidence < 0.7` 时必须 `escalate=True`，Dispatcher 会拒绝不一致结果。

### 6.4 PendingOperation

```python
class PendingOperation(BaseModel):
    operation_id: str
    tool_name: ActionToolName
    validated_args: ToolArgsUnion
    args_hash: str
    side_effects: Literal["write"]
    evidence_result_ids: list[str]
    proposed_by: Literal["action"]
    proposed_at: datetime
    schema_version: str
```

`validated_args` 必须先通过对应 Tool 参数模型。`evidence_result_ids` 指向写操作前取得的账号、订单等只读 ToolResult。`operation_id` 由 Action 节点包装的确定性代码生成，模型无权提供。

### 6.5 RiskDecision

```python
class RiskDecision(BaseModel):
    allowed: bool
    risk_level: Literal["low", "medium", "high"]
    require_human: bool
    reason_code: PolicyReason
    explanation: str
```

`allowed=False` 时不得创建可执行审批；`risk_level=high` 时 `require_human` 必须为真。

### 6.6 KnowledgeRef 与 ToolResult

```python
class KnowledgeRef(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    content_excerpt: str
    score: float

class ToolResult(BaseModel):
    request_id: str
    operation_id: str | None
    tool_name: str
    outcome: ToolOutcome
    data: ToolResultUnion | None
    error_code: str | None
    retry_count: int
    latency_ms: int
```

`ToolResult.data` 是八个具名输出模型的 discriminated union，不是任意 JSON 字典。

### 6.7 API 读模型

API 不直接把 checkpoint 暴露给前端。`tickets` 表保存可查询 projection：状态、意图、风险、最终回复摘要和更新时间。Worker 在节点边界更新 projection；若更新中断，可由最新 checkpoint 和事件记录修复。

---

## 7. Agent 设计

### 7.1 Supervisor

职责：

- 将请求分类为五个 Intent；
- 选择 Knowledge、Action 或 finalize；
- 在 worker 返回后判断是否还需另一个业务步骤；
- 生成最终用户回复。

边界：

- 无 Tool；
- 不做最终风险判断；
- 不生成 operation_id；
- 不决定任意 Python 函数；
- 所有输出经过 `RoutingDecision` 或 `FinalResponse` 校验。

### 7.2 Knowledge

职责：

- 通过 `search_knowledge` 获取 SOP、FAQ 和脱敏历史案例；
- 根据返回片段生成 grounded answer；
- 返回可回查 `KnowledgeRef`。

边界：

- 不能执行外部写操作；
- 状态层只可追加 `knowledge_refs`；
- 引用为空或置信不足时必须明确降级，不得编造来源。

### 7.3 Action

职责：

- 从白名单中选择一个业务 Tool；
- 产生符合对应参数模型的参数；
- 读取操作经网关立即执行；
- 写操作生成 `PendingOperation`；
- 在批准后消费 ToolResult，并把业务结果交给 Supervisor。

边界：

- 模型只看到当前 Agent 的白名单 Schema；
- 不能绕过网关直接 import/call Tool；
- `action_execute` 不得重新规划已批准操作。

### 7.4 Policy

Policy 是四个逻辑 Agent 角色之一，但最终决策是纯函数：

```python
def assess_risk(
    operation: PendingOperation,
    actor: ActorContext,
    resource: ResourceContext,
) -> RiskDecision:
    ...
```

基础矩阵：

| 操作 | 条件 | 决策 |
|---|---|---|
| read Tool | 白名单通过 | low，自动允许 |
| `create_ticket` | 合法请求 | low，自动允许 |
| `request_access` | 为本人创建申请 | medium，自动允许创建申请；不直接授予权限 |
| `reset_password` | 请求者为账号本人且账号 active | medium，自动允许 |
| `reset_password` | 非本人 | deny |
| `process_refund` | 本人订单且金额低于阈值 | medium，自动允许 |
| `process_refund` | 本人订单且金额达到阈值 | high，必须审批 |
| 任意未知/越权 Tool | 任意 | deny |

金额使用 `Decimal`。`REFUND_APPROVAL_THRESHOLD_USD = Decimal("200.00")`，边界为 `amount >= threshold`。

Policy Node 从认证记录和权威 Repository 加载 `ActorContext/ResourceContext`，再调用纯函数；它不让模型查询权限。执行写操作前，Tool implementation 仍需用资源版本或业务条件做最终一致性校验，条件变化时返回业务冲突而不是沿用旧结果。

Policy 默认使用确定性模板生成 explanation。若后续启用模型润色，只能在规则判定完成后调用统一 ModelClient，且不得覆盖 `allowed/risk_level/require_human/reason_code`。

---

## 8. 模型与 Prompt

### 8.1 ModelClient

领域 Protocol 位于 `agents/context.py`，OpenAI-compatible 实现位于 `backend/integrations/model_client.py`：

```python
class ModelClient(Protocol):
    async def complete_structured(
        self,
        *,
        messages: Sequence[ModelMessage],
        output_model: type[BaseModel],
        config: ModelCallConfig,
    ) -> BaseModel: ...
```

默认实现使用 OpenAI-compatible 协议。Agent 不直接依赖具体供应商 SDK。

环境变量：

```text
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
OPENAI_TIMEOUT_S
OPENAI_MAX_RETRIES
```

优先使用服务端结构化输出；不支持时退回 JSON 文本并由 Pydantic 严格校验。格式修复最多两次，仍失败则进入 F-02。

### 8.2 测试模型

`FakeModelClient` 根据固定调用序列返回 Pydantic 对象或指定异常。Recorded Model 只用于重放和离线流程回归，不用于证明 Prompt 质量。

### 8.3 Prompt 文件

四个 YAML 文件固定字段：

```yaml
version: "1.0.0"
role: |
  ...
responsibility: |
  ...
constraints: |
  ...
output_schema:
  ...
```

约束使用绝对语言。Prompt 版本进入 Trace 和 eval artifact。Agent contract、Pydantic schema 与 Prompt output_schema 必须一致。

### 8.4 不把 Prompt 当安全边界

注入检测可以给工单打标签或触发保守路由，但不能替代：

- Tool Registry；
- Agent 白名单；
- Pydantic 参数模型；
- Policy；
- Approval receipt；
- 幂等和审计。

知识库片段视为不可信数据，必须与系统指令分隔，禁止把检索内容解释为新权限。

---

## 9. Tool Governance

### 9.1 工具清单

| Tool | 类型 | 基础风险 | 幂等 | 业务含义 |
|---|---|---|---|---|
| `search_knowledge` | read | low | true | 检索知识和脱敏案例 |
| `query_user` | read | low | true | 查询模拟账号 |
| `query_order` | read | low | true | 查询模拟订单 |
| `check_service` | read | low | true | 查询模拟服务状态 |
| `create_ticket` | write | low | true | 在模拟下游系统创建跟进工单 |
| `request_access` | write | medium | true | 创建权限申请，不直接授权 |
| `reset_password` | write | medium | true | 重置本人模拟账号密码 |
| `process_refund` | write | medium | true | 模拟退款；Policy 在达到阈值时升为 high |

`delete_user` 不在生产 Registry。未知工具和越权工具通过拒绝测试覆盖。

领域文件落点：`account.py` 放置 `query_user/reset_password/request_access`，`service.py` 放置 `search_knowledge/check_service`，`ticket.py` 放置 `query_order/create_ticket/process_refund`。

### 9.2 ToolSpec

每个 ToolSpec 包含八个字段：

```text
name
description
parameters
result_schema
side_effects
risk_level
idempotent
timeout_s
```

`parameters/result_schema` 由 Pydantic 模型生成 JSON Schema。描述必须包含读写性质、必填输入、输出、敏感字段和审批条件。

### 9.3 白名单

```python
AGENT_TOOL_WHITELIST = {
    "supervisor": [],
    "knowledge": ["search_knowledge"],
    "action": [
        "query_user",
        "query_order",
        "check_service",
        "create_ticket",
        "request_access",
        "reset_password",
        "process_refund",
    ],
    "policy": [],
}
```

### 9.4 依赖注入

领域层定义 Protocol：

```text
KnowledgeStore
UserRepository
OrderRepository
ServiceRepository
CredentialService
RefundService
TicketService
AccessRequestService
```

模拟实现可以位于 `tools/`；Chroma、PostgreSQL 等基础设施实现位于 `backend/integrations/`。执行网关创建 `ToolContext` 并注入，`tools/` 永不 import `backend`。

### 9.5 执行管线

```text
Registry lookup
→ Input Model validation
→ Agent whitelist
→ Policy/Approval receipt verification（写操作）
→ PostgreSQL operation ledger
→ Redis execution lock
→ timeout + conditional retry
→ Tool implementation
→ encrypted result persistence（如敏感）
→ redaction
→ audit
→ ToolResult
```

任一步失败都必须返回稳定 `error_code` 并审计。

### 9.6 幂等

```text
args_hash = sha256(canonical_json(validated_args))
idempotency_key = sha256(tool_name + operation_id + args_hash)
```

PostgreSQL `tool_operations.idempotency_key` 唯一：

- `succeeded`：返回已保存结果，不执行 Tool；
- `running` 且 lease 有效：返回 concurrent execution；
- `running` 且 lease 过期：由恢复逻辑取得新 lease；
- `failed_retryable`：在上限内重试；
- `failed_final`：返回历史失败，不自动重复。

Redis 锁缩短竞争窗口，但锁丢失不能导致账本丢失。敏感结果用应用加密密钥加密存储；Audit 只保存掩码。

### 9.7 重试与熔断

- 只对明确列出的超时、连接错误和 5xx 重试；
- 参数错误、授权拒绝和业务冲突不重试；
- 默认最多 3 次指数退避；
- 每个 Tool 维护独立熔断状态，连续 5 次可重试失败后打开；
- 熔断状态放 Redis，最终调用历史仍在 PostgreSQL。

> 实现中常量名使用 `CIRCUIT_BREAKER_FAILURE_THRESHOLD`，上文“熔断”不代表新增状态字段。

### 9.8 审计

每次尝试记录 `ToolCallRecord`。`args_summary/result_summary` 经过字段级脱敏。审计失败不得被静默吞掉；写操作在无法记录可靠审计时必须 fail closed。

---

## 10. Memory 与 Skill

### 10.1 运行状态

短期状态由 PostgreSQL-backed LangGraph checkpointer 保存，`thread_id=ticket_id`。它支持节点边界恢复、人工 interrupt、状态检查和失败后续跑。

Checkpoint 是流程状态，不是 API 读模型。API 查询 `tickets` projection，Worker 可以用 checkpoint 修复 projection。

### 10.2 检索语料

Chroma 保存：

- SOP 和 FAQ 文档块；
- 脱敏历史工单及解决方案；
- document/chunk metadata。

Knowledge Store Adapter 位于 `backend/integrations/chroma_store.py`，实现 `tools/context.py` 中的 Protocol。只有 `search_knowledge` Tool 可以访问它。

### 10.3 不保留的 Memory

不保存跨工单人格、团队经验自动写回或长期用户画像。历史案例进入知识库前必须脱敏并经过明确导入流程，运行时 Agent 不自动学习。

### 10.4 Password Reset Skill

唯一 Skill：

```text
verify_authenticated_actor
→ query_user
→ verify_account_active_and_self_owned
→ reset_password
→ render_notification
```

步骤及失败策略由 Python 字面量定义。`render_notification` 只生成文本，不发送邮件或短信。所有 Tool 步骤仍通过统一网关。

---

## 11. API、Worker 与持久化

### 11.1 认证

内置模拟用户目录。密码只保存强哈希；登录成功签发短期 JWT。

固定角色：

- `requester`：创建并查看自己的工单；
- `approver`：查看和决定待审批操作。

不提供角色管理或动态权限配置。

### 11.2 API

Phase 0 已将下列 13 个操作发布为 OpenAPI 契约，并生成前端 TypeScript 类型。当前仅 `/health` 返回存活结果；`/ready` 在 PostgreSQL、Redis 和配置尚未接入 API 时返回 503，其余业务端点返回稳定的 501 契约错误。表中描述的是后续阶段必须实现的业务语义，路由存在不等于功能已经完成。

认证：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录并返回 JWT |
| GET | `/api/auth/me` | 当前身份 |

工单：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tickets` | 创建工单，HTTP 202 |
| GET | `/api/tickets` | 当前用户可见工单 |
| GET | `/api/tickets/{ticket_id}` | 工单状态和结果 |
| GET | `/api/tickets/{ticket_id}/trace` | 脱敏执行轨迹 |

审批：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/approvals` | approver 可见待审批列表 |
| GET | `/api/approvals/{approval_id}` | 审批详情 |
| POST | `/api/approvals/{approval_id}/decision` | approve/reject，带 version |

评估和健康：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/eval/runs` | Eval 历史 |
| GET | `/api/eval/runs/{run_id}` | 单次指标和元数据 |
| GET | `/health` | 进程存活 |
| GET | `/ready` | PostgreSQL/Redis/配置就绪 |

错误响应统一为：

```json
{
  "error": {
    "code": "APPROVAL_ALREADY_DECIDED",
    "message": "该审批已处理",
    "request_id": "req-..."
  }
}
```

### 11.3 事务 Outbox

创建工单时，API 在同一 PostgreSQL 事务中写入：

1. `tickets(status=queued)`；
2. `job_outbox(kind=start_ticket, dedupe_key=ticket_id)`。

审批决定时，在同一事务中更新 Approval 并写 `resume_ticket` outbox。ARQ Worker 的周期任务将未发布 outbox 写入 Redis；发布成功后标记时间。Redis 暂时不可用不会丢失业务任务。

### 11.4 Job

```text
start_ticket(ticket_id)
resume_ticket(ticket_id, approval_id, approval_version)
dispatch_outbox()
expire_approvals()
```

消息只含 ID。ARQ 使用 JSON serializer。Job ID：

```text
start:{ticket_id}
resume:{approval_id}:{version}
```

Worker Job 重试只覆盖基础设施错误；图和 Tool 的业务重试由各自层处理。

### 11.5 PostgreSQL 表

| 表 | 作用 |
|---|---|
| `users` | 模拟身份和固定角色 |
| `tickets` | API 读模型与终态 |
| `approvals` | 绑定操作的审批事实和 version |
| `tool_operations` | 幂等键、lease、加密结果、最终状态 |
| `tool_call_records` | 脱敏审计记录 |
| `job_outbox` | Redis 发布前的持久任务 |
| `eval_runs` | Eval 元数据和报告位置 |
| LangGraph checkpoint tables | 由 checkpointer 管理 |

所有 Schema 变化通过 Alembic migration。应用启动不得自动 `create_all`。

### 11.6 审批过期

- `expire_approvals` 每分钟扫描 pending 记录；
- 30 分钟时写 reminder event，状态仍为 pending；
- 2 小时时以条件更新转为 expired，并在同一事务中写入系统 `resume_ticket` outbox；
- Worker 以 `expired` 决定恢复图，由 Approval Node 写入快照，Dispatcher 把工单转为 `expired`；
- 来自用户的 expired approval 重复决定或恢复请求必须拒绝；
- MVP 不发送真实通知，也不升级“上级审批人”。

---

## 12. 安全、审计与可观测

### 12.1 最小安全模型

- JWT 短期有效，签名密钥通过环境变量注入；
- requester 只能读自己的工单；
- approver 只能处理 pending 审批；
- CORS 只允许配置的前端源；
- 所有输入有长度、枚举和格式限制；
- 不把知识库内容拼成系统指令；
- 错误响应不返回内部堆栈或供应商密钥。

### 12.2 敏感数据

统一 `redaction.py` 负责字段级与模式级脱敏：

```text
password
temporary_password
access_token
authorization
card_number
api_key
```

临时口令只向授权请求者返回一次；为幂等重试保存时使用应用级加密。日志、审计和 Langfuse 永远不保存明文。

### 12.3 关联 ID

每条链路使用：

- `request_id`：单次 HTTP/Tool 尝试；
- `ticket_id/thread_id`：工单和图线程；
- `trace_id`：跨 API、Worker、Agent 和 Tool；
- `job_id`：一次队列任务；
- `operation_id`：一个逻辑写操作。

### 12.4 可观测

默认输出 JSON 日志和内部指标：

- 队列等待与 Job 耗时；
- 每节点耗时和状态转移；
- 模型耗时、token 和错误；
- Tool 调用、重试、熔断、缓存命中；
- 审批等待时间；
- 工单终态。

Langfuse 通过配置启用，仅作为模型/节点 Trace 导出端。PostgreSQL Audit 才是治理证据。

---

## 13. 失败模式

| 编号 | 注入故障 | 确定性处理 | 必须断言 |
|---|---|---|---|
| F-01a | 可选 read Tool 连续超时 | 3 次后 fallback，继续保守回复 | resolved + fallback，无写副作用 |
| F-01b | 关键 write Tool 连续超时 | 3 次后 escalated | escalated，账本未 succeeded |
| F-02 | 模型连续返回非法结构 | 两次修复后 escalated | schema error 轨迹 |
| F-03 | Supervisor/worker 循环 | 第 15 步停止 | escalated，不再调用模型 |
| F-04 | 未知或越权 Tool | 网关拒绝 | rejected + audit + 0 次实现调用 |
| F-05 | 审批超过 TTL | expire job 条件更新 | expired，resume 被拒绝 |
| F-06 | Prompt injection | 白名单/Policy 拒绝 | 0 次危险副作用 |
| F-07 | checkpoint 后 Worker 崩溃 | Job 重试并从 checkpoint 恢复 | 写操作最多一次 |
| F-08 | 重复 approval/resume | version + Job ID + ledger 去重 | 单次状态转移和执行 |

未知程序异常进入 `failed`，保留 request/trace ID，不向用户泄漏堆栈。是否重试由异常分类决定，不捕获后盲目重跑整个图。

---

## 14. Evaluation

### 14.1 GoldenCase

```python
class GoldenCase(BaseModel):
    case_id: str
    query: str
    requester_id: str
    expected_intent: Intent
    expected_handoffs: list[ExpectedHandoff]
    expected_tools: list[str]
    expected_risk: RiskLevel
    expected_require_human: bool
    expected_outcome: TicketStatus
    relevant_document_ids: list[str]
    min_business_steps: int
    reference_answer: str
    tags: set[str]
```

`expected_handoffs` 直接记录 `from/to`，避免“Agent 序列是否包含 Supervisor”这种歧义。实际 Handoff 从标准化 Trace 事件计算。

### 14.2 数据集规模

- 少于 20：开发样例，不发布指标；
- 20–79：冒烟评估；
- 至少 80 且覆盖 F-01～F-08：完整评估。

公开数据和 LLM 只能生成语言变体，全部 expected labels 必须人工审核。数据集版本每次标签变化时递增。

### 14.3 确定性指标

| 指标 | 计算 |
|---|---|
| Intent Accuracy | expected intent 命中比例 |
| Handoff Accuracy | expected/actual edge 命中比例 |
| Tool Selection | 每例 Tool 集合 Jaccard 后取均值 |
| Policy Accuracy | allowed/risk/require_human 全字段命中比例 |
| High-risk Bypass Count | high 风险在无有效 approval 下执行次数 |
| Task Success Rate | 实际终态与 expected outcome 一致且无约束违规的用例比例 |
| Trace Efficiency | `min_business_steps / actual_business_steps`，上限 1 |
| Redundant Tool Rate | 无业务需要的重复 Tool / 总 Tool 调用 |
| Retrieval Precision/Recall | relevant_document_ids 与检索文档集合比较 |
| P95 Latency | 全流程耗时 95 分位 |

边界值必须写死并测试：两个 Tool 集合都为空时 Jaccard=1；actual business steps 为 0 时效率=0。

### 14.4 设计门槛

以下是目标门槛，不是当前成绩：

| 指标 | 门槛 |
|---|---:|
| Intent Accuracy | ≥ 90% |
| Handoff Accuracy | ≥ 90% |
| Tool Selection Jaccard | ≥ 85% |
| Policy Decision Accuracy | 100% |
| High-risk Bypass Count | 0 |
| Task Success Rate | ≥ 80% |
| Trace Efficiency | 不低于基线 |
| Redundant Tool Rate | 不高于基线 |
| P95 Latency | 不高于基线的 120% |

### 14.5 Faithfulness 与 Judge

Faithfulness 可以使用单独 LLM evaluator，但仅作观察指标。LLM Judge 需要至少 50 条人工评分样本校准，相关性超过 0.85 后才可对外使用。Judge 不参与 Policy、安全或确定性指标计算。

### 14.6 Offline 与 Live Eval

- Offline：Fake/Recorded Model，验证图、Schema、失败路径和指标实现；
- Live：真实 OpenAI-compatible 模型，验证 Prompt 和整体质量。

Live artifact 必含：时间、Git SHA、数据集版本、Prompt 版本、模型名、Base URL 的非敏感标识、参数、指标、延迟、token 和成本。

### 14.7 基线

普通 CI 只读 baseline。只有显式 `--update-baseline` 生成候选文件，人工审查 artifact 后提交。报告必须显示 baseline → candidate，而不是只有候选结果。

---

## 15. 测试与 CI

### 15.1 测试金字塔

1. 纯函数单测：路由校验、Policy、哈希、状态转换、指标、脱敏；
2. Agent 合约测试：Fake Model + Pydantic；
3. Tool 治理测试：Fake 实现 + 真实网关；
4. PostgreSQL/Redis 集成测试：migration、ledger、outbox、approval；
5. Graph 测试：start、interrupt、resume、crash recovery；
6. API 测试：JWT、数据隔离、HTTP 202、错误结构；
7. Docker E2E：Fake Model 下跑通三条代表路径；
8. Live Eval：真实模型质量回归。

### 15.2 后端 CI

`backend-ci.yml`：

- Ruff；
- mypy；
- pytest unit；
- PostgreSQL/Redis service containers；
- Alembic 从空库升级；
- integration tests；
- Tool Registry 与 JSON Schema 快照一致；
- Agent contract 与 Pydantic/Prompt 一致。

### 15.3 前端 CI

`frontend-ci.yml`：

- ESLint；
- Vue TSC；
- Vitest；
- production build；
- 从 OpenAPI 重新生成类型并检查无 diff。

### 15.4 Eval CI

`eval.yml`：

- PR 离线冒烟不访问真实模型；
- 行为相关目录变化需要 Live Eval artifact；
- 受保护分支或手动运行全量 Live Eval；
- CI 不自动覆盖 baseline。

---

## 16. 前端

### 16.1 页面

| 路由 | 页面 | 核心功能 |
|---|---|---|
| `/login` | Login | 模拟用户登录 |
| `/tickets` | TicketSubmit | 创建、列表、状态轮询、结果 |
| `/approvals` | Approval | 待审批列表、绑定操作详情、决定 |
| `/traces/:ticketId` | TraceViewer | Agent/Tool/Approval 时间线 |
| `/eval` | EvalDashboard | 运行历史、门槛、baseline 对比 |

主导航仍以四个业务页面为核心，Login 不计入展示页面数量。

### 16.2 审批 UI

展示：

- requester；
- Tool 名；
- 脱敏参数；
- risk/reason；
- operation_id 与 args_hash 的短摘要；
- 创建时间、过期时间和历史事件。

审批人只能 approve/reject 并填写理由，不能编辑参数。

### 16.3 轮询

- 工单运行中每 2 秒查询；
- 审批列表每 10 秒刷新；
- 页面不可见时暂停；
- 使用指数退避处理网络失败；
- 终态后停止轮询。

### 16.4 类型与安全

API 类型只来自 OpenAPI。JWT 默认保存在内存；刷新页面可重新登录。临时口令不进入 localStorage、前端日志或 Trace 页面。

---

## 17. 部署

### 17.1 默认 Compose

完整应用的目标 Compose 包含：

```text
frontend
api
worker
postgres
redis
```

Phase 0 仓库中的 Compose 只包含固定版本的 PostgreSQL 与 Redis，并已验证两个服务能达到 healthy。`frontend`、`api`、`worker`、Chroma 持久卷和 Nginx 属于后续阶段交付，不得把当前基础设施验证称为完整应用部署。

Chroma 由单 Worker 使用 persistent volume。默认不包含完整 Langfuse 自托管栈；配置外部 `LANGFUSE_HOST` 时启用导出。

### 17.2 服务要求

- 镜像使用固定版本，不使用 `latest`；
- API 和 Worker 使用同一个后端镜像、不同启动命令；
- API readiness 检查 PostgreSQL、Redis 和必要配置；
- Worker 有健康检查和优雅停止；
- PostgreSQL/Redis 使用健康依赖，不只依赖启动顺序；
- PostgreSQL 和 Chroma 挂载持久卷；
- Nginx 支持 SPA fallback 和 `/api` 反代。

### 17.3 配置

`.env.example` 必须列出且不包含真实值：

```text
APP_ENV
DATABASE_URL
REDIS_URL
JWT_SECRET
APP_ENCRYPTION_KEY
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
OPENAI_TIMEOUT_S
OPENAI_MAX_RETRIES
CHROMA_PATH
LANGFUSE_HOST（可选）
LANGFUSE_PUBLIC_KEY（可选）
LANGFUSE_SECRET_KEY（可选）
```

### 17.4 备份与恢复

作品集部署至少提供：

- PostgreSQL dump/restore 命令；
- Chroma volume 重建说明；
- migration 前备份提示；
- Redis 可丢失时如何从 outbox 和 PostgreSQL 恢复任务；
- 不在备份示例中输出密钥。

---

## 18. 目标 Repo 结构

这是目标结构，不代表文件当前都已实现：

```text
AgentDesk/
├── README.md
├── AGENTS.md
├── PORTFOLIO.md
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── alembic.ini
├── migrations/
├── docs/
│   └── architecture.md
├── specs/
│   ├── README.md
│   ├── agent_contracts/
│   ├── tool_schemas/tools.json
│   └── openapi/openapi.json
├── agents/
│   ├── supervisor.py
│   ├── knowledge.py
│   ├── action.py
│   ├── policy.py
│   ├── schemas.py
│   └── context.py
├── tools/
│   ├── registry.py
│   ├── schemas.py
│   ├── context.py
│   ├── account.py
│   ├── service.py
│   └── ticket.py
├── skills/password_reset/
│   ├── skill.py
│   ├── validate_user.py
│   └── notification_template.md
├── backend/
│   ├── Dockerfile
│   ├── main.py
│   ├── worker.py
│   ├── api/
│   │   ├── auth.py
│   │   ├── tickets.py
│   │   ├── approvals.py
│   │   └── eval.py
│   ├── auth/
│   ├── db/
│   ├── graph/
│   │   ├── state.py
│   │   ├── nodes.py
│   │   ├── edges.py
│   │   └── builder.py
│   ├── governance/
│   │   ├── executor.py
│   │   ├── whitelist.py
│   │   ├── idempotency.py
│   │   ├── circuit_breaker.py
│   │   ├── audit.py
│   │   └── redaction.py
│   ├── integrations/
│   │   ├── model_client.py
│   │   └── chroma_store.py
│   ├── observability/
│   │   ├── logging.py
│   │   └── langfuse_client.py
│   ├── jobs/
│   │   ├── ticket_jobs.py
│   │   └── outbox.py
│   └── memory/checkpointer.py
├── prompts/
├── eval/
│   ├── golden_dataset.json
│   ├── baseline.json
│   ├── metrics.py
│   ├── run_eval.py
│   └── judge.py
├── tests/
│   ├── fakes/
│   ├── unit/
│   ├── integration/
│   └── test_failure_modes.py
├── frontend/
│   ├── Dockerfile
│   └── src/
└── .github/workflows/
    ├── backend-ci.yml
    ├── frontend-ci.yml
    └── eval.yml
```

后端 Dockerfile 位于 `backend/Dockerfile`，构建 context 必须是仓库根，才能复制所有顶层领域包。

---

## 19. 开发计划与验收

### Phase 0：契约闭合

产出：

- 四份主文档一致；
- Agent contracts；
- 八个 Tool Schema；
- OpenAPI 草案；
- 20 条人工 Golden Cases；
- pyproject、前端依赖和 Compose 目标依赖确定。

验收：没有空契约、没有 Schema 命名冲突、文档链接有效。当前仓库已按 [`Phase 0 implementation plan`](superpowers/plans/2026-09-14-phase-0-contract-foundation.md) 完成本阶段产出与本地验收；这不代表 Phase 1～3 已实现。

### Phase 1：基础纵切

产出：

- PostgreSQL、Alembic、Redis、ARQ；
- JWT 登录；
- OpenAI-compatible ModelClient 和 Fake；
- Supervisor/Knowledge/Action 的只读路径；
- Ticket API、Worker、基础前端。

验收：提交网络查询返回 202，Worker 执行、引用可回查、重启后状态仍可查询。当前仓库已按 [`Phase 1 implementation plan`](superpowers/plans/2026-09-14-phase-1-read-only-vertical-slice.md) 完成本阶段产出与全量验收，产出 `eval/artifacts/phase1-after.json` 真实对比产物。

### Phase 2：写操作治理与 HITL

产出：

- Tool Gateway、白名单、账本、重试、熔断、审计；
- 确定性 Policy；
- PendingOperation、Approval、interrupt/resume；
- Password Reset Skill；
- F-01～F-08。

验收：高风险退款在审批前零副作用；重复 resume 只执行一次；Worker 崩溃后恢复。

### Phase 3：评估与展示

产出：

- 至少 80 条完整 Golden Dataset；
- offline/live eval；
- baseline 流程；
- 三个 CI 工作流；
- Trace 和 Eval 页面；
- 可选 Langfuse；
- Docker Compose、备份恢复说明。

验收：Compose 一键启动，CI 和 Live Eval 产出可追溯报告。只有此后才能生成完成时简历 bullet。

---

## 20. 决策记录

| 决策 | 选择 | 原因 |
|---|---|---|
| 项目级别 | 作品集级生产化 | 核心机制真实，产品范围受控 |
| Agent Pattern | Supervisor + 专职 workers + 确定性 Policy | 权限和评估边界 |
| 图运行 | LangGraph | checkpoint、interrupt、resume、显式图 |
| 进程 | API + ARQ Worker | HTTP 快速返回、任务可恢复 |
| 模型协议 | OpenAI-compatible | 默认可用且供应商可替换 |
| 业务存储 | PostgreSQL | 跨进程一致性、事务、migration |
| 队列与锁 | Redis | ARQ 与短期并发控制 |
| 幂等事实源 | PostgreSQL ledger | Redis 丢失不重复写 |
| RAG | Chroma + Protocol 注入 | 范围可控且不破坏依赖方向 |
| 身份 | 固定 requester/approver + JWT | 可审计但不扩展完整 RBAC |
| 实时更新 | Polling | MVP 简单可靠 |
| Langfuse | 可选外部服务 | 保留 Agent Trace，避免默认基础设施膨胀 |

---

**设计状态**：v2.0 已获人工方向确认。实现必须以 [`AGENTS.md`](../AGENTS.md) 和 `specs/` 中的机器契约为门禁；本文中的范围和阈值不代表当前已实现或已达到。
