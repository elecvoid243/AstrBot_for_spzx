# Unreleased

- Project load in ChatUI now only registers the directory and injects an existing `AGENTS.md`; it never generates `AGENTS.md` or initializes codegraph. The load dialog is path-only, the loading/completed bubbles are gone, and the project chip reports whether `AGENTS.md` was injected. / ChatUI 项目加载改为“选路径即加载”：仅登记目录并在 `AGENTS.md` 存在时注入，不自动生成、不自动初始化 codegraph；加载对话框只保留路径，移除加载/完成气泡，项目胶囊显示 `AGENTS.md` 注入状态。

- Keep the ChatUI activity ring attached to the active reply when another Agent sends a follow-up. Hide pending receipt placeholders, clear captured or empty completed receipts, and reconnect to the original run when needed. / 修复跨会话 Agent 追加消息导致思考转圈落在底部空白行的问题；待接收及已追加的请求占位不再抢占回复状态，必要时重新订阅原运行。

- Show running plugin-provided MCP servers in the MCP list and subagent tool policies; manage their lifecycle on the plugin page. / MCP 列表和子代理工具策略显示运行中的插件自带 MCP，启停及配置请在插件页面操作。

- Remove **Subagent orchestration mode → DAG orchestration**; existing DAG settings no longer enable DAG. Restore-default tool lists no longer include the retired `orchestrate_tasks` tool. / 移除 **子代理编排方式 → DAG 编排**，旧配置不再启用 DAG；工具恢复默认名单移除已停用的 `orchestrate_tasks`。

- Move **message-area title and subagent navigation → existing Chat toolbar**, restore compact subagent card styling, and place subagent reasoning beside the transcript on the right. Add per-invocation **Information** buttons with durable configuration snapshots (system prompt, tools, Skills and Provider ID). / 将 **消息区标题与子代理导航 → 现有 Chat 工具栏**，移除重复标题，恢复紧凑卡片样式，修复子代理思考抽屉的位置；下拉菜单新增信息按钮，按需显示调用时保存的系统提示词、工具、Skills 和 Provider ID 等配置。

- **Expand subagent card → read-only invocation page** under the session's Main conversation / Subagents navigation. Show independent Running and Background markers, group removed and legacy invocations under Cleaned up, and mount only the selected transcript. Remove the card's follow-up input. Persist and resume background progress beyond the parent reply's lifetime, and deliver an awakened WebChat agent's final answer even without an explicit send-message tool call. / **展开子代理卡片 → 只读调用子页面**，从会话顶部的主对话／子代理入口切换；独立显示运行中与后台标志，已移除及旧调用归入已清理分组，只挂载当前轨迹，移除追加消息入口；后台进度可在主回复结束后继续保存和恢复，唤醒后的普通最终回复也会送达 ChatUI。

- Unify **Ask in thread → Ask in side chat** with the saved side-chat panel, full composer and menu. Quoted chats keep their excerpt and checkpoint context; existing quoted threads remain available. / 将 **在新分支中询问 → 在侧边聊天中询问**，统一使用侧边聊天面板、完整输入框和保存菜单，保留选中文字及检查点上下文，兼容已有引用分支。

- Keep internal image attachment markers out of ChatUI reply text, completion events and recovery snapshots while preserving image display and surrounding text. / 修复 ChatUI 图片附件内部标记泄漏到回复正文、结束事件与恢复快照的问题，保留图片显示及前后文字。

- Restyle the Agent browser with icon segments, an address bar, connection badges, a framed read-only viewport and an action timeline. **Refresh snapshot → address-bar refresh icon**; light, dark, idle and narrow layouts share the same observation lifecycle. / 美化 Agent 浏览器，加入分段切换、地址栏、连接状态徽标、只读画面与动作时间线；**刷新快照 → 地址栏刷新图标**，适配浅色、深色、空闲与窄侧栏布局。

- Auto-open **Web Preview → Agent Browser** once per live browser-using reply when the current chat's workspace is closed. Preserve open views and manual dismissal; ignore history and background chats. / 当前主聊天调用浏览器工具时，工作区未打开则自动进入 **Web 预览 → Agent 浏览器**，每轮一次，尊重已打开页面及手动关闭。

- Compact file preview actions: **Preview → Rename** moves to **Preview → Edit → Rename**; **Move to recycle bin → Delete** is a red action beside Edit and Copy, with explicit recycle-bin confirmation. Remove the extra host-path/current-disk summary row. / 精简文件预览操作：**预览 → 重命名** 移至 **预览 → 编辑 → 重命名**；**移入回收站 → 删除**，以红字与编辑、复制同排显示，二次确认仍说明移入回收站，移除额外的宿主路径与当前磁盘内容说明行。

- Main and side-chat file citations share guarded navigation into the existing file pane. Side chats retain history when hidden and reopen from their menu: **Temporary chat destroyed on close → saved side-chat title**. Cancelled navigation, failed saves and conflicts preserve the current editor. / 主聊与侧聊引用共用草稿守卫和文件侧栏；**关闭即销毁的临时侧聊 → 菜单中的已保存侧聊**，取消、保存失败或冲突时保留编辑状态。

- Preserve the last Data & Logs tab when leaving the workspace, and restore playback and downloads for sent ChatUI audio and video attachments.
- Prepare direct and quoted file attachments before acquiring the session lock, with stop and LLM-enable checks around queued work. Follow streamed reasoning activity while respecting manual scrolling and search/file positioning.

- Remember the ChatUI model selection per conversation and focus the composer after switching projects. Share model state across the header, requests, and context usage display while preserving conversation reading positions and thinking effort settings.

- Generate numbered original-path notices in the image preparation stage during main-agent construction, including quoted images. After request hooks, apply the same size and format policy only to newly introduced image references and refresh their notices. Skipped and captioned attachments retain explicit status without taking a visual index; animation labels identify frame montages.
- Raise the model image input cap from 32 MiB to 64 MiB. Larger originals are skipped before reading image bytes, with a model notice retaining their paths and suggesting the file-reading tool or a smaller upload; accepted inputs still produce images strictly below 512 KiB.
- Local Agent input images are always prepared as JPEG/PNG files strictly below 512 KiB. Compliant local images are reused without copying. Transparent previews retain PNG alpha; animations become 3×3 montages. Original attachment paths remain available to tools, and event-owned previews are deleted after use without a shared conversion cache.
- Configuration mapping: **Enable image compression** (`provider_settings.image_compress_enabled`) is replaced by always-on preparation; **JPEG quality** (`provider_settings.image_compress_options.quality`) is replaced by automatic size control; **Maximum edge length** is renamed to **Input image maximum edge length** (`provider_settings.image_compress_options.max_size`). User attachments in CUA sessions follow the same limits.

- Add independent authenticated host file browsing with directory paths, parent breadcrumbs and Windows drive selection. With no project, **Agent browser → Files & Browser → Files / Web Preview**; with a project, **Workspace → Files** stays available and **Browse host folders or drives** opens other locations. Browsing and manual editing preserve the Agent project, worktree, execution directory and permissions; saving detects external changes and preserves conflicting drafts.

- File and Docs toolbars: **Delete → Move to recycle bin**. Manual file management now supports host files outside the loaded project; rename stays within the current directory. Failed trash operations preserve files without permanent deletion. / 文件及文档工具栏：**删除 → 移入回收站**；支持管理项目外宿主文件，同目录重命名，回收站失败时保留文件。
