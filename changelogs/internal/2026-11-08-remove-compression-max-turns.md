# 更新日志 — 2026-11-08

## 移除“压缩前最多保留对话轮数”

- 删除配置项 `agent_runner.config.compression.max_turns` 及其运行时链路：`ContextConfig.enforce_max_turns`、`MainAgentBuildConfig.max_context_length`、`ToolLoopAgentRunner.reset()` 的 `enforce_max_turns` 入参。
- 上下文压缩现在只由 Token 预算触发：提供方 `compress_threshold_tokens` 优先，未设置时使用模型窗口的 82%；输入加输出预算仍须放进模型窗口。
- `trim_turns` 保留，语义收窄为“按对话轮数截断时一次至少丢弃的轮数”，不再受轮数上限钳制，仅保证不小于 1。
- 旧配置中的 `compression.max_turns` 会被忽略，并在启动时输出一次 warning；`provider_settings.max_context_length` 只作为遗留键在迁移时清理，不再映射到新配置。
- 行为影响：长会话不再有“轮”级硬上限，首次压缩时点后移，成本与延迟上界改由 Token 阈值决定。需要更早触发压缩时，请调低提供方 `compress_threshold_tokens`。
- 四语 `config-metadata`、中英文档与受影响测试同步更新。
