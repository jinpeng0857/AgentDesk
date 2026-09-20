# AgentDesk 项目表达与证据清单

> 本文件用于管理简历素材和面试叙事，不是系统设计文档。
>
> 文档版本：v2.0
> 当前结论：Phase 0 契约基线已落地并通过本地验收；完整项目尚未完成
> 开发约束：[`AGENTS.md`](AGENTS.md) · 设计：[`docs/architecture.md`](docs/architecture.md)

---

## 1. 表达原则

简历只陈述仓库和运行产物能证明的事实。

- 设计门槛不是实测成绩。
- 计划采用某项技术，不等于已经实现。
- 空测试文件不等于测试覆盖。
- Golden Dataset 的目标规模不等于已有样本数。
- 不保留百分比、数值或待办式占位符，避免被误复制成简历成果。
- 任何性能、准确率、成功率和“零绕过”表述都必须链接到 eval artifact。
- 简历 bullet 使用完成时之前，必须通过 §3 的证据门禁。

---

## 2. 当前可验证状态

截至 Phase 3 评估体系、大盘与全栈容器化完成（**Phase 1、Phase 2 与 Phase 3 已全量通过验收（Gate A / Gate B / Gate C All Passed）**）：

### 已完成

- 明确项目定位：作品集级生产化，而非企业级 SaaS 或一次性 Demo。
- 完成四角色权限边界、确定性 Dispatcher、高风险审批和独立 Worker 的系统设计。
- 确定 OpenAI-compatible 模型接口、PostgreSQL 持久化、Redis/ARQ 队列和 Chroma 检索方案。
- 定义 Tool Governance、状态所有权、失败模式、Evaluation 与 CI 门禁。
- 建立 Pydantic v2 领域契约、八个 Tool Schema、封闭 Registry 和按角色白名单。
- 填写四份 Agent contract 与 Prompt，并提供契约校验脚本。
- 从 FastAPI 代码导出 OpenAPI，再生成前端 TypeScript API 类型，保障前后端零契约漂移。
- 建立 **80 条人工审核标签的完整 Golden Dataset**，均衡覆盖 5 类 intent（每类 16 条）与 F-01~F-08 异常失败模式。
- 实现**纯函数确定性评估计算引擎**（`eval/metrics.py`），支持 Intent Accuracy、Handoff Accuracy、Tool Jaccard、Policy Accuracy（100% 门禁）、High-risk Bypass Count（硬门禁 0 绕过）、Task Success Rate、P95 尾部延迟等 13 个指标，严格杜绝 LLM 充当主观裁判。
- 固化官方基线 `eval/baseline.json` (`baseline-v1.0.0-frozen`)，升级 Eval 运行器与 Eval API（`GET /api/eval/runs` 与 `GET /api/eval/runs/{run_id}`）。
- 交付前端 **TraceViewer 审计时间线页面**（`TraceViewerView.vue`），支持按工单追溯 Supervisor、Knowledge、Action、Policy、Approval、Dispatcher 全流转细节与脱敏参数。
- 交付前端 **EvalDashboard 评测大盘**（`EvalDashboardView.vue`），以鲜明色彩与醒目标签**严格区分“设计门槛 (Design Threshold)”与“本次结果 (Actual Observed)”**。
- 落地 **TraceExporter 协议与可选 Langfuse 外部适配器**，默认 100% 离线自包含与零外部网络请求，遥测异常静默隔离不中断工单主图，元数据脱敏严防密码泄漏。
- 完成前端 Multi-Stage 容器化（`frontend/Dockerfile`、`frontend/nginx.conf`），补齐 Docker Compose 全栈拓扑依赖与健康检查链路。
- 交付全栈自动化巡检脚本 `scripts/full_stack_smoke.py` 与容灾备份恢复演练脚本 `scripts/backup_restore_drill.ps1`。
- 冻结最终评估工件 `eval/artifacts/l3-live-final.json`，通过最终验收报告 `docs/evidence/l3/final-acceptance.md`。

---

## 3. 简历证据门禁

| 候选能力 | 写入简历前必须具备的证据 | 当前状态 |
|---|---|---|
| 多 Agent 编排 | 可运行图、路由测试、真实 Trace | **满足**：四个 Agent 角色、Dispatcher 确定性边界、LangGraph 读写图与断点恢复测试全量通过 |
| 独立 Worker | HTTP 202、队列记录、Worker 重启恢复测试 | **满足**：HTTP 202、JSON ARQ 载荷、确定性 job ID、outbox 投递与 Worker 崩溃恢复断点续跑测试已通过 |
| Tool Governance | Registry、白名单、Policy、幂等账本、审计测试 | **满足**：Registry、白名单、Schema、统一执行网关、操作账本幂等缓存与脱敏审计测试全量通过 |
| Human-in-the-loop | interrupt、审批记录、resume、重复恢复测试 | **满足**：Policy 风险判定、interrupt 挂起、审批单绑定、OCC 乐观锁、resume 恢复与重复 resume 幂等通过 |
| RAG | 可回查引用、检索评估产物 | **满足**：Chroma 返回五字段可回查引用，持久化至 projection，并在详情 API 与前端卡片完整渲染 |
| Evaluation | 人工审核数据集、baseline、带元数据的 Live Eval | **满足**：80 条人工审核样本、官方基线 `eval/baseline.json`、离线/在线评测运行器及终版工件 `l3-live-final.json` 已固化 |
| 前端 | 可访问页面、OpenAPI 生成类型、构建记录 | **满足**：Vue 3 Login、Tickets、ApprovalView、TraceViewer 与 EvalDashboard 全部通过 Vitest 与生产构建 |
| 部署 | Compose 构建、健康检查、备份恢复演练 | **满足**：Docker Compose 全栈拓扑配置就绪，包含 Nginx、前端容器、API、Worker、Postgres、Redis 及灾备演练脚本 |
| 量化指标 | 可下载 eval artifact + Git SHA | **满足**：`eval/artifacts/l3-live-final.json` 关联完整 Git SHA 与 80 Case 实测指标，高风险绕过严格为 0 |

当某行证据全部存在后，才能新增对应完成时 bullet。不能先写 bullet 再等待实现追上文案。

---

## 4. 完成后的项目叙事结构

本节只规定叙事结构，不提供带占位符的简历句子。

### 4.1 第一条：Agent Orchestration

回答：

- 为什么需要多 Agent，而不是单 Agent；
- 四个角色如何按权限拆分；
- Dispatcher 如何校验路由；
- checkpoint 和 Worker 如何提供恢复能力；
- 对应的路由或恢复证据在哪里。

### 4.2 第二条：Tool Governance

回答：

- Tool Schema 如何生成和校验；
- 白名单、Policy、审批和执行网关的顺序；
- PostgreSQL 幂等账本与 Redis 锁分别解决什么；
- 敏感结果如何加密、脱敏和审计；
- 失败注入证明了什么。

### 4.3 第三条：Human-in-the-loop

回答：

- Action 为什么分 propose 和 execute；
- Approval 如何绑定 `operation_id + args_hash`；
- interrupt 后如何从持久化 checkpoint 恢复；
- 重复审批和重复 resume 如何被阻止；
- 高风险操作未绕过审批的实测证据。

### 4.4 第四条：Evaluation

回答：

- Golden labels 为什么人工审核；
- Handoff Accuracy 如何避免 Agent 序列歧义；
- Offline Eval 与 Live Eval 的职责区别；
- baseline 为什么不能由普通 CI 自动更新；
- 指标、数据集和模型版本如何追溯。

### 4.5 第五条：Full-stack Delivery

回答：

- FastAPI API 与 ARQ Worker 为什么分离；
- PostgreSQL、Redis、Chroma 各自的职责；
- Vue 页面如何消费 OpenAPI 生成类型；
- Docker Compose、migration、健康检查和 CI 如何组成可复现交付。

---

## 5. 面试问答

以下答案描述已批准的设计。实现完成前，应明确说“设计为”，不要说“已经在生产验证”。

### Q：为什么不用单 Agent？

工单分类、知识检索、业务执行和安全授权具有不同权限边界。让一个 Agent 同时拥有这些能力，会让 Prompt 注入或错误规划直接获得写权限，也难以区分路由、工具和策略错误。AgentDesk 把它们拆为 Supervisor、Knowledge、Action 和 Policy，并在代码层分别限制状态写入和 Tool 权限。

### Q：四个角色都需要 LLM 吗？

不需要。Supervisor、Knowledge 和 Action 需要语言理解；Policy 的最终 allow/deny、风险等级和审批要求由纯函数计算。使用多 Agent 不等于所有决策都交给模型，安全边界必须确定性和可测试。

### Q：为什么用 LangGraph？

核心需求包含显式条件图、节点级 checkpoint、人工 interrupt 和跨请求 resume。LangGraph 与这些需求直接匹配。选择依据是持久化工作流能力，不是单纯因为它是 Agent 框架。

### Q：为什么 API 和 Worker 分开？

工单运行可能包含多次模型调用、工具超时和长时间审批。API 只创建记录并返回 HTTP 202，Worker 执行图，可以独立重启和恢复。这样 HTTP 生命周期不会承载长任务，也不会把进程内后台任务误当成可靠队列。

### Q：为什么从 SQLite 改为 PostgreSQL？

API、Worker、审批和 checkpoint 会跨进程并发访问状态。PostgreSQL 提供事务、条件更新、唯一约束和 migration，适合作为工单、审批、幂等账本和 checkpoint 的事实源。SQLite 适合本地原型，但不符合本项目选择的进程边界和可靠性目标。

### Q：如何避免重复退款或重复重置密码？

每个逻辑写操作拥有稳定 `operation_id`，已验证参数产生 `args_hash`，两者与 Tool 名共同形成幂等键。PostgreSQL 操作账本以该键做唯一约束，Redis 只负责短期执行锁。即使 Worker Job 被重新投递，执行器也会读取历史状态，而不是再次产生副作用。

### Q：为什么 Redis 不是幂等事实源？

Redis 锁可能过期或被清空，只适合并发协调。最终状态和加密结果必须落在 PostgreSQL 账本中，才能在 Redis 丢失后判断操作是否已经成功。

### Q：审批如何保证“批的是这一笔，执行的也是这一笔”？

ApprovalRecord 绑定 `operation_id` 和 `args_hash`。恢复执行前重新计算参数哈希并核对审批记录。MVP 不允许审批时修改参数；如果参数改变，旧操作作废并重新发起审批。

### Q：如何防止 Prompt injection？

输入检测只用于告警和保守路由。真正的安全边界是 Tool Registry、Agent 白名单、参数 Schema、确定性 Policy、审批凭证和执行网关。Dispatcher 负责图转移，不负责 Tool 授权。

### Q：为什么生产 Registry 不保留 `delete_user`？

一个永远不允许执行的危险函数没有必要出现在生产可执行面。权限测试可以请求未知或不在白名单中的 Tool，并验证网关拒绝、审计和零副作用。这样测试安全边界时不需要注册真实危险能力。

### Q：RAG 为什么不直接放在 Knowledge Agent 里访问 Chroma？

Knowledge 必须通过 `search_knowledge` Tool，才能统一经过 Schema、白名单和审计。领域层只依赖 `KnowledgeStore` Protocol，Chroma Adapter 位于服务层并通过依赖注入提供，避免 `tools/` 反向 import `backend/`。

### Q：Offline Eval 和 Live Eval 有什么区别？

Offline Eval 使用 Fake 或 Recorded Model，适合稳定验证图、Schema 和失败路径；它不能证明 Prompt 质量。Live Eval 使用真实 OpenAI-compatible 模型，负责衡量分类、路由、工具选择和回复质量，并记录模型、Prompt、数据集和 Git 版本。

### Q：为什么不默认自托管 Langfuse？

本项目需要 Agent Trace，但不需要为了 Trace 默认引入一整套观测平台依赖。结构化日志和 PostgreSQL 审计是基础能力，Langfuse 作为可选外部导出端。这样保留可观测价值，同时控制单机部署范围。

---

## 6. 实测结果登记规则

完成 Live Eval 后，可在本节新增结果，但每条必须包含：

```text
run_id
artifact 路径或 CI 链接
Git SHA
数据集版本
Prompt 版本
模型名
运行时间
样本数量
指标
```

如果缺少其中任一项，不得把结果转写为简历成绩。

当前没有可登记的实测结果。

---

## 7. 项目完成定义

只有同时满足以下条件，才能把 AgentDesk 作为“已完成项目”写入简历：

- Docker Compose 从空环境构建并通过健康检查；
- 正常查询、普通写、高风险审批三条路径 E2E 通过；
- Worker 重启、重复 resume 和 Tool 超时等失败模式通过；
- OpenAPI 类型无漂移；
- 完整 Golden Dataset 和人工审查 baseline 已提交；
- Live Eval 达到 `AGENTS.md` 的门槛；
- 简历中的每个量化数字都能链接到 artifact。

在此之前，优先继续实现和测量，不包装尚不存在的成果。
