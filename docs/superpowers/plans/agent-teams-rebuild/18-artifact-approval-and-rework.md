# [Agent Teams][AT18] 审批精确产物版本并沿画布路径返工

发布状态：已批准并发布至 [#143](https://github.com/elecvoid243/AstrBot_for_spzx/issues/143)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT07 · #132](https://github.com/elecvoid243/AstrBot_for_spzx/issues/132)、[AT16 · #141](https://github.com/elecvoid243/AstrBot_for_spzx/issues/141)  
对应规范用户故事：40、49、50

## 构建内容

用户查看待审产物的固定版本和显式评审意见，选择通过或退回，让流程沿配置的交付或有界返工路径继续。

## 验收标准

- [x] 审批请求绑定明确的产物身份与版本，预览和提交使用同一固定版本；新产物版本不会被旧批准自动覆盖。
- [x] 推荐动作及理由来自用户指定的上游 Agent 输出；独立评审使用显式评审节点。
- [x] 画布提供命名动作出口，批准和退回连接到用户配置的后续步骤，不隐式交回原作者。
- [x] 反复修改使用 AT07 的可见有界循环，新一轮审批具有新的请求和精确产物版本。
- [x] 达到循环上限或返回路径无效时显示原因，不无限执行或把退回误记为完成。
- [x] 待办与运行过程展示评审依据、实际决定、产物版本及后续路径；权限按当前请求和产物读取规则校验。
- [x] 答案重复、版本错配、取消及暂停的行为与统一人工请求一致。

## 验收记录

2026-11-03 已通过协调者验收并集成。实现提交、验证范围及后续集成证据见 [README 已验收进度](README.md#已验收进度)。本地验收记录不代表 GitHub Issue 已关闭。

## 测试决策

真实执行“撰写 v1—评审—退回—撰写 v2—通过”，断言每次读取及批准版本、命名出口、轮数上限和未批准版本不可交付。

## 被以下阻塞

- [AT07 · #132](https://github.com/elecvoid243/AstrBot_for_spzx/issues/132) — 在画布编排有界循环并查看每轮结果
- [AT16 · #141](https://github.com/elecvoid243/AstrBot_for_spzx/issues/141) — 在统一待办中回答画布预设人工问题
