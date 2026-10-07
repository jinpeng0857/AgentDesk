# AgentDesk 界面改版验证与预览

日期：2026-10-06。这里的截图使用 `configure-fixtures.js` 提供的固定 API 响应，仅验证前端渲染与交互。截图中的工单、审批、模型、指标与 Git 标识均为 UI 样例，不是运行成果或真实模型验收成绩。

## 实际执行的前端门禁

在 Node.js 22.22.2 下，从 `frontend` 执行以下脚本，均退出 0：

| 脚本 | 结果 |
|---|---|
| `npm run lint` | ESLint 通过，最大允许警告数为 0 |
| `npm run type-check` | Vue TSC 通过 |
| `npm run test` | 6 个测试文件、18 个测试通过 |
| `npm run build` | Vite 8.3.0 生产构建通过，63 个模块 |

`git diff --check` 在本轮修改范围内退出 0。测试保留审批版本、参数不可编辑、脱敏和 Token 不写入 localStorage 等断言；新增工单创建、等待审批及不充分评估证据的回归覆盖。

## 浏览器检查

- 五页均保存了桌面（1440×1000）与手机（375×812）全页截图；全页图片高度可能超过视口高度。
- 375px 下五页的 `documentElement.scrollWidth` 等于视口宽度；工单页另检查了 768px 和 1024px。
- 工单示例只填入输入框；提交操作独立。等待审批与人工接管文案不虚构已完成答复。
- 审批通过的固定响应会更新状态和版本并移除决定按钮；过期审批没有通过／拒绝控件。
- 轨迹筛选工具事件后保留原始序号 03；不会按筛选结果重新编号。
- 缺少对照基线的 P95 指标显示“未判定”；offline 模式明确说明不能证明真实模型或 Prompt 质量。
- 键盘焦点可见；减少动画设置下按钮 transition 为 0s。跳转工作区链接仅在聚焦时显示。
- 修正验证脚本的 API 拦截范围并重新加载后，浏览器 console error 检查返回 0 条。

## 预览文件

| 页面 | 桌面 | 手机 |
|---|---|---|
| 登录 | [login-desktop.png](login-desktop.png) | [login-mobile.png](login-mobile.png) |
| 工单 | [tickets-desktop.png](tickets-desktop.png) | [tickets-mobile.png](tickets-mobile.png) |
| 审批 | [approvals-desktop.png](approvals-desktop.png) | [approvals-mobile.png](approvals-mobile.png) |
| 轨迹 | [traces-desktop.png](traces-desktop.png) | [traces-mobile.png](traces-mobile.png) |
| 评估 | [eval-desktop.png](eval-desktop.png) | [eval-mobile.png](eval-mobile.png) |

## 验证范围

本轮没有启动或验证真实 API、Worker、PostgreSQL、Redis、Ollama 完整流程。Docker Engine 当前未运行。没有据此创建或更新 `docs/acceptance/current.md`，也没有晋升评估候选。前端依旧调用既有真实 API；固定响应仅安装在验证浏览器内，没有加入应用运行代码。

## 复现 UI 样例

在 `frontend` 用 Node.js 22.22.2 启动 `npm run dev -- --host 127.0.0.1`。在仓库根目录执行：

```powershell
npm exec --yes --package=node@22.22.2 --package=@playwright/cli -- playwright-cli -s=agentdesk-ui open http://127.0.0.1:5173/login
npm exec --yes --package=node@22.22.2 --package=@playwright/cli -- playwright-cli -s=agentdesk-ui run-code --filename=output/playwright/configure-fixtures.js
npm exec --yes --package=node@22.22.2 --package=@playwright/cli -- playwright-cli -s=agentdesk-ui snapshot
```

用快照对应控件填写样例账号 `demo-reviewer` 和样例密码 `fixture-only` 后登录。它们只被验证脚本接受，不是真实服务凭据。Token 只存在内存，刷新页面后需重新登录。
