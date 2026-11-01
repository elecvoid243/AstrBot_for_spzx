# Personal projects, branches and side chats

- Enable the existing Projects, Branch, Ask in thread and New side chat controls for ordinary accounts, with ownership checks on projects, sessions, parent messages, attachments and streams. Administrators follow the same daily Chat ownership rules.
- Remap inherited message quotes to their actual branch records; preserve local attachment previews without sending browser-only blob fields to the API.
- Inherit source configuration when creating branches and threads; let a thread select an administrator profile independently. Preserve project membership, inherited attachment references and untrusted imported-history markers.
- Preserve attachment files still referenced by surviving owned branches or threads when a session is deleted; clean them after the final live reference is deleted.
- Restore owned side-chat generation after reload, and clear project rows, recent workspace paths, thread caches and subscriptions when accounts change.
- Validate plugin workspace consumption against the actual session/thread and project relationship. Preserve existing workspace semantics and API-key behavior; do not add account-specific sandboxes or configuration editors. Project extension admission is integrated with the companion tool-consumption slice.
- Previous entry → current entry: the shared administrator's project, branch and side-chat locations remain in Chat, now separately available to each account for its own resources. ZIP import/export remains gated by its own slice.
- Extend the existing owned-session configuration API to thread IDs and accept `config_id` on thread sends; regenerate the OpenAPI client and public API document.

## 个人项目、分支与侧边聊天

- 普通账户可使用现有项目、分支、选中文字追问与侧边聊天；项目、会话、父消息、附件和生成流均校验归属，管理员日常 Chat 同样适用。
- 继承的消息引用映射到分支实际复制的记录；侧聊保留本地附件预览，发送时不携带浏览器 blob 字段。
- 分支及侧边会话创建时继承来源配置，侧聊之后可独立选择管理员配置；保留项目关联、本人附件引用及导入历史的不可信标记。
- 删除会话时保留其他存活本人分支或侧聊仍引用的附件，最后一个存活引用删除后再清理文件。
- 刷新可恢复本人侧聊的生成订阅；账户切换清理项目、路径历史、侧聊缓存与旧连接，迟到响应不能回填。
- 项目工作区消费按实际父子会话及项目关联校验，保持现有工作区语义与 API Key 兼容性，不新增用户沙箱或配置编辑器。项目扩展入口与工具消费切片共同集成。
- 旧入口 → 新入口：共用管理员登录下的项目、分支及侧聊仍位于原 Chat 位置，各账户只能操作本人资源；ZIP 导入导出继续由后续切片放行。
