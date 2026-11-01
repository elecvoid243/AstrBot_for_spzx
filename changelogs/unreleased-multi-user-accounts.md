# Unreleased

## Dashboard account migration

- Persist the original administrator with a stable internal identity while retaining password upgrade, TOTP, desktop login and recovery flows.
- Preserve the original WebChat owner across login-name changes, including session, project, attachment references and UMO configuration bindings.
- Bind dashboard, live-chat and plugin asset credentials to current account status and credential versions. Existing single-account login, Bot and Chat entries map to the migrated administrator; navigation is unchanged.
- Restore administrator ownership atomically from legacy backup credentials, including after a login rename.

## 管理员账户迁移

- 原单账户登录入口对应持久化的原管理员；Bot 和 Chat 入口保持原位置。
- 修改登录名不改变原聊天资源归属；原密码升级、二次验证、桌面登录和恢复流程继续使用，凭证变更后需重新登录。
