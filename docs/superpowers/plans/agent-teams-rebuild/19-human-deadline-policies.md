# [Agent Teams][AT19] 按已展示推荐处理可配置的人工超时

发布状态：已批准并发布至 [#144](https://github.com/elecvoid243/AstrBot_for_spzx/issues/144)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT17 · #142](https://github.com/elecvoid243/AstrBot_for_spzx/issues/142)、[AT18 · #143](https://github.com/elecvoid243/AstrBot_for_spzx/issues/143)  
对应规范用户故事：46、47、48、45

## 构建内容

用户为预设问答、动态选择或产物审批设置期限，决定超时后继续等人还是采用创建时已展示的推荐。

## 验收标准

- [ ] 所有人工请求默认无限期等待；只有明确设置期限且选择自动策略时才自动决定。
- [ ] 超时策略只有继续等待人工或采用已有有效推荐；界面清楚显示当前策略和实际期限。
- [ ] 创建请求时固定有效选项／动作及推荐理由并展示，超时后消费该推荐，不再次调用模型生成判断。
- [ ] 推荐缺失、动作无效或证据版本不符时继续等待并解释原因，不猜测或静默批准。
- [ ] 普通问答、动态选择和产物版本审批均按同一规则执行；记录标明人工决定或自动采用模型推荐。
- [ ] 期限随请求持久化，重启不重新计时；已过期恢复仍遵守当前取消、暂停和决定状态。
- [ ] 人工回答、超时和取消竞争只允许一次有效决定，暂停时即便记录决定也不释放执行许可。
- [ ] 不改变普通 ChatUI 既有超时回退配置或引入全局自动审批开关。

## 测试决策

使用可控时钟、真实请求状态和并发屏障覆盖两种策略、三类请求、无推荐、重启跨期限、暂停与取消竞争；通过模型调用账本证明没有超时隐藏调用。

## 被以下阻塞

- [AT17 · #142](https://github.com/elecvoid243/AstrBot_for_spzx/issues/142) — 把动态 ask_user_choice 接入持久等待与原调用接续
- [AT18 · #143](https://github.com/elecvoid243/AstrBot_for_spzx/issues/143) — 审批精确产物版本并沿画布路径返工
