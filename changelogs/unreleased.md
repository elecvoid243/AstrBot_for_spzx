# Unreleased

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

- Added account-owned remote Chat connections and paginated read-only history. Previous sidebar Projects/Conversations sections now appear under This instance; Remote connections → Manage connections adds directly bound machines with separate project/session groups. Saved dedicated keys stay server-side, and remote authorization failures clear the affected view without ending the local login. (#155)

### Remote Chat RC04 (#156)

- Extend the former remote read-only history view with human text, ordinary busy follow-up, live snapshots and session-wide stop. Reconnect without resending; execution and history stay remote.
- Retain offline drafts; query bounded remote admission receipts after uncertain sends. Revoke result access live without cancelling accepted work; preserve inert rendering and local account isolation.

## Remote creation and Agent discovery (RC06)

- Added default-off host creation permission at Settings → Security → Remote session access. Disabling it preserves exposed sessions and communication.
- Navigation mapping: Chat sidebar → Remote connections → connection now adds New remote session; local New conversation is unchanged. Creation selects a remote project, uses remote defaults and updates selected exposure for all bindings of the same remote account.
- Ordinary Agent list/read/create tools now accept explicit direct connection IDs and binding revisions, returning project directory names and stable targets. Agent operations require both Agent switches; human operations use remote authorization independently. Read keeps bounded display projection and pagination. Remote Agent sending remains RC07.

## Remote Agent messaging (RC07)

- Extend `send_chat_message` with explicit direct connection IDs and immutable revisions. Authenticated peer provenance enters ordinary history, execution and safe busy follow-up; remote source badges show the connection and source session without a local link. Existing navigation stays unchanged.
- Require separate reverse bindings for active replies, current exposure and both instances' Agent/remote switches; preserve human-only choices and final admission invalidation.
- Share one origin authority across local sends, remote sends, concurrent branches and reverse replies. Outbound long polling supports NAT and further-hop budget relays without indirect resource access; stricter live limits only tighten the chain. Unknown acceptance retains its reservation, with no automatic resend or durable queue.
- Keep process-local coordination bounded (one-hour channels, bounded mailboxes/decisions). Expiry, lost control and restarts reject further automatic sends instead of resetting the budget.


### Remote Chat RC05 (#157)

- Extend the existing remote composer with **Attach images or files**, scoped image previews/downloads, and human choice answers. No navigation entry moves. Keep files and execution on the directly bound remote instance; retain RC04 receipt and reconnect semantics.
- Add live account/session exposure checks for pending uploads, historical and active attachments, and human answers. Rebinding discards stale previews and replies; remote failures preserve local login. Agent/tool messages cannot claim human provenance through request fields.
- 扩展原远端输入框，增加**添加图片或文件**、受限图片预览／下载及真人选择回答；导航入口不变，附件与执行保留远端，继续沿用 RC04 回执及重连语义。撤权、账号与连接隔离同样适用于附件和交互。
