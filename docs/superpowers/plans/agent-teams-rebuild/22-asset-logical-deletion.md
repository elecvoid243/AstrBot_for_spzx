# [Agent Teams][AT22] 删除资产并保留已有固定引用与运行历史

发布状态：已批准并发布至 [#147](https://github.com/elecvoid243/AstrBot_for_spzx/issues/147)（2026-11-02）。  
标签：ready-for-agent  
父 Issue：[#125 · Agent Teams 重构规范](https://github.com/elecvoid243/AstrBot_for_spzx/issues/125)  
Blocked by: [AT09 · #134](https://github.com/elecvoid243/AstrBot_for_spzx/issues/134)、[AT20 · #145](https://github.com/elecvoid243/AstrBot_for_spzx/issues/145)  
对应规范用户故事：53、54、55

## 构建内容

用户从角色、自主团队或工作流列表删除整个资产，新的直接使用被阻止，既有固定调用、运行和历史继续可用。

## 验收标准

- [x] 三类资产均有明确可发现的删除入口和影响说明；成功后列表更新，刷新仍保持删除。
- [x] 删除针对整个资产，其所有版本退出可用列表与新的选择范围；不提供单版本删除。
- [x] Dashboard 和聊天不能直接新启动已删除资产，已有聊天绑定不能绕过限制。
- [x] 新配置不能新增对已删除资产的引用；既有固定版本引用继续有效，已有父流程可按固定版本调用它。
- [x] 已开始的运行不因删除取消，历史、产物及恢复所需版本数据保留；不级联物理清空。
- [x] 重复删除、并发启动／保存、无权删除和失效对象返回可解释结果，不出现半删除状态。
- [x] 导出、导入选择和依赖读取遵循同一删除语义，不重新暴露已删除资产供直接选择。

## 验收记录

2026-11-03 已通过协调者验收并集成。实现提交、验证范围及后续集成证据见 [README 已验收进度](README.md#已验收进度)。本地验收记录不代表 GitHub Issue 已关闭。

## 测试决策

真实资产／运行与聊天授权链路覆盖三类删除；删除子资产后启动已有父固定版本、恢复已有运行并读取产物，同时拒绝新的直接启动和新增引用。

## 被以下阻塞

- [AT09 · #134](https://github.com/elecvoid243/AstrBot_for_spzx/issues/134) — 通过实际资源匹配导入导出新版资产包
- [AT20 · #145](https://github.com/elecvoid243/AstrBot_for_spzx/issues/145) — 粘贴完整聊天身份并绑定固定资产版本
