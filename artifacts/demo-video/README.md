# AgentDesk 运行演示视频

正式成片：[`AgentDesk-2026-10-05-demo.mp4`](AgentDesk-2026-10-05-demo.mp4)

- 时长约 1 分 34 秒；1440 × 900、25 fps、H.264 MP4；无音轨，说明文字直接显示在画面中。
- 展示模拟 IT 工单的登录与异步处理、VPN 知识检索和引用、退款风险判定、人工审批与冻结参数、Worker 恢复执行、工具审计轨迹，以及空评测库的真实页面状态。
- 录制使用独立 PostgreSQL `agentdesk_video_capture_test`、Redis DB 3、模拟订单和知识文档，以及临时运行的**确定性 OpenAI-compatible 演示模型**。它用于展示已有图与治理机制，不是 Live Eval 或真实模型质量成绩。
- 录制后核对：两个工单均为 `resolved`；审批记录为 `approved`、版本 2；模拟退款记录 1 条；`search_knowledge` 与 `process_refund` 各有 1 条成功工具审计。
- 同日另用真实 `qwen3:8b` 试跑：VPN 工单在 Supervisor/Knowledge 间重复路由后达到 15 步上限并升级；退款工单被 Dispatcher 直接升级，未进入审批。真实模型的端到端验收因此尚未通过。
- 工作树录制时的 Git HEAD：`ca63213`，且已有未提交改动。以上为本地演示结果，不代表部署环境或完整 80 例评估。

WebM、过程 MP4 和检查用 PNG 保留在本地，由 `.gitignore` 排除。仓库只提交正式成片与本说明。
