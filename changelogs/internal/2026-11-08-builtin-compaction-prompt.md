# 更新日志 — 2026-11-08

## 压缩提示词改为内置结构化模板

- LLM 压缩的提示词固定为内置的结构化 checkpoint 模板：8 个固定章节、terse bullets、空段写 `(none)`、保留精确路径/命令/错误串、合并旧 checkpoint 而不是层层概括。
- 删除配置项 `agent_runner.config.compression.instruction` 及其全链路：`ContextConfig.llm_compress_instruction`、`MainAgentBuildConfig.llm_compress_instruction`、Runner 入参、`/compact` 内置插件的读取逻辑。旧配置中的该键在迁移时清理，不再生效。
- checkpoint 文本用 `<compacted-summary>` 包裹，使模板中的“合并旧 checkpoint”规则对 AstrBot 自己的摘要生效；`compaction_id` 保护机制不变。
- 行为影响：摘要内容固定为英文工程文体，不再随用户语言输出；不再支持通过配置自定义提示词。需要自定义摘要时，请使用 `custom_compressor` 扩展点。
