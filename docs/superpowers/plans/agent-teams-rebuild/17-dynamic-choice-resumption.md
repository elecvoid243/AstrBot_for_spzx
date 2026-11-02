# [Agent Teams][AT17] 把动态 ask_user_choice 接入持久等待与原调用接续

发布状态：已批准并发布至 [#142](https://github.com/elecvoid243/AstrBot_for_spzx/issues/142)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT16 · #141](https://github.com/elecvoid243/AstrBot_for_spzx/issues/141)  
对应规范用户故事：38、39、41、42、43、45、46、49

## 构建内容

Agent 在执行中调用 ask_user_choice 后，用户能从同一待办入口回答；服务重启后仍接续原工具调用与运行。

## 验收标准

- [ ] 可信团队执行绑定把动态工具请求关联到原任务、尝试和工具调用，接入 AT16 的公共人工请求机制。
- [ ] 问题、选项、说明、明确推荐与理由持久保存；动态推荐由提问 Agent 给出，不由系统另起隐藏模型调用。
- [ ] 等待不只依赖内存 Future；回答作为对应原工具调用结果被消费，不重跑整个节点或新启动工作流。
- [ ] 请求创建、答案提交和答案消费各边界真实重启后仍能恢复；已经确认完成的模型／工具步骤不重复执行。
- [ ] 暂停、取消、重复回答与迟到事件遵循统一状态规则；未知外部效果仍保留既有阻塞及人工处置语义。
- [ ] 团队请求默认无期限，不被普通 ChatUI 的默认 timeout、fallback 或 GC 提前结束。
- [ ] 普通 ChatUI 的 ask_user_choice 开关、自由文本、超时回退及既有协议保持原行为；新的等待策略只由服务端团队执行上下文选择。
- [ ] 不支持必要恢复能力的执行器明确报告能力限制，不能假装完成接续。

## 测试决策

从真实内置工具 call 经过实际团队执行管线、持久请求、公开回答接口回到原调用；复用外部调用账本和真实进程退出先例，另跑普通 ChatUI 选择工具回归。

## 被以下阻塞

- [AT16 · #141](https://github.com/elecvoid243/AstrBot_for_spzx/issues/141) — 在统一待办中回答画布预设人工问题

## 进一步说明

#72 是已存在的内置工具先例，不是新增阻塞；#71 普通会话协作明确不属于本票。
