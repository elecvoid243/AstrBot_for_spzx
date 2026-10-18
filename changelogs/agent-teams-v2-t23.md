# Agent Teams v2: previous definition migration

- Added an explicit migration preview and atomic, idempotent confirmation for previous teams, members and saved workflows. Effective configuration, mapped links/layout, finite declarative loops and compatible single-result subflows are retained.
- Missing resources and unsupported previous semantics remain visibly pending and cannot launch. Existing role/asset editors create reviewed new versions; previous versions and frozen runs remain unchanged.
- Migration starts no execution, inherits no previous conversations, takes over no unfinished runs and deletes no history. The previous engine and its default entry remain available during this transition.

| Previous entry | New definition entry |
| --- | --- |
| Previous team sidebar / Members / Workflow Editor | Agent Teams → Migrate previous definitions → Assets → Teams / Workflows / Role templates |
| Previous run monitoring and history | Previous team workspace → Run Monitor / History (retained) |

## 中文

- 新增旧团队、成员与工作流定义的显式预览及事务化幂等迁移，保留有效配置、可映射连线与布局、有界声明式循环和兼容的单结果子流程。
- 缺失资源和无法表达的旧语义明确待配置，并阻止启动。通过既有角色与资产编辑器检查后保存新版本，不覆盖历史版本或已有运行快照。
- 迁移不启动运行、不继承旧长期会话、不接管未完成运行，也不删除旧历史。过渡期保留旧引擎及原默认入口。

| 旧入口 | 新定义入口 |
| --- | --- |
| 旧团队侧栏／成员／工作流编辑器 | Agent 团队 → 迁移旧定义 → 资产 → 团队／工作流／角色模板 |
| 旧运行监控与历史 | 旧版团队工作区 → 运行监控／运行历史（保留） |

Scope: #25, US02/05/06/07/48 and the migration portions of AC19/31/35 in #2. This entry does not claim completion of the composite parent acceptance criteria or the final engine switch.
