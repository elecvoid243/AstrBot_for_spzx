# Ordinary account password reset and disable controls

- Extend **Bot → Accounts** with **Reset password** and a confirmation for **Disable account**. The account management entry stays in the same place.
- Atomically revoke the target ordinary account's existing credentials when its password changes or it is disabled. Keep other accounts and existing conversation ownership unchanged.
- Reuse live account-state validation for Dashboard JWTs, cookies, protected downloads and stream reconnections. Running tasks keep their existing lifecycle; ordinary file and stream features are not opened by this change.
- Resetting a disabled account's password does not enable it. Administrator credentials and recovery continue through the existing security settings.

## 普通账户密码重置与禁用

- 在原 **Bot → 账户管理** 入口补充 **重置密码** 与 **禁用账户** 确认，入口位置不变。
- 密码重置或禁用会原子撤销目标普通账户的现有凭证，不影响其他账户或已有聊天归属。
- JWT、Cookie、受保护下载与流重订阅复用实时账户状态检查。已运行任务沿用既有生命周期，本次不开放普通用户的文件或恢复流功能。
- 重置已禁用账户的密码不会启用该账户；管理员凭据和恢复仍使用现有安全设置。
