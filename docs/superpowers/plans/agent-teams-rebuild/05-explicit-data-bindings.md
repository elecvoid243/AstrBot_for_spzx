# [Agent Teams][AT05] 通过字段选择串联 Agent 与结构化输出

发布状态：已批准并发布至 [#130](https://github.com/elecvoid243/AstrBot_for_spzx/issues/130)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT02 · #127](https://github.com/elecvoid243/AstrBot_for_spzx/issues/127)  
对应规范用户故事：16、17、18

## 构建内容

用户把两个 Agent 串联，通过可视化选择器让下游读取指定上游字段，并按需定义结构化输出。

## 验收标准

- [ ] 输入和输出节点具备真实运行契约，Agent 默认文本输出可直接供下游引用。
- [ ] 需要结构化输出时通过表单定义字段及类型，不强制手写 JSON Schema。
- [ ] 输入侧栏按上游节点、字段名、类型选择数据来源，不要求手写路径或逐字段拉线。
- [ ] 控制连线只决定执行顺序，未选中的上游输出、对话与工具过程不会自动注入下游。
- [ ] 后端统一验证缺失字段、类型不匹配和无效引用，UI 定位到对应节点／输入。
- [ ] 删除、改名和编辑结构后，失效引用可见且启动前阻止无效执行；撤销能够恢复有效关系。
- [ ] 两个 Agent 实际运行后，下游可观察输入与选择器配置一致，最终产物可读取。

## 测试决策

真实公共图运行 + 可控执行器观察下游输入；覆盖文本默认、结构化字段、未选择敏感片段不注入、错类型和被删除引用。

## 被以下阻塞

- [AT02 · #127](https://github.com/elecvoid243/AstrBot_for_spzx/issues/127) — 从一个直接配置的 Agent 创建并运行工作流
