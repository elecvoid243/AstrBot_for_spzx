# Chat runtime ownership

- Admit ordinary accounts to their own stop, recovery, system events, goals, interactive questions and foreground subagent follow-ups. Verify persisted sessions and thread parents for primary and orphan runs; administrator roles do not bypass daily ownership.
- Preserve the trusted account principal on goal continuations and text WebSocket messages. Recheck current account credentials before new WebSocket messages and reconnects.
- Reuse the existing Chat composer, goal sidebar and choice cards. Previous administrator-only runtime controls map to the same locations for their resource owners. Clear queued follow-ups, cards, attention state and subscriptions on account changes.
- 原有停止、恢复、目标及交互入口继续使用原位置，普通账户只能操作本人会话；管理员日常聊天也遵守同一归属规则。退出或切换账户时清理旧运行显示与待处理卡片。

- Extend the existing unified WebSocket to the public profile/model/effort selections and owned attachment references. Open **+ → Live Mode** in the existing conversation, with an owned bind and authenticated image/audio/video/file previews; preserve original voice recordings and streamed audio.
- Wait for configuration selection before queued or live dispatch; background follow-ups retain their own binding. Closing Live clears recording, playback, Blob URLs and late callbacks. Revoked credentials cannot interrupt a core run, and disconnected runs retain the original media owner.
- Move local voice uploads across filesystems without deleting the source if copying fails or registering an incomplete attachment.
- 实时对话入口现通过 **+ → 实时对话** 的现有视图；普通账户与管理员均只能使用本人会话及附件，WebSocket 沿用相同的公开配置和输入选项。

- Keep a Live turn's existing consumer and recovery identity until the core end event after socket disconnection or credential revocation. Detach closed output sockets, preserve complete text/audio transcripts and owned media exactly once, and clean recording sources after the task uses them. A stream segment completion does not end the core turn.

- Notify admitted Live turns at the existing WebChat pipeline boundary when core execution fails or is cancelled, preserving partial text/audio transcripts and owned files while releasing the background consumer. Original core errors and cancellation still propagate; other platforms retain their lifecycle.
