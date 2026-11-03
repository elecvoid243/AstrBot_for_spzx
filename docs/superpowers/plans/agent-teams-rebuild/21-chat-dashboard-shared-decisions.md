# [Agent Teams][AT21] 在原聊天和 Dashboard 处理同一人工请求

发布状态：已批准并发布至 [#146](https://github.com/elecvoid243/AstrBot_for_spzx/issues/146)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT17 · #142](https://github.com/elecvoid243/AstrBot_for_spzx/issues/142)、[AT18 · #143](https://github.com/elecvoid243/AstrBot_for_spzx/issues/143)、[AT20 · #145](https://github.com/elecvoid243/AstrBot_for_spzx/issues/145)  
对应规范用户故事：44、45、39、52

## 构建内容

聊天发起者在原聊天收到问题、推荐和可访问依据，或切到 Dashboard 回答，两处结果一致且只推进一次。

## 验收标准

- [x] 从聊天发起的团队／工作流保存可信原发起者及来源，动态问题和产物审批都可投递回原聊天。
- [x] 投递显示问题、选项、推荐理由及可访问的上下文／产物入口，并保留请求身份用于准确定位答案。
- [x] 原聊天与 Dashboard 回答同一个持久请求，不复制互相独立的待办。
- [x] 群聊中仅原发起者可答；不能按群 UMO 放开给所有成员，也不能用模型提交的身份字段授权。
- [x] 两端同时回答只产生一次有效决定，另端显示已处理；迟到回答、取消和权限变化给出明确结果。
- [x] 多个 Agent 同时提问时答案不串到最近一条任意消息；聊天投递重复不制造新请求或重复推进。
- [x] 原聊天投递失败或暂时离线时保留未决请求，Dashboard 可处理，恢复投递不重新创建决定。
- [x] 团队请求的长期等待和恢复行为继续有效，普通 ChatUI 的独立请求机制不被全局替换。

## 验收记录

2026-11-03 已通过协调者验收并集成。实现提交、验证范围及后续集成证据见 [README 已验收进度](README.md#已验收进度)。本地验收记录不代表 GitHub Issue 已关闭。

## 测试决策

用实际聊天入口与受控平台发送器，覆盖私聊、群聊原发起者、他人回答、同请求两端并发、重复投递和发送失败；验证原运行只接续一次。

## 被以下阻塞

- [AT17 · #142](https://github.com/elecvoid243/AstrBot_for_spzx/issues/142) — 把动态 ask_user_choice 接入持久等待与原调用接续
- [AT18 · #143](https://github.com/elecvoid243/AstrBot_for_spzx/issues/143) — 审批精确产物版本并沿画布路径返工
- [AT20 · #145](https://github.com/elecvoid243/AstrBot_for_spzx/issues/145) — 粘贴完整聊天身份并绑定固定资产版本
