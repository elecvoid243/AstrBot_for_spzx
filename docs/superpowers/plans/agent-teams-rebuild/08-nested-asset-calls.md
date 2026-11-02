# [Agent Teams][AT08] 调用固定版本的子工作流与自主团队

发布状态：已批准并发布至 [#133](https://github.com/elecvoid243/AstrBot_for_spzx/issues/133)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT04 · #129](https://github.com/elecvoid243/AstrBot_for_spzx/issues/129)、[AT05 · #130](https://github.com/elecvoid243/AstrBot_for_spzx/issues/130)  
对应规范用户故事：23、25、32

## 构建内容

用户把已有工作流或自主团队作为一个调用节点复用，并进入其独立作用域查看执行和返回结果。

## 验收标准

- [ ] 调用节点选择明确资产版本，并配置实际输入与要返回的结果；资产后来更新不改变当前引用。
- [ ] 子工作流和自主团队在独立作用域运行，根运行、父子关系、任务和产物归属可读取。
- [ ] 画布通过调用卡与层级导航呈现子流程；运行任务树可进入成员和子任务。
- [ ] 同一资产在多个位置或嵌套作用域复用时不串用成员、输入、产物或调用结果。
- [ ] 现有深度、权限、并发与用量限制在嵌套调用继续生效。
- [ ] 父子暂停、取消、失败及恢复保持已有执行约束，不将观察页面导航当成执行控制。
- [ ] 子资产输入／输出变化或不存在的固定版本在保存或启动前给出可定位错误。

## 测试决策

复用已有子作用域、团队调用和工作流调用公共服务测试；真实嵌套至少两处同资产复用，验证固定版本、结果选择与跨作用域访问拒绝。

## 被以下阻塞

- [AT04 · #129](https://github.com/elecvoid243/AstrBot_for_spzx/issues/129) — 用预设角色创建并运行自主团队
- [AT05 · #130](https://github.com/elecvoid243/AstrBot_for_spzx/issues/130) — 通过字段选择串联 Agent 与结构化输出
