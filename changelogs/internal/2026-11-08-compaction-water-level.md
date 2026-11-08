# 更新日志 — 2026-11-08

## 长程任务压缩：按预算选区 + 目标水位

- 压缩触发改为 `min(窗口 × 0.8, 窗口 − 输出预算 − headroom)`；提供方的绝对阈值只作为覆盖项并被钳制到压力预算内。
- 保留尾部改为按消息预算的 `keep_recent_ratio`（默认 0.15），选区落在工具调用/结果的平衡边界，不再拆开批次；单轮内可压缩更早的已闭合步骤。
- 新增目标水位（窗口的 55%）与一次重试：一次摘要不够时在同一步再压一次，仍不达标则报告 `insufficient` 并回落到逐轮丢弃或容量错误。
- 全文压缩的 checkpoint 改为单条 user 消息（preamble + `<compacted-summary>`），轮内压缩保留 assistant checkpoint；摘要必须在输出上限内、非空、且严格小于被替换区间才会落库。
- 压缩报告新增 `shadowed_tokens`、`checkpoint_tokens`、`yield_ratio`、`passes`、`reason`、`threshold_tokens`、`retain_tokens`、`target_tokens` 等字段，供 ChatUI 后续展示。
- 本轮尚未做：面板暴露 `target_ratio` / `compaction_headroom_tokens` / `compress_threshold_ratio`，provider 级溢出恢复（P2），以及 ChatUI 的收益/次数展示。
