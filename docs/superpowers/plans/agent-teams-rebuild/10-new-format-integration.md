# [Agent Teams][AT10] 完成新格式整体替换并移除旧兼容通路

发布状态：已批准并发布至 [#135](https://github.com/elecvoid243/AstrBot_for_spzx/issues/135)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT07 · #132](https://github.com/elecvoid243/AstrBot_for_spzx/issues/132)、[AT09 · #134](https://github.com/elecvoid243/AstrBot_for_spzx/issues/134)  
对应规范用户故事：1、12、25、56

## 构建内容

新版角色、单 Agent／多节点工作流、自主团队及资产包在同一基线可运行，旧槽位与迁移入口不再可用。

## 验收标准

- [ ] 联验 AT01–AT09 的直接资源链路，包括固定角色、覆盖、团队成员、图输入、循环及嵌套资产包。
- [ ] 移除旧 Agent Teams 迁移 UI、旧格式解析、槽位映射与兼容执行入口，不保留双读／双写或新旧模式开关。
- [ ] 普通工作流不再作为与画布并列的独立创建或执行模式；新单 Agent 工作流使用统一图契约。
- [ ] 旧资产或包不能被静默升级为新资产，错误说明需按新格式重建；本票不自动删除用户数据。
- [ ] 新格式生成的固定版本、冻结运行、产物、权限和恢复能力完整；无关普通聊天及其他功能的存储不被清理。
- [ ] 共享集成分支的相关后端、前端、API 客户端、类型和构建检查通过后才合入可发布基线。
- [ ] 核对过时测试和说明，替换旧格式功能预期为明确拒绝及新格式行为；必要入口映射随实现更新。

## 测试决策

本票是新格式大范围替换的集成门槛，复用各纵向票测试并执行完整相关回归；用公共新入口验证全部资产类型，确认旧入口不可执行。

## 被以下阻塞

- [AT07 · #132](https://github.com/elecvoid243/AstrBot_for_spzx/issues/132) — 在画布编排有界循环并查看每轮结果
- [AT09 · #134](https://github.com/elecvoid243/AstrBot_for_spzx/issues/134) — 通过实际资源匹配导入导出新版资产包

## 进一步说明

这是集成验证例外，不是一张先做全部 Schema 的水平任务。AT01–AT09 在共享隔离分支推进；不采用带兼容层的 expand-contract，遵守用户明确的无兼容要求。
