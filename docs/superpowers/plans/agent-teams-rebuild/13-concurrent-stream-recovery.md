# [Agent Teams][AT13] 切换多成员过程并在断线后连续回看

发布状态：已批准并发布至 [#138](https://github.com/elecvoid243/AstrBot_for_spzx/issues/138)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT07 · #132](https://github.com/elecvoid243/AstrBot_for_spzx/issues/132)、[AT08 · #133](https://github.com/elecvoid243/AstrBot_for_spzx/issues/133)、[AT11 · #136](https://github.com/elecvoid243/AstrBot_for_spzx/issues/136)  
对应规范用户故事：23、32、36、57、58、59

## 构建内容

用户可在并行 Agent、子作用域和循环轮次之间切换，刷新或断线后继续读取同一过程而不丢失、重复或串流。

## 验收标准

- [ ] 任务树可定位协调者、动态成员、子流程和循环轮次，过程按稳定归属筛选而非成员显示名。
- [ ] 快照读取与订阅续接使用一致游标边界；交界到达事件不会丢失，重连重复事件不重复显示。
- [ ] 实时增量与持久消息按同一身份归并，重试／恢复的不同尝试清楚区分。
- [ ] 切换 Agent、关闭页面或没有观察者时后台继续运行并保存记录；再次进入可恢复当前状态。
- [ ] 多 Agent 同时输出时不互相覆盖；大量历史按需分页，缓冲和订阅数量有界。
- [ ] 运行中的后台更新保持选中成员、页签和用户滚动意图，错误／重连只影响相关提示而不替换整页。
- [ ] 记录缺失、截断和远端观测限制可见，不以重连动作重新执行已完成任务。

## 测试决策

使用并发屏障、受控事件源和真实保存记录覆盖快照订阅交界、重复／迟到事件、多个订阅、子流程同名成员、循环及长历史；浏览器验证切换和重连稳定性。

## 被以下阻塞

- [AT07 · #132](https://github.com/elecvoid243/AstrBot_for_spzx/issues/132) — 在画布编排有界循环并查看每轮结果
- [AT08 · #133](https://github.com/elecvoid243/AstrBot_for_spzx/issues/133) — 调用固定版本的子工作流与自主团队
- [AT11 · #136](https://github.com/elecvoid243/AstrBot_for_spzx/issues/136) — 在运行工作台流式阅读 Agent 的真实过程
