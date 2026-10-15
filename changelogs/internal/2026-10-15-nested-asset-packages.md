# Nested asset packages and immutable upgrades

- Export the complete pinned role, loop, team and workflow closure. Format 2 carries child definitions; existing format 1 single-layer packages remain supported.
- Validate all references, contracts, return tasks, depth, cycles and iteration limits before matching resources. Install the whole closure and its reviewed local bindings atomically, or retain affected entries pending configuration.
- Reuse identical versions, append upgrades under the same local asset identity and reject conflicting asset or role content under an existing identity/version. Existing runs, pinned children, artifacts and approvals retain their original snapshots.
- Keep the selected imported entry as the default local version when its closure pins another version of the same asset; every pinned reference still targets its exact installed content.
- Preserve imported resource compatibility requirements across local role edits and check current authorization before further execution. Packages carry no credentials or local authorization, run no scripts and do not install plugins or services. Historical HTML demo exports have no production compatibility promise.
- Keep task-only resource requirements within their exact asset closure, rebase slots with their reviewed bindings, and isolate installations with different effective requirements. A parent-only override upgrade reuses unchanged children; explicit unused and legacy requirements remain enforced. Task-supplied prompts stay with each task without replacing role defaults with the first parent's tool choices.
- Entry mapping: **Workflow Assets → Export workflow JSON / Import workflow package → Export asset JSON / Import asset package**. The workspace and **Edit Asset / Start Run** entries remain in place; the labels now cover complete nested assets.
- 入口对照：**工作流资产 → 导出工作流 JSON / 导入工作流包 → 导出资产 JSON / 导入资产包**。工作区及**编辑资产 / 启动运行**保留；新文案覆盖完整嵌套资产。
