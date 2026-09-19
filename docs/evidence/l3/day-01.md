# Day 01 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: 冻结真实模型基线，解除 Phase 1 阻塞
- Commit SHA: pending
- Changed files:
  - eval/artifacts/phase1-before.json
  - docs/evidence/l3/day-01.md
- Contract/permission/state/eval impact:
  - 冻结首份 live model baseline artifact (run_id: live-model-baseline-2458dd97bcf4)。
  - 未修改 Prompt、Agent、Tool 或 Graph 业务代码与状态所有权。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| `uv run python eval/run_eval.py --mode live --stage model-baseline --output eval/artifacts/phase1-before.json` | 0 | `cases=20 intent_match_count=17 failures={}` |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| DeepSeek OpenAI-compatible (api.deepseek.com, deepseek-chat) | 20 cases evaluated, 17 matched, 0 transport/config errors | eval/artifacts/phase1-before.json |

## Human review

- Authorization boundary: 保持不变，未修改工具白名单或代理角色。
- Sensitive-data review: 审计确认产物中无任何 API key、Bearer token、明文密码或凭据。
- Test-quality review: 真实调用 OpenAI 兼容模型端点，各 case 返回结构完整可解析。
- Documentation accuracy: 实测 17/20 intent match，未将指标夸大为系统终极指标。

## Remaining gaps

- None for Day 01. Phase 1 真实模型基线阻塞正式解除。
