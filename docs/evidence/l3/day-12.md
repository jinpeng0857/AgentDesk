# Day 12 Acceptance

- Date (UTC+8): 2026-09-19
- Goal: Password Reset Skill 与敏感结果保护 (AES-256-GCM)
- Commit SHA: d07903b
- Changed files:
  - backend/governance/encryption.py
  - backend/governance/executor.py
  - backend/integrations/tool_adapters.py
  - skills/password_reset/notification_template.md
  - skills/password_reset/skill.py
  - skills/password_reset/validate_user.py
  - tools/account.py
  - agents/action.py
  - tests/unit/test_password_reset_skill.py
  - tests/integration/test_password_reset.py
  - docs/evidence/l3/day-12.md
- Contract/permission/state/eval impact:
  - 交付全系统唯一允许的 Deterministic Skill（AGENTS.md H-16）：
    - skills/password_reset/skill.py：实现 PasswordResetSkill，固定 5 步状态序列（query_user -> validate_user -> formulate_operation -> execute_gateway -> render_notification）。
    - 步骤顺序与失败分支完全由 Python 代码固化，不可被 LLM 动态篡改或跳过。
    - 严格遵循分层架构约束，通过 WriteGateway 协议解耦后端执行器，保持 skills 模块对 backend/fastapi/sqlalchemy 的零依赖。
  - 敏感数据加解密与隐私防护（AES-256-GCM）：
    - backend/governance/encryption.py：实现 ResultEncryptor，采用 AES-256-GCM 认证加密保护敏感凭据，支持 12 字节随机 Nonce。
    - backend/governance/executor.py：执行结果在落入 tool_operations.encrypted_result 前进行加密，缓存命中（CACHE_HIT）时自动解密并回填强类型结果，避免明文密码入库。
    - tool_calls.result_summary 经脱敏器处理，明文临时密码被掩码为 ***，杜绝日志与审计流水泄漏。
  - 密码重置业务领域与持久化适配器：
    - tools/account.py：实现 reset_password 领域工具函数。
    - backend/integrations/tool_adapters.py：实现 SqlCredentialService，使用 Argon2 安全哈希更新 users.password_hash，并递增 users.version。
  - 端到端闭环验证：
    - 单元测试与真实 PostgreSQL 数据库测试覆盖完整快乐路径、非激活用户拒绝、跨用户拒绝、操作账本幂等 Replay 缓存命中、以及密文存储不可逆防泄漏。

## Automated evidence

| Command | Exit code | Raw summary |
|---|---:|---|
| uv run ruff check . | 0 | All checks passed! |
| uv run mypy --python-version 3.12 agents tools backend eval scripts tests/unit/test_password_reset_skill.py tests/integration/test_password_reset.py | 0 | Success: no issues found in 65 source files |
| uv run pytest tests/unit/test_password_reset_skill.py -q | 0 | 7 passed in 0.36s |
| uv run pytest tests/integration/test_password_reset.py -q | 0 | 3 passed in 1.70s |
| uv run pytest tests/unit/test_dependency_boundaries.py -q | 0 | 5 passed in 0.08s |
| uv run pytest tests/unit -q | 0 | 189 passed in 3.09s |
| uv run pytest tests/integration -q | 0 | 67 passed in 17.52s |
| uv run python scripts/validate_agent_contracts.py | 0 | Agent and Prompt contracts are valid. |
| uv run python scripts/validate_golden_dataset.py | 0 | 20 cases valid across 5 intents and 8 tools |

## Real-service evidence

| Service or case | Result | Evidence location |
|---|---|---|
| Deterministic Step Sequence | Fixed 5 steps verified: query -> validate -> formulate -> execute -> render | tests/unit/test_password_reset_skill.py |
| Inactive / Cross-User Rejection | Inactive user / cross-user fails before write gateway execution | tests/unit/test_password_reset_skill.py, tests/integration/test_password_reset.py |
| Password Argon2 Hashing & Version Bump | User password_hash updated with Argon2, users.version incremented | tests/integration/test_password_reset.py |
| AES-256-GCM Ledger Encryption | Plaintext password never stored in DB; encrypted_result decryptable only with key | tests/integration/test_password_reset.py |
| Audit Redaction Non-Leakage | tool_calls.result_summary['temporary_password'] == '***' | tests/integration/test_password_reset.py |
| Skill Idempotent Replay | Second execution returns cached secret without calling credential service or re-hashing | tests/unit/test_password_reset_skill.py, tests/integration/test_password_reset.py |

## Human review

- AGENTS.md H-16 Compliance: 确认系统严格仅包含 password_reset 这一项 skill，其步骤与失败分支完全在 Python 中固化。
- Zero Plaintext Secret Leakage: 验证数据库表、审计日志、追踪上下文均不记录未加密密码明文。
- Replay Correctness: 验证重放同一个 operation 时，网关解密 ledger 中的密文并正确还原 SecretStr，确保用户能够再次获得同一条重置通知而不会破坏密码状态。

## Remaining gaps

- Day 12 完成。进入 Day 13（Refund 写 Tool 与金额边界）。
