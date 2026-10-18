# Agent Teams v2: read-only legacy history

- Added a paginated **Runs → History** entry for v2 runs, original legacy runs and legacy members' long-term conversations. Legacy records explicitly show their engine and read-only status, with original results, transcript sources and saved thinking/tool content.
- Kept old records in their original stores. Historical interruptions are a reading projection that retains the original saved status. Still-live legacy executions remain live; viewing history does not cancel them or perform the final engine switch.
- Legacy history cannot resume, retry or supply member input to v2. Migration receipts locate accessible current asset versions through the normal asset service; preparing and starting a new run uses fresh member contexts and existing readiness checks.
- Updated English/Chinese instructions, navigation mappings and actual WebUI screenshots.

| Previous entry | Unified reading entry |
| --- | --- |
| Previous History / member long-term conversations | Agent Teams → Runs → History → Legacy engine / Read-only history |
| A migrated legacy definition | History → Prepare a new run from migrated asset → normal input/readiness confirmation |

## 中文

- **运行 → 历史** 统一分页读取新版运行、原始旧运行和旧成员长期会话。旧记录标明旧引擎与只读，原结果、过程来源和已有思考／工具内容均保留。
- 历史中断仅为读取投影，原始保存状态不改写；查看不会停止仍实际活跃的旧任务，也不执行最终切换。
- 旧历史不能触发新版继续、重试或成员输入。迁移回执通过公共资产服务定位当前版本，正常输入与就绪确认后发起全新上下文。

| 旧入口 | 统一读取入口 |
| --- | --- |
| 旧历史标签页／成员长期会话 | Agent 团队 → 运行 → 历史 → 旧引擎／只读历史 |
| 已迁移的旧定义 | 历史 → 用迁移资产准备全新运行 → 正常输入／就绪确认 |

Scope: #26, US34/43/46/48/49 and only this ticket's portions of AC29/31/32/33 in #2. This does not claim completion of the composite parent criteria or T25's final engine switch. The previous workspace remains available during the transition.
