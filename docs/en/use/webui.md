# WebUI

The AstrBot admin panel features plugin management, log viewing, visual configuration, statistics viewing, and more.

## Expose your sessions to a remote client

Administrators enable **Settings → Security → Remote session access → Allow remote session connections**. The instance switch is off by default. Turning it off blocks new inbound and outbound remote operations, preserves configuration and leaves already started tasks running.

Every account, including ordinary accounts, can open **Chat sidebar → Settings → Remote session access**. Existing navigation entries are unchanged. Choose **Only selected sessions** (the initial selection is empty) or **All my sessions, including future sessions**, then save. This scope includes your ordinary local sessions, including archived sessions and sessions inside projects. It excludes side conversations, other accounts, and resources from other instances. Removing a session from the scope takes effect on the next request for every connection key.

Create a separate named key for each direct binding. Copy it when it appears: plaintext is shown once and is never stored on the server. Close the dialog or use **Hide key** to clear it from the panel. **Revoke** permanently invalidates that key; other keys remain usable. Account deactivation, deletion or a password reset also invalidates access. Login renames preserve ownership; rotating account login credentials requires issuing new connection keys.

![Personal remote session exposure and connection keys](./images/chat-remote-access.jpg)

An authenticated API client uses `Authorization: RemoteSession <key>` with these read-only endpoints:

| Request | Result |
| --- | --- |
| `GET /api/v1/remote-chat/projects` | Projects containing exposed sessions; no workspace paths |
| `GET /api/v1/remote-chat/sessions` | Exposed local sessions with project names |
| `GET /api/v1/remote-chat/projects/{project_id}/sessions` | Only the exposed sessions in that project |
| `GET /api/v1/remote-chat/sessions/{session_id}/history?limit=50` | Newest page of full message history, in chronological order |
| `GET /api/v1/remote-chat/sessions/{session_id}/history?limit=50&before_id={next_before_id}` | Older page; repeat while `has_more` is true |

History pages contain complete message bodies, without summary truncation. Page size is 1–200. A known session, project, message or attachment ID grants no additional access. Dedicated keys cannot call ordinary Chat, file or administrative endpoints. Global API keys, login passwords and browser sessions cannot replace a dedicated key at these remote endpoints. There is no forwarded access to other instances. Attachments, streaming and delivery are separate follow-up features; future endpoints must reuse the same live credential and exposure checks.

## Read sessions from other machines

![Direct connections grouped separately from this instance](./images/chat-remote-history.jpg)

![Manage account-owned connections](./images/chat-remote-connections.jpg)

Open **Chat sidebar → Remote connections → Manage connections**. Each local account can save multiple named bindings. Enter the remote server origin (for example `http://192.168.1.20:6185`) and a dedicated key issued by that remote account. Both instances must enable remote session connections. HTTP and HTTPS, IPv4, IPv6 and private LAN origins are supported; paths, URL credentials, query strings and redirects are rejected. The local server uses the saved key; it never sends your local login token to another server. Saved keys stay encrypted on the local server and are not returned to the browser. Rotating the local Dashboard signing secret requires replacing saved connection keys.

The former Projects/Conversations sections remain under **This instance**. Remote connections have separate groups, preserving project folders and session names. Connection IDs, rather than names, distinguish routes and selected sessions even when two machines use the same project or session ID. Select a remote session to see its connection name and server address above its history. **Load older** retrieves earlier full messages in pages of 50. Remote history has no local send, delete, branch or configuration action. Markdown is displayed without active links or embedded media; attachment, tool and interactive records remain readable as inert history until their remote transports are available.

Use **Manage connections** to rename a binding, change its address, replace its key (leave the replacement field blank to retain it), or remove it locally. Removing a binding does not delete remote sessions. The panel distinguishes unreachable servers, expired authorization, disabled remote access and unsupported protocols. A failed grant clears the corresponding accessible list and transcript, while the local login remains valid. Access is rechecked every 15 seconds, when the window regains focus, and by **Refresh access**. Account switching immediately discards old lists and history, including delayed responses. Turning the local switch off retains connection metadata and blocks management and outbound reads until enabled again. A remote grant exposes only that instance's own sessions, never its outbound bindings.

## Save and restore your Chat sessions

Use a session's **Export** action to download its ZIP archive, then use **Import sessions** in the Chat sidebar to upload, review and confirm it. The existing entries remain in the same places for administrators and ordinary accounts. Only the owner can export a session; knowing another account's session or import identifier does not grant access, including for an administrator using Chat.

The preview belongs to the account that uploaded it and can be confirmed once within 30 minutes. Restarting the service expires previews. Switching accounts clears the upload preview and cancels pending browser requests and downloads. Upload again under the intended account to restore a local archive.

Import creates new sessions, messages, attachments and side conversations owned by the signed-in account. Packaged quotes point to the restored messages. You can open the session, preview its attachments, select an administrator-provided profile and continue chatting. The existing version-1 format contains history, LLM context, attachments, side conversations and per-origin preferences. It does not contain project membership or global profile routes; select your profile again in ChatInput. A branch's origin outside the package is not reconnected to a live session.

Review the import warnings: non-UMO preferences, filesystem custom roots and Dify, Coze, DashScope or DeerFlow remote conversation identities are ignored. Missing packaged attachments and quote targets outside their imported message stream are skipped. Imported file-change and terminal text remains visible, but it does not authorize reading or restoring server files. Exporting, reimporting or branching imported history preserves that restriction.

![Import preview for a personal Chat session](/images/chat-multi-user-transfer-en.png)

## Navigation and Previous Entry Points

These paths use the current default sidebar. If you customized it, open `Settings → Appearance → Customize Sidebar` at the bottom of the sidebar to review or reset the layout.

| Previous entry point or name | Current entry point |
| --- | --- |
| Providers → Add Provider → Agent Runner | Config → Select a profile → AI → `…` beside the AI heading → Change execution mode |
| Data / Dashboard | Data & Logs → Statistics |
| Conversation Management / Conversations | Data & Logs → Conversations |
| Logs / Console | Data & Logs → Logs |
| Trace | Data & Logs → Trace |
| Config → Normal Config | Config → Select a profile |
| Config → System Config | Settings → General, Appearance, Network, or Security, depending on the setting |
| Commands / Command Management | Extensions → Handlers → Command |
| Standalone MCP / Skills entries | Extensions → MCP Servers / Skills |
| Custom Rules / Future Tasks / SubAgents | More Features → The corresponding feature |
| Agent Teams → Members | More Features → Agent Teams → Assets → Role templates |
| Agent Teams → Workflow Editor, Run Monitor | Assets → Workflows → Start Run is the default; New Workflow opens the versioned editor, and Runs displays frozen snapshots |
| Workflow Assets → Export workflow JSON / Import workflow package | Assets → Teams / Workflows → Export asset JSON / Import asset package → Validate and preview → Confirm installation; includes exact nested versions and grouped dependencies. Edit Asset / Start Run remain. Upgrades add versions; pending entries cannot run until their dependencies are ready |
| Migrate previous definitions / active legacy run check | Removed; configure versioned Assets and start runs directly. See [the asset workspace](./agent-team-workflows.md) |
| Previous History tab | Agent Teams → Runs → History, showing asset runs only |
| Legacy autonomous team sidebar / Run Monitor | Versioned autonomous assets: Assets → Teams → New Autonomous Team; Runs → View Run displays dynamic members, task relationships and complete artifacts |
| Runs → View Run → select task → Continue the same attempt | Compatible remote executors show **Query original remote run**; built-in checkpoint recovery retains **Continue the same attempt**. The action stays in the selected task's details. |
| New runs only showed waiting reasons | Agent Teams → Assets → Workflows → Edit Asset configures human questions / version approval; Runs → View Run → Human requests handles answers, previews, approvals or rejection and exact-version exports |

Old log, trace, conversation, and statistics URLs still redirect to the corresponding tabs. Agent runners are now saved in each profile; they are no longer created as model providers. See [Agent Runners](./agent-runner.md) for the setup steps.

## Accessing the Admin Panel

See [Agent Teams role templates](./agent-team-roles.md) for direct resource selection, inheritance previews and versioned saving.
See [Asset and run workspace](./agent-team-workflows.md) for saving, starting and inspecting workflows and task results.

After starting AstrBot, you can access the admin panel by visiting `http://localhost:6185` in your browser.

> [!TIP]
> - If you're deploying AstrBot on a cloud server, replace `localhost` with your server's IP address.

## Login

For first-time login, AstrBot generates a random initial password and prints it in startup logs. Please read the startup log line containing the WebUI credential and use that password to log in (username is usually `astrbot`).

## Multiple accounts and basic chat

The existing single-account login remains the original administrator's entry point. Administrators retain the **Bot / Chat** switch and can open **Multi-user management → Create account** in the Bot sidebar to set an ordinary user's unique login name and initial password. This creates an ordinary account; there is no registration, role assignment, or cross-account sharing.

| Previous entry point | Current entry point |
| --- | --- |
| Original WebUI administrator login | The same login page; administrators retain Bot and their own Chat |
| Chat through a shared administrator login | An administrator creates ordinary accounts; each user signs in on the same page and goes directly to their own Chat |

![Administrator-managed ordinary accounts](./images/multi-user-accounts.png)

Ordinary users see only chat navigation. Select **New Chat**, enter a message, and select **Send** to receive a streamed reply using the selected profile. Open one of your sidebar conversations to reopen its history. Ordinary accounts support text and multimedia conversations, profile selection, and the input parameters described below. Ordinary accounts can also rename, star, archive, restore and delete their own sessions, use batch operations, search messages, and browse older history pages and message markers. Edit and continue the latest user turn or retry the latest reply. Quoted messages must belong to the same owned session. Existing owned attachments and the selected profile, model and thinking effort are retained when editing and retrying. Projects, branches and side chats are available as described below. ZIP archives can be saved and restored as described in [Save and restore your Chat sessions](#save-and-restore-your-chat-sessions). Administrators maintain shared configuration; ordinary users do not edit instance configuration.

Use **Upload files** (the paperclip), drag files into Chat, or paste an image to send images, audio, video, and files. The microphone retains the existing recorded-audio upload flow. Large files retain chunk progress, retry, and cancellation. Preview or remove staged attachments before sending; reopen your history to preview media or select **Download** for uploaded and model-generated files. Legacy administrator attachments remain with the original administrator. Knowing another account's attachment ID, upload task, stored filename, or download token does not grant access, message reuse, or cleanup permission.

![An ordinary user sending and downloading owned attachments](./images/multi-user-attachments.png)

![An ordinary user's isolated text conversation](./images/multi-user-chat.png)

The previous basic Chat entry now exposes **Select conversations**, **Search messages**, the session **More actions** menu and **View Archive** in the same sidebar. These controls operate on the current account's data; administrators follow the same ownership rule in daily Chat. Mixed batches report unsuccessful items rather than treating them as deleted or archived. Signing out or switching accounts clears history pages, search results, unread markers and batch selections.

![Organizing an ordinary account's sessions and history](./images/multi-user-history.png)

### Personal projects, branches and side chats

Use **Projects → +** to create a project, edit its existing workspace settings, or start a conversation with the project's **Create new session** button. Drag your conversations into or out of your projects. Project lists, workspace previews and downloads belong to the current account. Project workspace modes and administrator-provided tool capabilities retain their existing meaning; this does not create a separate server filesystem or execution sandbox for each account.

For administrators using spcode, **Projects → +** opens the same project-path loader as the composer project chip. The former **Custom path** choice maps to this loader; **Project shared workspace** and **Separate by session** are no longer offered for new projects. A successful load groups the current conversation under that project. Choose **Work without a project** in the chip menu to unload it and remove the current conversation from the project. Existing workspaces remain available.

Use **Branch** below one of your assistant replies to copy the history through that reply into a new conversation in the same project. The branch starts with the source profile and keeps references to your attachments. Deleting a parent, branch or side chat preserves files still referenced by another surviving conversation. Select reply text to **Ask in side chat**, retaining the quote and context through that reply, or use **New side chat** at the upper right to start with empty context. Both entries share the side-chat panel, full composer and saved-chat menu, and inherit the parent profile when created; a side chat's configuration chip can then select another administrator profile independently. Closing a side panel keeps it saved; reopen it from the side-chat menu. Stop or delete affects only that owned thread. Reloading an active side chat reconnects to its running reply.

| Previous entry | Current entry |
| --- | --- |
| Projects, Branch and side chats under the shared administrator login | The same Chat controls, separately available to each account for its own resources |

Administrators also use only their own daily Chat projects and conversations. Signing out clears project lists, selected rows, recent workspace paths and side-chat streams; late responses cannot repopulate the next account's view. Saved project membership, branch links, side chats and profile bindings survive restarting the server.

![Personal project, branched conversation and independent side chat](/images/chat-multi-user-projects-en.png)

While a reply is running, the send button becomes **Stop** when the composer is empty. A queued text follow-up can be sent into your own running conversation or sent after interrupting it. Reopening or refreshing your conversation reconnects to its active reply; another account cannot subscribe to that run, stop it, or answer its questions, even if it knows the identifiers. Interactive choice cards belong to their actual conversation.

![An ordinary user's own interactive question](./images/multi-user-runtime-choice.png)

Use **+ → Set goal** in the existing composer to set a conversation goal. Its existing sidebar provides pause, resume, and clear actions for that conversation. Goals retain their meaning and persist after reconnecting. The previous administrator-only runtime controls now appear in the same Chat locations for each resource owner. Signing out clears pending cards, queued text, goal display, browser attention signals and subscriptions before the next account opens Chat.

You can also send `/goal set <goal>`. When a new goal starts or a paused goal resumes, its sidebar opens automatically if no reasoning, workspace, or other sidebar is open. Existing sidebars stay open, and dismissing the goal sidebar keeps it closed during later turn updates. The composer menu's **Set goal** item shows only its label. ChatUI no longer appends judge verdicts as chat messages; the latest evaluation remains available in the goal sidebar. When the goal completes, the goal button beside **Bot** in the top toolbar gains a green highlight and a checkmark. It still opens the goal sidebar.

![Completed goal toolbar button and sidebar](./images/goal-completed-toolbar.png)

![A paused goal in its owner's conversation](./images/multi-user-runtime-goal.png)

Use **Settings → Transport Mode → WebSocket** to use the same text, attachment, profile, model and thinking-effort controls over WebSocket. The default remains SSE. WebSocket also supports quoting messages from your current conversation. Queued follow-ups wait for the current conversation's profile selection; follow-ups sent to a background conversation retain that conversation's binding.

Open **+ → Live Mode**, then select the orb to start voice chat in the current conversation. The profile and input choices are prepared before it opens. Live Mode preserves the original recording and recognized text in your history, plays streamed audio, and previews or downloads generated images, audio, video and files using your account's attachment access. Closing it stops local recording and playback and clears media previews. Reopen the conversation to recover an in-progress Live reply; its text and generated media continue saving under the original account until completion. Signing out or switching accounts clears its connections; running tasks keep their existing lifecycle.

| Previous entry | Current entry |
| --- | --- |
| Administrator-only runtime controls | The same Chat stop, goal, question and follow-up controls for the owning account |
| Live Mode could not be opened from Chat | **+ → Live Mode** opens the existing Live view for the current owned conversation |

![Live voice and generated media in the owning account](./images/multi-user-runtime-live.png)

On narrow screens, use the sidebar button at the top left of Chat to open your conversation list. **New Chat**, saved conversations, and **Log out** remain available in that drawer; Bot navigation stays hidden.

![An ordinary user's mobile chat sidebar](./images/multi-user-chat-mobile.png)

Select **Log out** before switching accounts in the same browser. Conversation lists, unsent text drafts, staged attachments, upload queues, media previews, and old requests are cleared or invalidated. Daily WebChat queries and operations enforce ownership for every account, including administrators. Administrators retain full backups and operational logs, so this feature does not promise confidentiality from trusted server maintainers.

### Full-instance backup and recovery

Administrators keep the existing **Bot → Settings → Maintenance → Backup Manager** entry. Ordinary accounts cannot use full backups, restoration or operational logs, including compatibility URLs. Personal conversation ZIP export/import remains in Chat and is separate from this full-instance operation.

| Previous workflow | Multi-account workflow |
| --- | --- |
| Backup Manager → Export Backup | Same entry; includes stable account identities, password hashes, roles, enabled state, personal histories, attachments, projects, branches, side chats, saved choices and profile bindings |
| Import Backup → confirm → restart | Same entry; stop supported runtime tasks, restore, sign in with the backup administrator to inspect the actual result, restart, then sign in again |

1. Select **Export Backup → Start Export → Download Backup**. Keep the archive private: it includes all accounts and the instance's configuration and data. AstrBot-managed session/project workspaces are included. Absolute custom workspace directories outside the AstrBot data directory still require their own server backup.
2. Select **Import Backup**, choose the ZIP, then **Upload & Check**. Review the version information and overwrite warning before **Confirm Import**. The existing format accepts old single-account archives and current multi-account archives. An old archive restores its original administrator ownership; a modern archive preserves each account's identity and resource ownership.
3. Full restore puts the instance into maintenance. Supported model, Live, goal and streaming tasks stop and finish cleanup **before** data replacement; they cannot append an old reply to the restored history. A failure to stop them leaves the database untouched. This uses the official lifecycles of the core and audited plugins; it does not control arbitrary third-party threads or provide an execution sandbox. Ordinary password reset and account disable keep their existing task lifecycle.
4. Every full restore creates a new signing key and retires previous JWTs and file-download tokens, including tokens issued when an older snapshot was created. A 401 is a request to sign in again, **not** proof of success. Wait until restoration finishes, then sign in using the administrator credentials contained in the backup. The Maintenance dialog reopens the actual task receipt. If it reports failure or interruption, use **Retry**, upload another valid archive or select one from **Backup List**; no hidden-file editing is required. If restoration failed or was interrupted after data replacement began, **Restart Now** only restarts the maintenance environment and does not mean restoration succeeded. Sign in again as administrator and complete a retry with a valid backup before Chat can resume.
5. When the receipt says **Import Completed!**, select **Restart Now**. The old runtime remains stopped until restart. The existing self-hosted restart or Desktop restart bridge is used; readiness is checked without retaining the retired login. Sign in again when the login page returns.
6. Each enabled account can reopen its own Chat, preview/download its restored attachments, select its administrator-provided profile and continue chatting. Disabled accounts remain disabled. Log out before switching accounts in the same browser. Administrators retain Bot and operational backup/log access, while daily Chat still cannot access another account's conversations.

Imported histories remain marked as imported, and native file-change authority is preserved as recorded. A full backup does not turn an imported tool card into permission to inspect or undo a server file. Only valid extracted members of a legacy archive are registered as legacy attachments; unrelated files already on the server are never granted access by restoration or repeated startup.

![Administrator checks the actual full-restore result before restarting](/images/multi-user-full-restore-en.png)

### Manage ordinary accounts

As an administrator, open **Bot → Multi-user management**, find the ordinary account and select **Reset password**. Enter a new password that meets the displayed policy and confirm. The user must log in again with the new password; the old password and existing login credentials stop working immediately. Existing conversations stay with the same account. Resetting a password does not enable a disabled account.

Select **Disable account** and review the target login name and consequences before confirming. The status becomes **Disabled**. The account cannot log in, and its old credentials cannot send messages, download protected resources or subscribe to chat streams again. Running tasks keep their existing lifecycle. Select **Enable account** to restore access with a fresh login, or **Delete account** to permanently remove the account and its owned data after confirmation. These controls manage ordinary accounts only; they do not change roles or disable administrators.

![Administrator controls in Multi-user management](./images/multi-user-accounts.png)

When a subsequent request reports an invalid credential, the user interface ends that identity's access, clears visible chat state and drafts, and returns to login. Revoked credentials also lose access to protected attachments. Reconnecting a chat stream validates the current account and credential version.

### Choose a profile and chat input parameters

Use the configuration chip beside the input to select a profile created by an administrator. The selection belongs to the current conversation, including the first message; choosing `Default Configuration` explicitly overrides broader routing rules. A new conversation starts with your last profile choice. Reopening a conversation restores its own saved binding.

For profiles using the local agent runner, the existing model menu lets you choose an enabled, administrator-configured chat model. The thinking-effort menu selects an existing preset and a value for the current ordinary conversation. The server saves the provider, model, preset and effort value per conversation across refreshes, browsers and devices. Conversations without an explicit choice keep the existing account and shared defaults. Quick messages can be added, edited, reordered or deleted through Quick messages → Edit quick messages; changes affect only the current account. These choices do not change shared configuration. Changing accounts clears the live selection and loads only the signed-in account's conversation settings.

| Previous behavior | Current behavior |
| --- | --- |
| Configuration chip reads management configuration/routes | Same chip selects a profile through an owned-conversation API |
| Thinking-effort selection changes the instance default | Selection is saved for the signed-in account; administrators separately edit shared preset definitions |

Only administrators can create or edit profiles, providers, model definitions, and thinking-effort presets. Each account edits and saves its own quick messages. Ordinary users see consumption controls without configuration editing, provider connection tests, or management pages. Profile metadata contains only its ID, name and runner type; model choices omit credentials and server endpoints.

![An ordinary account selects a profile, model and thinking effort](./images/chat-account-header.png)

### Consume tools and their results in your own chat

The chat input keeps the existing **Skills** menu, managed-terminal indicator,
project status and workspace entry. A skill selection is a one-shot request for
this conversation; it does not edit a skill, plugin or shared configuration.
Tool availability still comes from the administrator's selected profile and the
existing enablement rules. Service management remains administrator-only.

Open a terminal from the indicator to follow its output, stop it or clean up a
finished record. These actions belong to both the conversation and the terminal
task; knowing another task ID does not grant access. Switching accounts closes
old output windows and clears skill queues, result cards and extension caches.

File-change cards use their original conversation and generated message/run.
Diff and undo reject a different path, backup or source record. If the file has
changed since that result, the old card cannot read the replacement contents or
overwrite them. A deletion can be undone only when its exact recycle entry and
original file or directory evidence are still available. Old deletion records,
ambiguous concurrent deletions and changed recycle objects keep their display
but have no undo action. Imported history is display-only for these actions.
The same ownership rules apply to an administrator's everyday Chat use.

![An ordinary account reviews a file result, terminal output and skill guidance](./images/chat-tool-results-en.png)

Update the spcode and Browser Use plugins together with the host when using
these extensions. The host accepts only the actual handlers that advertise the
ownership guard; an older installed handler cannot gain access through the same
URL. The old `/api/plug/…` entry points use the same checks. The deleted-file
restore alias delegates to the core's exact-record check. spcode filesystem
consumption stays inside the owned conversation's resolved workspace; its
home/drive shortcuts point to that workspace. Git revision exports download as
owned attachments. Ordinary accounts do not gain plugin management, global
service status, arbitrary server-file browsing or permission-root editing.

| Previous entry or behavior | Current account-aware behavior |
| --- | --- |
| Chat tools hidden while multi-account support was introduced | The same Chat input and workspace expose supported consumption controls |
| Skill palette reads the Skills management list | Owned-conversation skill inventory, with management still in Extensions |
| File-change request uses only a path/backup/hash | Original owned message or run authorizes the recorded file |
| Deleted-file undo selects the newest matching path | Only the exact recorded recycle object can be restored |
| Git historical file opens on the server | Export downloads through the account's attachment boundary |

The instance still shares its configured tools and runtime. This is application
resource ownership, not a separate filesystem or execution sandbox per account.
Administrators retain the documented operational backup and log capabilities.

## Open Agent file references

In the main chat, click an explicit file link in an Agent reply to open the
existing file pane. File consumption validates the current account and the
conversation's resolved workspace. An arbitrary server path or a reference
copied from another account does not authorize a file read. Reopening a supported
reference reads the current owned workspace file; generated attachments use the
separate owned preview/download boundary. This does not change Agent permissions.

Use `[name](G:/project/main.py)` for a whole file, `#L12` for one source line,
or `#L12-L18` for a closed source-line range. POSIX host paths such as
`[source](/home/user/main.py#L12)` are supported too. Use forward slashes and
percent-encode spaces and special filename characters, for example
`[notes](G:/My%20Project/notes%23draft.md#L2-L4)`. Plain path text, code blocks,
relative links, and web links keep their existing behavior. Incomplete streamed
links do not read files.

![A file reference opens current source lines in the existing pane](./images/file-reference-preview.png)

File references use authenticated spcode
services with the trusted conversation identity and resolved workspace. Install or enable spcode if
the pane asks for it. Missing, unreadable, unsupported, oversized files and invalid
line ranges display an explicit state; an invalid range is not highlighted.
Markdown references with a line target show source text so line numbers remain
meaningful. The existing project Files, Git, Documents, Terminal and Browser
entries remain in the same workbench.
With a project loaded, click **Files** to leave a reference and return to the
project file list. From **Git Diff**, use a file's **Open → Open in file browser**
menu to open that project file instead of the previous reference. These explicit
actions preserve the existing check for unsaved edits; background project or
worktree refreshes leave the reference in place.

### Preview images, documents and spreadsheets

Absolute references to images, PDF, DOCX, XLSX, CSV and Markdown open the
existing viewers in the same file pane, inside the owned project or conversation workspace. Whole CSV files open as tables; CSV and Markdown
citations with line targets display **source text**, including quoted multiline
CSV rows. Images, PDF and Office documents show an explicit message when a
text line target cannot be located. These previews do not add Office, PDF or
image editors.

Binary previews retain the existing extension/MIME whitelist and 50 MiB limit;
source text retains its 5 MiB limit. Encoded spaces, Chinese names, `#` and `%`
refer to the actual filename. Reopening a citation reads current disk bytes.
Changing targets cancels or ignores old byte/parse requests and releases image
URLs and PDF tasks. spcode must remain enabled for both metadata and bytes.

![A real PDF citation in the existing file pane](./images/file-reference-rich-preview.png)

### Edit files and handle conflicts

Click **Edit** in a text preview to edit and save text up to 2 MiB within the conversation's resolved workspace, including its session workspace when no project is loaded. Manual saves validate the signed-in account and workspace and preserve the original encoding, BOM and dominant newline style. They leave Agent permissions, allowed directories, project and worktree unchanged.

Leaving a draft through a citation, directory, path, workbench tab, sidebar or session offers **Save and open**, **Discard and open**, or **Cancel**. A failed or conflicting save stops navigation and keeps the complete draft. Browser refresh and closing use the browser's own leave warning.

When another program changes the file, a normal save leaves disk contents intact. Choose **Reload from disk** to discard the draft, or **Overwrite confirmed version** to save it against the disk revision you confirmed. Another disk change causes another conflict. Text entered while a save is pending remains unsaved even after the earlier request succeeds.

Background worktree changes, project unloads and project switches keep the displayed file and draft. Polling does not close the editor. Use an explicit citation, workbench tab or return to the current project files to leave through Save, Discard or Cancel.

![A conflict preserves the complete editor draft](./images/file-edit-conflict.png)

This is optimistic concurrency control: the service rechecks the disk revision after staging and before replacement, but cannot lock arbitrary external editors out of the final check-to-replace interval. Missing files, permission, encoding and write errors preserve the draft and show a failure. The old project-relative write API retains its original scope.

### Rename files and use the recycle bin

Choose **Edit** at the top of the file preview, then **Rename** in the editor toolbar to change a file's name within its current directory, within the owned conversation workspace. Entry mapping: **Preview → Rename** moves to **Preview → Edit → Rename**. Confirmation shows the fixed full target path. Name collisions, permission errors and missing targets are reported. The sidebar follows a successful rename; old chat citations keep their original paths.

The file preview toolbar uses a red **Delete** button (previously **Move to recycle bin**). Its confirmation still states that the file will move to the system recycle bin, offers **Move to recycle bin**, and explains recovery through the operating system. Owned workspace files and the Docs sidebar use this behavior. If trash is unavailable or refuses the operation, the file is preserved; there is no permanent deletion fallback.

The file preview puts **Edit**, **Copy**, and **Delete** in one row and removes the extra path and current-disk-content summary. Hover over the filename to see its full path. Invalid or unsupported citation line ranges still display a notice.

![Compact file preview actions](./images/file-preview-toolbar.png)

For unsaved changes, first choose **Save and open**, **Discard and open** or **Cancel**. Cancellation, failed saves and conflicts stop the management action. The target stays fixed during the request. Manual management uses WebUI authentication and the owned workspace boundary without changing the Agent's project or permissions.

Windows requires Windows 8 or later and the plugin's `pywin32` dependency for safe recycling on local fixed drives. Unsupported locations or missing native support return an error before deletion; no legacy deletion backend is used. Rename also fails if the operating system or filesystem cannot guarantee that an existing destination will be preserved.

![ Confirm the actual file before moving it to the system recycle bin ](./images/file-trash-confirm.png)

### Browse host folders and drives

Without a project, open **Files & Browser** above the composer to see **Files** in the current conversation's workspace. Use the folder picker and home/drive shortcuts to browse other locations; selecting a location changes the file pane without loading a project.

Click the last breadcrumb to type an absolute file or directory path, or a path relative to the currently listed directory, and press **Enter**. Files open in the existing preview. Paths and parent navigation must stay inside the owned workspace; outside paths are rejected. The path bar shows the actual resolved location. Missing or unreadable paths display an error; large directory listings show the existing 1000-entry limit.

To the right of the path, **Back to the previous directory** (left arrow) retraces directories visited in the current file browser, independently of **Parent directory**. Opening files within the same directory and failed directory loads do not add history; Back is disabled when history is empty. **Return to the loaded project directory** (folder with a house) opens the loaded project's root and is disabled without a project. Both actions check unsaved edits first; cancelling preserves the current file and back history.

With a project loaded, **Workspace → Files** still returns to its file list. File browsing and file references share one navigation lifecycle. Background polling preserves an open file or draft, while each read or save validates the current account and workspace. Git Diff/History, Documents and Terminal retain their project context; Web Preview and the Agent browser remain available.

| Previous entry | Current entry |
| --- | --- |
| Agent browser without a project | Files & Browser → Files / Web Preview |
| Project-relative path input | File pane breadcrumb → host absolute path or path relative to the listed directory |
| Unrestricted host folder and drive shortcuts | Existing shortcuts open the owned conversation workspace |

![Back and return-to-project buttons in the file browser](./images/file-browser-navigation.png)

## Compact Git changes

In **Project Workbench → Git Diff**, smaller file checkboxes, text and action buttons reduce the space taken by large change lists. File checkboxes are smaller than directory checkboxes. Stage, Unstage, Restore and the Open menu remain available.

Consecutive directories with no direct changed files and exactly one child stay combined in one row, such as `dashboard/src/components/chat/message_list_comps`. Clicking it expands or collapses the terminal directory's contents. Branching directories and directories with direct files keep their own rows. Previous **Expand levels → click the combined directory to expand files**; Expand all also preserves combined paths. Counts, change totals and directory selection cover the full subtree, while file actions retain their original full paths.

![A compact Git change list with combined directory paths](./images/git-diff-compact-tree.png)

## Switch Git worktrees

In the **Files / Git Diff / History** tabs of **Project Workbench**, the **Worktree** controls adapt to the sidebar width. Buttons remain visible when they fit within two rows. If they need more rows, the worktree dropdown appears alongside **Current branch** in the same row. Open it to choose another worktree. Widening the sidebar or removing worktrees automatically restores the buttons when they fit. Narrow sidebars hide the text labels and refresh description while keeping both selectors and the action icons.

Entry mapping: the dropdown layout's previous **Worktree row → +** action moves to **Worktree dropdown → New worktree**. The button layout retains **+**. Both layouts keep right-click management on each worktree. The dropdown includes full paths to distinguish worktrees with the same name.

![Branch and worktree menus sharing one row](./images/worktree-shared-toolbar.png)

## Two-Factor Authentication

AstrBot WebUI supports TOTP (Time-based One-Time Password) based two-factor authentication.

### Enabling Two-Factor Authentication

1. Open `Settings → Security` at the bottom of the sidebar and find `WebUI Security`.
2. Toggle on "Enable WebUI TOTP"; a setup dialog with a QR code will appear.
3. Scan the QR code using any TOTP-compatible authenticator app (e.g., Google Authenticator, etc.).
4. Enter the 6-digit verification code generated by the authenticator app to complete the verification.
5. The system will generate recovery codes for logging in if the authenticator device is lost. Be sure to save them securely.

To replace the TOTP secret, you can do so in the TOTP management window, where you will need to verify the current TOTP code first.

### Recovery Codes

- Each recovery code can be used only once. After logging in with a recovery code, two-factor authentication will be automatically disabled, and you will need to set it up again.
- Recovery codes can be regenerated in the TOTP management window.
- If you lose the recovery codes, you will not be able to regain account access through normal means. You will need to manually edit `data/cmd_config.json`, set `dashboard.totp.enable` to `false`, and manually clear `dashboard.totp.secret` and `dashboard.totp.recovery_code_hash` to disable two-factor authentication.

### Security Related

- Once two-factor authentication is enabled, modifying TOTP-related configurations requires verifying the current TOTP code to prevent unauthorized changes.
- Changing the admin panel password will revoke all trusted devices.

## ChatUI

AstrBot includes a built-in ChatUI for talking to configured models directly in your browser.

Assistant replies in the main chat no longer have a left-hand avatar, leaving more width for the text. Once a reply finishes, intermediate work folds into a subtle completed-work capsule with a thin checkmark and emphasized duration. Click it to expand or collapse the work as before.

![Completed-work capsule and replies without avatars](./images/chat-work-capsule.png)

The model menu in the header and the thinking-effort menu save choices on the server for the current ordinary conversation. Refreshing, switching conversations or opening another browser restores that conversation's choices. Browser model caches are no longer used for ordinary conversations. Choosing the default model clears its override. A one-turn explicit model or effort parameter, including a regeneration override, applies only to that request. Scheduled turns and goal kickoff/resume/continuation use the same session settings without a browser. Unset effort uses the account's existing preference, then the shared default; `auto` retains the provider's configured reasoning parameters. Values remain provider-specific, including custom text and numeric budgets. A removed or disabled provider produces an error; select another model or clear the override. Save failures show an error and block sending until a valid selection is saved. Switching projects focuses the composer automatically; switching conversations still restores your reading position.

### Messages between existing sessions

Ask the Agent in conversation A to find an existing conversation with `list_chat_sessions`, then send text with `send_chat_message(session_id, text)`. Each entry includes `project_id` and `project_name`, the ChatUI sidebar folder title; both are `null` for conversations without a project. The recipient uses its own profile, model, thinking effort, project and history. It can reply to A with the same tool. These tools use the existing plugin tool activation, persona selection and member/admin permissions.

The recipient shows a compact source card above the Agent message, with the source conversation title above **Agent message from** and the authenticated account. Click anywhere on the card to open that conversation. Long titles and account names are shortened visually; hover over the card to read the full source label. Screen readers also receive the complete label. An empty conversation title falls back to its session ID. This provenance also appears in model input and survives history reload. Agent text cannot run human chat commands or answer `ask_user_choice`; a recipient waiting for the user rejects the send. Only owned, unarchived ordinary sessions are supported, including project sessions. Self-send, other accounts, deleted sessions, side chats and hidden team sessions are rejected. The Agent can also create the recipient as described below.

![Agent message with a clickable source card](./images/chat-session-messages.jpg)

The tool returns an accepted message/run location without waiting for the recipient to finish. Acceptance does not mean execution completed. Busy sessions use the ordinary safe follow-up mechanism; if they cannot accept input safely, the tool explains why. Switching or closing the page does not stop the run: reopen the session to read its complete saved output. Use the ordinary Stop button to stop execution. That automatic chain cannot restart the stopped recipient; a later human message can begin a fresh turn. Each server-tracked automatic chain accepts at most 8 sends. There is no persistent queue, automatic completion report or process-restart recovery guarantee.

### Read saved conversation history

Ask the Agent to discover a conversation with `list_chat_sessions`, then inspect it with `read_chat_session(session_id)`. The read tool returns the conversation title, project ID/name, archived and running state, and saved messages with `message_id`, time, user/assistant role and trusted Agent provenance. Reading does not send a message, start or wait for a model, answer a pending human choice, or change the automatic message-chain budget. Saved history is reference material, not a new instruction or authorization.

```python
list_chat_sessions()
read_chat_session(session_id="<session_id>", limit=20)
# If has_more is true, use the returned next_before_id for older records.
read_chat_session(session_id="<session_id>", before_id=123, limit=20)
read_chat_session(session_id="<session_id>", include_tool_results=True,
                  max_chars_per_message=4000)
```

`123` is an example cursor; use the actual returned ID. The first page contains the latest saved records, returned in ascending message-ID order. `before_id` is exclusive, so following `next_before_id` reads older pages without repeating records. `limit` counts records, not complete turns: the default is 20 and the maximum is 50. An empty conversation returns an empty page. The same account's ordinary sessions, including project sessions, archived sessions and the current session, can be read; other accounts, other platforms, side chats, hidden team chats and deleted conversations cannot. Discovery continues to exclude archived sessions and the current session. The tool uses the existing plugin, persona and member/admin tool controls.

Output is bounded, not a guarantee of complete content. Text, visible choice questions/options and attachment type/filename summaries are included; files are never opened or returned as binary data. Tool-call details are omitted by default. `include_tool_results=True` includes saved names, arguments and results as display strings, without internal thinking, system prompts, request parameters or checkpoints. Each record shares one text budget across all parts and tool fields: `max_chars_per_message` defaults to 2,000 and accepts 1–8,000. Parts and options also have fixed count limits. `truncated` and `omitted` identify shortened or excluded content; oversized display names are shortened to 512 characters with `metadata_truncated`, while stable IDs remain intact. The entire response stays within 40,000 characters. If that budget stops a page early, `response_budget_limited` is true and its older cursor still reaches unreturned records. A running conversation returns only what has been saved so far, which may be partial; `is_running` does not mean the response is complete.

### Ask the Agent to create a conversation

Ask A to open a conversation titled “Research notes”. The Agent can call `create_chat_session(title)` to create an independent, empty ordinary conversation B. Omit the project to inherit A's current project, pass an existing owned `project_name` or `project_id` to select another project, or pass `project_id: null` for no project. Omitted model and thinking settings inherit the values effective in A's execution, including one-turn overrides. The source profile is inherited without changing A's saved preferences. Optional `selected_provider`, an allowed `selected_model`, and `thinking_effort` choose available settings explicitly.

A project name is the ChatUI sidebar folder title, not a filesystem path. For example, `create_chat_session(title="Research notes", project_name="Research project")` selects that existing folder directly, even when it contains no conversations. Names match saved titles verbatim and are case-sensitive; spaces are part of the title. Copy the `project_name` returned by `list_chat_sessions` unchanged. The tool never creates a folder. `project_name` and `project_id` are mutually exclusive, including `project_id: null`; omitting both inherits A's project. Unknown, empty or incorrectly typed names are rejected before allocating a conversation. Duplicate names within your account return candidate `project_id` values: retry with one of those IDs. Other accounts' folders do not participate in matching.

Without `text`, the tool only creates B and does not start a model. Nonempty first-message `text` uses the same send path and provenance described above. B does not copy A's history or create a side chat or hidden member. Creation refreshes the existing ordinary/project conversation lists. Click B to open its history or continue manually; A and B can keep exchanging messages with `send_chat_message`.

The first screenshot shows an empty project conversation created without a first message, with English selected. The detail below illustrates the source card used for an incoming Agent message, with Chinese selected. A created conversation uses the same card for its first Agent message and can then be continued by a human as usual. Interface labels follow the selected language.

![Empty project conversation created without a first message](/images/chat-session-created-empty.jpg)

![Source card on an incoming Agent message, with Chinese selected](/images/chat-session-created-first-message.jpg)

The tool returns the created conversation ID, `project_id`, `project_name` and opening location; both project fields are `null` for no project. If creation succeeds but the first message is rejected, it returns `created_send_failed`, the session ID and `send_error`. Ask the Agent to reuse that ID for sending. Retrying the same real tool invocation reuses its creation result within the current execution; this does not provide process-restart recovery. Configuration failures clean up the incomplete session. First-message sends share the original automatic chain's 8-send limit: creating another conversation cannot reset that budget.

The reasoning sidebar follows the latest activity when opened. Scrolling up or dragging its scrollbar pauses following; scrolling back down to the bottom resumes it. Opening the sidebar through search or a file-change entry prioritizes that location, and streaming updates do not pull it back to the bottom.

The yellow/red message markers beside the chat scrollbar are now short grayscale ticks. A longer, darker tick marks the turn being read; hollow ticks indicate inherited history. Hover or focus a tick to preview the user question as a title, with the corresponding reply excerpt in muted text below. Reasoning and tool logs are excluded. Click to jump. Dense ticks are grouped: the picker shows up to 20 questions and reply excerpts per page and opens on the page containing the current turn. Older messages load automatically when needed. On touch devices, tap a tick to navigate.

![Conversation markers and question list](./images/chat-scroll-markers.png)

ChatUI supports these common workflows:

- Create, rename, and delete conversations, and switch previous conversations from the sidebar.
- Select the config profile, model provider, and model on the chat page; if Provider session separation is enabled, you can also choose a model for the current session only.
- Send text, images, files, and voice input; uploaded attachments show previews and use file signature checks to help identify file types.
- View model thinking, tool-call status, knowledge-base or web-search references, and per-message token and latency statistics.
- Copy or regenerate existing replies, including regenerating with another model.
- Edit a user message and continue generation from that point, or start a thread from a specific excerpt.
- Switch between streaming/normal response modes and SSE/WebSocket transport modes.

The config chip in the composer, the `+ → Configuration` menu, and the selection dialog display the built-in `default` profile as `Default Configuration` (`默认配置文件` in Chinese). Its ID remains `default`, and custom profiles keep their names.

Chat attachments can be up to 512 MiB. Files of 32 MiB or more upload in chunks: the attachment card shows the percentage and a cancel button. If an upload fails, use its retry button to upload the missing chunks, or remove the failed card to discard it. Keep the page open while retrying; refreshing the page or restarting AstrBot loses the resumable session, and a session expires after one hour without upload activity. Existing image previews remain available.

Each upload is saved under a distinct filename, while chat continues to display the original name. Uploading different screenshots both named `image.png` keeps both files, and deleting one attachment leaves the other intact. Existing attachments do not need to be renamed.

Sent audio and video attachment cards include playback controls and a download button that keeps the original filename. Attachment rows containing media wrap to avoid clipping the player; image previews and ordinary file downloads keep their existing controls.

Under `Settings → Maintenance`, backup imports also show upload progress and offer `Resume Upload` after an upload failure. Chunked backup uploads support up to 8 GiB. These controls extend the existing chat attachment area and backup import dialog; their entry points have not moved.

### Subagent pages

The conversation title appears above **Main conversation** and the **Subagents** menu. Each invocation has its own read-only page. Click the compact subagent card in the main reply, or select a call in the menu, to view its assigned task, streamed thoughts, tools and answer using the same message layout as the main conversation. Subagent pages have no message composer or follow-up input.

![Main conversation with the subagent picker](./images/subagent-pages-main.png)

**Running** identifies an active invocation; **Background** identifies how it was launched, so both can appear together. Finishing a task does not itself mean the agent was cleaned up. Actual removal or automatic cleanup moves its invocations into the collapsed **Cleaned up** group. Older records without lifecycle metadata also appear there and retain their saved content. Cleaning up the agent does not close a page you are reading.

![Read-only subagent page with live background output](./images/subagent-pages-detail.png)

New calls update the menu without switching your page. Use **Main conversation** to return; refreshing or reopening a session also starts on the main page. Only the selected page's message components are mounted. Background progress and its final status are saved independently of the main reply, and reconnecting loads the current snapshot even when the main reply has already ended. When a background result wakes the main agent, its normal final answer is delivered to ChatUI even if it did not explicitly call the message-sending tool.

| Previous entry | Current entry |
| --- | --- |
| Expand a subagent card inside a main reply | Click the card → that invocation's read-only page |
| Follow-up input inside the expanded card | Removed; subagent pages are read-only |
| Old subagent execution cards | Subagents → Cleaned up → the saved invocation |

### Side Chats and Branch Threads

With a conversation open, hover over or click **New side chat** (the speech-bubble-plus button at the top right of the message area). The menu lists the side chats saved for this conversation; choose **New side chat** to start another. It starts empty, without a quoted message or inherited main-conversation context. Include any background the separate question needs yourself.

![Retained side chat with file citations in the isolated production ChatUI](./images/side-chat-references.png)

Closing the panel, opening a file, switching conversations, or leaving the chat page hides the side chat and keeps its history. Return to the same main conversation and select its title in the side-chat menu to reopen it. A reply that is still streaming keeps its visible content and continues updating when reopened. Use the delete button explicitly to remove a side chat; deletion requires a successful backend connection.

Agent file citations in side chats use the same host-absolute Markdown links and existing file pane as the main chat. Click a citation to hide the side chat, read the current file and highlight any valid source-line range. The file stays at its original absolute location after project or worktree changes. Viewing it does not change the Agent's project, execution directory or permissions. Reopen the side chat from the menu to continue.

Before a switch would leave unsaved edits, choose **Save and open**, **Discard and open**, or **Cancel**. Cancellation, failed saves and conflicts keep the editor and draft in place. Missing files, unreadable targets and invalid line ranges show feedback in the file pane. If spcode is unavailable, enable the plugin and retry the citation.

![Side-chat citation opens the existing file pane and source lines](./images/side-chat-file-pane.png)

Select text in a past reply and choose **Ask in side chat** to open the same side-chat panel, including the quote and context through that reply's checkpoint. It shares the full composer, profile selection, attachments, Stop and Delete actions with **New side chat**. Both entry types appear in the saved side-chat menu. A quoted side chat initially uses the excerpt as its menu title, then uses its first user message. Existing quoted threads also appear in this menu; their inline quote markers and counts still reopen them.

![Quoted text and the full composer in the unified side chat](./images/quoted-side-chat.png)

On desktop, drag the panel's **left edge** to resize the side chat. At narrow mobile widths it fills the screen and hides the resize handle. Use **×** at the top right to return to the main conversation while keeping the side-chat history. To copy an entire conversation into another main session, continue using **Branch** below a reply.

### Project Workbench Web Preview

With a project loaded for the current conversation, click **Workspace** above the composer (tooltip: **Browse workspace files, changes, and history**) to open **Project Workbench**, then select **Web Preview** beside **Terminal**. Files and Web Preview are also available without a project. The other project tabs appear after the workbench recognizes a Git or non-Git project; they may be absent while its project check is pending or fails.

1. Start the website's development server separately. The preview does not start it for you.
2. Enter its HTTP(S) address and press **Enter**, or select a suggested local port. For example, `localhost:5173` becomes `http://localhost:5173/`; a bare public hostname defaults to HTTPS. Localhost refers to the machine running your browser, which may differ from the AstrBot server.
3. Select **New preview page** (+) to add a page, switch using its tab, and close it with ×. Closing the last page opens a blank page. Switching pages keeps the other previews loaded.
4. Use **Reload**, **Open in new tab**, or the star in the address bar to bookmark the current page. Up to 12 bookmarks, the preview tabs and the active tab are stored per project in this browser; they do not sync across browsers or devices. Without a project, tabs last until the preview is remounted. Switching workbench tabs keeps the loaded previews mounted.

5. Pages keep a desktop layout and scale proportionally to the sidebar width. Resizing the sidebar adjusts the preview; long pages still scroll vertically inside the iframe. Use **Fullscreen preview** at the right of the address bar to expand the preview, then **Exit fullscreen** or **Esc** to return. Resizing and fullscreen do not reload the page or discard entered values.

This cross-origin example uses a minimum desktop layout width:

![Web Preview fits the sidebar width and includes a fullscreen button](./images/web-preview-fit-en.png)

Links inside a page that request a new window still open a browser tab; the sidebar **+** adds a preview tab manually. Cross-origin iframes cannot intercept the website's new-window behavior.

The preview below shows two pages open in the Web Preview component.

![Web Preview with two page tabs](./images/web-preview-tabs-en.png)

This is an embedded page, with no back/forward controls. Its address bar keeps the entry address rather than tracking links or redirects inside the page. Sites can block embedding with their frame policy; an HTTPS dashboard may also block an HTTP preview. Use **Open in new tab** if embedding fails. The iframe is sandboxed, with additional isolation for addresses sharing the dashboard's origin; it does not bypass browser or target-site restrictions. File, data, and JavaScript URLs are unsupported.

| Previous entry or behavior | Current location or behavior |
| --- | --- |
| Select reply text → Ask in thread | Select reply text → Ask in side chat; shares the side-chat panel, full composer and saved-chat menu |
| Temporary side chat, destroyed on close | Message area → side-chat menu → New side chat or a saved title; independent context, history retained until explicitly deleted |
| Side-chat file citations | Open the shared file pane; reopen the side chat from its saved title in the menu |
| No workbench entry without a project | Files & Browser above the composer; the Workspace entry remains for loaded projects |
| Workspace → Files / Documents / Git Diff / History / Terminal | Existing tabs remain; Web Preview is added after Terminal in Project Workbench |

> [!NOTE]
> To keep message delivery ordered, keep only one ChatUI page open for the same browser session. If you open chat in multiple tabs, the system may ask you to reconnect.

## Visual Configuration

Select `Config` in the sidebar, then choose the profile to edit from the selector at the top. Sections appear in this order: `AI`, `Platforms & Messages`, `Access Control`, `Plugin`, and `Ext.`. Use the search control at the top to find a setting.

- In `AI`, built-in AI settings have `Model`, `Persona`, `Capabilities`, and `Advanced` tabs. Use `… → Change execution mode` beside the AI heading to connect a third-party agent.
- `Access Control` contains administrator IDs and the session whitelist, including whitelist logs and administrator exemptions.
- `Platforms & Messages` contains message-trigger, session-isolation, and delivery settings.
- `Plugin` selects the plugins enabled for this profile. To edit an individual plugin's parameters, open `Extensions` and click that plugin's gear icon (`Extension Config`).
- `Platforms & Messages → Text-to-Image` contains the output toggle and character threshold. Configure the rendering strategy, service address, and custom template under `Settings → Appearance`.
- `Ext.` contains segmented replies and group-context awareness.

| Previous location | Current location |
| --- | --- |
| Platform | Platforms & Messages |
| Platform → General → Administrator IDs | Access Control → Administrators → Administrator IDs |
| Platform → Whitelist | Access Control → Whitelist |

![Access Control](./images/config-access-control-en.jpg)

After selecting an explicit model, click the gear beside the selector to open its settings dialog. Gears are also available in model and fallback-model menus. Clicking a gear does not change the selection or fallback order. Saving model settings immediately affects every profile using that model; the current profile's model selection still requires `Save Configuration`. Automatic selection has no gear beside the selector.

![Model settings shortcut](./images/config-model-settings-en.jpg)

After editing, click the disk icon labeled `Save Configuration` in the lower-right corner and check for a successful save message.

In the bot editor, the arrow beside a default or session-route configuration option opens that profile in a side drawer. Changes made in the drawer also require selecting `Save Configuration`.

Use the `{}` icon labeled `Edit Configuration File` to edit the current profile as JSON. Select `Apply This Configuration` to stage the changes in the visual editor, close the editor, and then select `Save Configuration`.

### System Settings

Global settings are under `Settings` at the bottom of the sidebar:

- `General`: timezone, external callback address, logs, and cache.
- `Appearance`: sidebar, theme, and text-to-image rendering.
- `Network`: HTTP proxy, Python package sources, and GitHub proxy. For the address to use when AstrBot runs in Docker, see [Deploy with Docker](/en/deploy/astrbot/docker.md).
- `Security`: WebUI HTTPS, login rate limits, and TOTP.
- `Maintenance`: backup, restore, and restart.
- `OpenAPI`: developer access keys.

System configuration changes save automatically. Check for a successful save message and restart AstrBot if the page indicates that a restart is required.

## Default Persona

The default persona is now a stored record in persona management with ID `default`. You can edit its prompt, preset dialogs, tools, and skills, but cannot delete it or create another record with this ID. An existing record keeps its content; startup creates it only if absent. The selector pins it in the root folder, and preview and editing load its saved content.

When WebChat has no explicitly selected persona, its default system prompt toggle still applies. Explicitly selecting `Default Persona` uses the stored prompt. Other platforms use this editable record for their default persona. Local tool and skill selections continue to distinguish all capabilities from none.

## Plugins

Select `Extensions` in the sidebar. The top tabs are `Plugins`, `Skills`, `MCP Servers`, and `Handlers`. Within `Plugins`, switch between `Installed` and `AstrBot Plugin Market` to view local and market plugins.

On either plugin list, you can also click `Install Plugin` (+) in the bottom right corner to manually install plugins via URL or file upload.

The `Skills` and `MCP Servers` pages support search. The skills list also displays each skill's source to distinguish built-in, project, and other skills.

> Due to the plugin update mechanism, the AstrBot Team cannot fully guarantee the security of plugins in the plugin market. Please carefully verify them. The AstrBot Team is not responsible for any losses caused by plugins.

### Handling Plugin Load Failures

If a plugin fails to load, the admin panel will display the error message and provide a **"Reload"** button in the failed plugins list. This allows you to quickly reload the plugin after fixing the environment (e.g., installing missing dependencies) or modifying the code, without having to restart the entire application.

## Data & Logs {#data}

Select `Data & Logs` in the left sidebar to switch between `Statistics`, `Conversations`, `Logs`, and `Trace` from the tabs at the top of one workspace. `Statistics` opens by default the first time; afterwards, the page reopens the tab you last used.

### Statistics

The `Statistics` tab summarizes platform instances, messages, model calls, tokens, and uptime. It also shows message trends, platform message rankings, model-call trends, model usage rankings, and conversation model usage rankings. Use the range selector at the top to view 1 day, 3 days, or 1 week.

### Conversations

Use the `Conversations` tab to find and manage saved conversation records:

- Filter by keyword, bot ID, private/group conversation type, or UMO, and sort by creation or update time.
- By default, the list loads 30 records per page from the server. When **Group by session** is enabled, pagination switches to UMO sessions and expanding a session reveals all of its conversations. Full message history is loaded only after you select a conversation and is then shown in the preview on the right.
- Edit titles, export multiple selected conversations, or delete multiple selected conversations.
- Use the `{}` button in the preview header to inspect the raw `history` JSON in a read-only, word-wrapped Monaco editor.
- The preview opens at the beginning of the history and lists message roles and tool results in their original order. Its toolbar toggles Markdown and adjusts text from 12 to 18 px (13 px by default); these reading preferences are saved in your browser. Images appear as small thumbnails, while tool arguments and structured content expand on demand. The `reasoning_content` field appears under `Reasoning`; a `{type: "think", think: "..."}` content block appears as JSON under `Structured content`. Checkpoints are hidden from the reader and its message count, but remain in the raw JSON.
- Select `Back to legacy` in the upper-right corner if you still need the original table-based view.

### Logs

The `Logs` tab shows AstrBot runtime logs in real time. You can filter by log level and install missing Pip packages from this page. To view DEBUG logs, first set `Console Log Level` to `DEBUG` under `Settings → General → Logs`.

Enter a keyword in the search field to filter logs and highlight matches. Search is available in both the normal and fullscreen log views.

### Trace

The `Trace` tab shows AstrBot execution traces in real time and is useful for debugging model-call paths and tool invocations. Use the switch at the top to enable or disable trace recording.

> [!NOTE]
> Trace recording currently covers only some model-call paths from the AstrBot main Agent. Coverage will continue to improve.

## Command Management

Use `Extensions → Handlers → Command` to centrally manage all registered commands; system plugins are hidden by default.

Filter by plugin, type (command / command group / subcommand), permission, and status, and combine with the search box for quick lookup. Command group rows can expand to show subcommands, badges display the subcommand count, and subcommand rows are indented to indicate hierarchy.

You can enable/disable and rename each command.

## Updating the Admin Panel

When AstrBot starts, it automatically checks if the admin panel needs updating. If it does, the first log entry (in yellow) will prompt you.

In the browser WebUI, open `⋮ → Update AstrBot` in the upper-right corner, expand `Advanced settings`, and click `Download and Update` under `Update Dashboard to Latest Version Only`. The page refreshes automatically after a successful update. In the desktop app, the update entry opens the desktop application updater.

You can also use the `/dashboard_update` command to manually update the admin panel (admin command).

Admin panel files are located in the data/dist directory. If you need to manually replace them, download `dist.zip` from https://github.com/AstrBotDevs/AstrBot/releases/ and extract it to the data directory.

## Agent browser live observation

When the current main chat calls a Browser Use browser tool while the workspace sidebar is closed, it opens directly to **Web Preview → Agent Browser**. An already open workspace keeps its current view. Automatic opening happens at most once per reply, so closing the panel keeps it hidden for the rest of that reply. Changing chats or manually opening another panel while the check is pending cancels the old request. History, plain mentions of tool names and other chats do not trigger it. The manual entry remains available when the plugin is unavailable or another chat owns the browser.

Windows offline deployment requires the Browser Use `0.3.0` complete plugin ZIP, the AstrBot backend containing this feature, and its matching Dashboard build. The plugin ZIP does not upgrade the host or frontend. AstrBot's `4.28.1` version alone does not identify these changes: compare `astrbot_revision` and `dashboard_sha256` in the plugin's `runtime/manifest.json` with `dist/assets/browser-observer-build.json`. Stop AstrBot before installing each component and select the matching `dist` with `--webui-dir`. The packaging command below also emits a backend source ZIP and its SHA256 beside the Dashboard ZIP; the source ZIP requires an existing compatible AstrBot Python environment. Keep the existing data directory separate from the new code. The installer refuses to overwrite an existing plugin; persistent data stays under the separate `plugin_data` directory.

After a production Dashboard build, maintainers can run `python scripts/package_browser_observer_dashboard.py --plugin-revision <plugin-commit> --output <dashboard.zip>` to include version/revision metadata and a SHA256 sidecar. Run `python scripts/browser_observer_acceptance.py --plugin-zip <complete-plugin.zip> --dashboard-dir <dist> --evidence-dir <new-temporary-directory>` with AstrBot development dependencies and `aiohttp playwright psutil` to test final-ZIP installation, real login, the complete chat interface, a deterministic local model, real plugin management and Chromium. Local proxy isolation does not replace a physically offline target or real vLLM verification. For a missing entry, check matching builds and cached assets; for `runtime_unavailable`, inspect plugin startup logs and the private interpreter; for `busy`, close the browser in its owning chat; for 401/403, sign in and use a chat you own.

With the matching Browser Use plugin enabled, ask the Agent to open a page in the current chat. Without a code project, use **Files & Browser → Web Preview → Agent browser** above the composer. With a project loaded, use the existing **Workspace → Web Preview → Agent browser** entry. The current snapshot appears first, followed by live updates from the actual Chromium viewport. The view follows the Agent's selected tab, including when that tab closes. One toolbar row contains the connection dot, page title, URL and controls. Narrow sidebars prioritize the URL; hover over the address bar for the title. The refresh icon at the right of the address bar refreshes the snapshot. Image dimensions, sequence, tab identity and full input instructions are available in the **Info** popover below the image. Login state and entered form values come from the browser controlled by the Agent. The image is read-only by default and accepts clicks after confirmed takeover; observation frames are not added to the model context.

Recent actions below the image is collapsed by default. Expand its heading to show the icon timeline with the current action and up to 32 recent results for navigation, clicking, input, scrolling and tab operations. A subtle background marks the active action, and result badges distinguish outcomes. Running/waiting, success, failure, timeout and cancellation have distinct states. Input summaries always hide the content. When actual execution supplies reliable coordinates, a brief ring marks the operation position at the viewport scale. Navigation (including reloading the same URL), tab changes, scrolling, the next action or a two-second expiry invalidate the marker. Without reliable positioning, only the action summary appears. When new scroll or viewport metadata arrives before its image on the same page, the previous frame stays visible with a synchronization notice. Those old pixels cannot receive input; input and markers resume only after a matching frame loads. Changing chats or pages, or losing access, still clears the previous image immediately. Cancellation does not undo page operations that have already happened.

Normal observation receives frames over SSE without periodically refetching snapshots or reloading the webpage. While no browser is open, a status check every three seconds discovers new sessions. A disconnected stream reauthorizes, obtains a snapshot and resumes observation.

The previous iframe entry maps from **Web preview** to **Web preview → Web preview**; its bookmarks retain their existing behavior. Another chat cannot view or operate an occupied browser. Ask the owning chat to close all browser sessions before handing it to another chat. Hiding the sidebar, switching workbench tabs, or putting the Dashboard browser tab in the background stops its observation subscription while execution continues. Reopening obtains a fresh snapshot and stream. After an interruption, click **Refresh snapshot** to reconnect. Switching chats clears the previous image, actions and subscription before authorizing the new chat. After a page refresh, reopen the sidebar to resume observation.

Opening Agent browser checks its status and subscribes once. There is no automatic polling, timed refresh, or reconnection after a lost connection, network recovery or returning to the browser tab. Click **Refresh** beside the address to check again or reconnect. Healthy connections continue receiving live frames. Interruptions retain the last frame with **Showing the last frame · click refresh to reconnect**; cached pixels cannot authorize input. Confirmed closure, disabled plugins, access or ownership changes, and switching accounts, chats or browser identities clear the old image. Manual control still sends lease heartbeats; these do not refresh the page.

This local example uses real private Chromium behind a test control connection, with a simulated observation interruption:

![Disconnected view retains the last frame until Refresh is clicked](./images/agent-browser-reconnect.png)

Unloading a code project keeps the active Agent browser view open. Git, files, documents, terminal and the original iframe preview retain their existing entries. The view distinguishes idle, another chat holding the browser, plugin disabled, disconnected and reconnecting states. Recovery obtains a consistent snapshot before action increments; an expired recovery window or changed runtime displays a resynchronization notice. Only the latest image is fetched. Resuming observation for the same chat does not restart the browser or clear its login state. Read-only observation sends at most about four frames per second and keeps only the newest pending image; static pages may send no new frames. Actual rate and delay depend on the page and environment. After re-enabling the plugin, click Refresh to query the new browser identity.

The screenshot below uses a local example page and deterministic observation events. The address and controls share one row. Pause, disconnect and input feedback share a single status row below the image; hover for the full message or open **Info** for details. The former text button for **Leave keyboard control** is now a keyboard icon in that row. The Web preview / Agent browser mode selector remains in the dropdown attached to the Web Preview tab. Both **Web Preview** modes hide the former branch/worktree toolbar; it returns when switching to project views such as **Files, Git Diff or Git History**. Use the fullscreen icon below the image to expand the browser viewport; the exit button or Esc returns to the sidebar. The panel supports light and dark themes and narrow sidebars.

**Stop execution** stops the current chat’s Agent run, waits for the in-flight browser action to finish or cancel, and rejects queued and subsequent browser actions. The browser remains visible for inspection. **Close browser** also closes its sessions and releases ownership; a new task starts with a fresh isolated profile. Completed page operations are not undone. In read-only mode, hiding the panel stops observation; during human control it also disconnects input and leaves the browser paused. Disabling the plugin ends its owned browser, action, stream and MCP resources; retained requests cannot reactivate it. Re-enable the plugin to establish a new runtime generation without restarting AstrBot.

![Single-row address bar, compact status row and collapsed actions](./images/agent-browser-compact.png)


Images always scale proportionally to the available sidebar or fullscreen width; limited height no longer makes them narrower, and taller images can scroll vertically. The remote page retains a desktop layout width of at least 1280px, growing with wider panels up to 2560px. Viewport height uses the same scale, bounded to 240–1600px. This keeps narrow panels from cropping common desktop pages with minimum-width layouts; sites whose content exceeds that layout width can still scroll horizontally. Remote sizing follows a 350 ms debounce, while existing pixels fit the width immediately; viewport resets after page operations are synchronized again. Long pages retain normal vertical scrolling without capturing the entire document as a thumbnail. Resizing waits for an active Agent action and does not interfere with another window's human control. Update AstrBot backend, Dashboard and Browser Use together; older plugins continue displaying their existing viewport fitted to width. Snapshots use up to 2x sampling and JPEG quality 90, bounded to 2560×1600. Complex images exceeding the 2 MiB image budget receive lower JPEG quality; stream messages remain bounded to 3 MiB. Actual sharpness and frame rate depend on the page, device and connection.

A manual input made stale by scrolling, navigation or layout changes is reported as not executed. That input and the unsent queue are discarded while ownership remains active, waiting for fresh pixels. Retry explicitly; input is never replayed automatically. Lease heartbeats no longer depend on a page execution context surviving navigation. Actual network loss, logout, revoked access and uncertain execution still stop input and pause the browser.

![Viewport following the sidebar dimensions using a local page and observation fixture](./images/agent-browser-adaptive.png)

### Take control of an Agent browser

In the same Agent Browser panel, click **Take control** while the Agent is running
or after it finishes. A waiting message appears while browser actions settle.
The image stays read-only until the server confirms ownership. Click the displayed
webpage to operate the existing private Chromium; the browser, tab and login state
are retained. Click **Hand back** to restore Agent admission after a fresh page read.
A running local Agent pauses without timing out or calling the model repeatedly.
Its pending actions are discarded. After explicit handback, it observes current tabs,
page and elements and decides the next action. New messages do not release the pause.
If the turn was cancelled or has ended, handback keeps the browser available for
another request and does not start a task.

The control belongs to one signed-in account, chat and client connection. Closing
or hiding the panel, changing chats, losing observation or disconnecting input
stops local input and leaves the browser paused. Reconnect explicitly, take control
again and hand back before starting a new task. Stop execution and Close browser
keep their existing meanings. Plugins without `manual_control_version` remain
read-only and show an update hint. Plugins with click takeover but without
`keyboard_input_version` retain clicks and handback, and show a keyboard upgrade hint.

After takeover, double-click targets or scroll vertically and horizontally over the image. Wheel gestures inside the controlled image stay on the remote page; scrolling outside it remains local. The actual image rectangle maps to the remote CSS viewport, including narrow sidebars, host zoom and fullscreen. **Sending ordered input** means an acknowledgement is pending; **Input confirmed · waiting for updated page** means the runtime accepted the operation but its new image has not loaded yet. **Updated page displayed** confirms a later consistent image loaded, rather than only a successful API response. The controller is shown as human control and human actions are marked **You** in the timeline. Fullscreen also provides **Hand back**.

Navigation, tab/viewport changes and document or nested-container scrolling invalidate old position samples. Replaceable adjacent wheel deltas are merged, and stale wheel samples are discarded when the new image arrives. Clicks, text and keys remain ordered and separate from the image buffer. Failure stops input without replay. Human viewing targets up to 10 captures/second (actual rate depends on page and machine), with one pending image, bounded action/input queues and the existing JPEG size budget. Hold the left mouse button to drag sliders, page elements or text selections. Dragging requires `drag_input_version: 1`; older plugins retain their click and keyboard support. File transfer and system dialogs remain outside this feature. Plugins without `pointer_input_version: 1` retain single clicks and show a scrolling/double-click upgrade hint.

Other windows show **Read-only: another window controls this browser**. Refresh or
reconnect first synchronizes status and a new image; a paused browser requires
explicit takeover and handback. **Input confirmation was lost** means the action
may already have happened. Check the page before acting again; queued input is
discarded and no click or submission is replayed. Logout and revoked account/chat
access stop input too. Stop preserves the browser; Close or disabling the plugin
ends its lifetime. Restarting the plugin cannot reuse old control credentials.

![Another signed-in window is read-only while the browser is occupied](images/agent-browser-recovery.png)

![Human scrolling and double-click feedback](images/agent-browser-pointer.png)

Fullscreen keeps status notices and **Hand back** visible, and fits the image to the space left by the controls. Blank margins stay local.

![Fullscreen](images/agent-browser-pointer-fullscreen.png)


After confirmed takeover, click the image to focus remote keyboard input. Type English or Chinese, use Tab, Enter, arrows, Backspace and editing shortcuts, or explicitly paste plain text (up to 2048 characters / 4096 UTF-8 bytes). IME preedit stays local and commits once; cancelled composition inserts nothing. Tab/Escape reach the remote page while the image owns keyboard focus, including fullscreen. Use **Leave keyboard control** to return to local controls, or **Hand back control** to explicitly return the browser to the Agent. Clicking the chat composer or another local control releases held keys. Target changes discard queued input and require focusing the image again. Passwords and input payloads are absent from action summaries; after handback the Agent can still read visible page content.

![Keyboard input under confirmed human control](./images/agent-browser-keyboard.png)

![Running Agent paused under confirmed human control](./images/agent-browser-manual-control.png)


## Customizing WebUI Port

Modify the `port` in the `dashboard` configuration in the data/cmd_config.json file.

## Forgot Password

Modify the `password` in the `dashboard` configuration in the data/cmd_config.json file and delete the entire password key-value pair.

> [!TIP]
> For more details, see [FAQ - Forgot Dashboard Password](/en/faq.md#forgot-dashboard-password).

Workspace entry mapping: **Workflow Assets → Assets → Teams / Workflows**, **Role templates → Assets → Role templates**, **Independent Runs → Runs → All runs / In progress / Needs attention / History**. Configure versioned assets and start runs directly, without migration or legacy run checks. History displays asset runs only. See the [asset and run workspace](./agent-team-workflows.md#navigation-and-previous-entry-points) for current screenshots and controls.


## Skill Store

In `Extensions → Skills`, click `Open Skill Store` to open the store in a new browser tab. Its address follows the Core release API in `Settings → Update Sources`. This link replaces the previous inline store; see [steps and screenshot](./skills.md#skill-store).


## Header and personal quick messages

Ordinary accounts use the same header model selector as administrators. Their username and **Log out** appear at the top right, where administrators retain the **Bot** button. All signed-in accounts can read the announcement bar and reopen it using the header announcement button after collapsing it.

To tell the Agent which account is currently signed in, an administrator can enable **Message Handling → User Identification** in the profile selected for that conversation. The prompt includes the current login name, such as `johnmac`, and stable user ID, with a reminder not to substitute an identity from another user's memories. Disabling the option omits this identity reminder. It does not change the memory plugin's own user isolation.

| Previous entry | Current entry |
| --- | --- |
| Ordinary account model selector in the composer | Model selector in the chat header |
| Identity and logout beside View archives | Username and Log out at the top right |
| Administrator-only quick-message presets | Quick messages → Edit quick messages, saved per account |
| Accounts | Multi-user management |

Existing presets supply initial content until an account saves its own list. Administrators and ordinary users then have separate lists; editing one does not overwrite another. Deleting every phrase preserves an empty list. The server stores the list across logins. Shared thinking-effort preset editing remains administrator-only.

![Ordinary account header, announcement and account controls](./images/chat-account-header.png)

**Multi-user management** serves a single-instance, multi-user setup: separate accounts access the same AstrBot program through ChatUI with their own conversations. Administrators can re-enable disabled ordinary accounts; users must sign in again and their earlier credentials remain revoked.

Deleting an account requires confirmation and permanently removes its chats and associated data, including history, attachments, project associations, personal settings and managed workspaces. Other accounts and the administrator remain intact. External host directories linked by users are not managed Chat data and are not recursively deleted. Recreating the same login creates a fresh identity without inheriting old data.

![Personal quick-message editor](./images/chat-personal-quick-messages.png)

![Confirmation before permanently deleting an account and its owned data](./images/multi-user-delete-confirmation.png)

The mobile header also provides the model selector and account controls:

![Ordinary account header on mobile](./images/chat-account-header-mobile.png)

### Offline manual-login acceptance

Browser Use 0.3.0 requires matching host and Dashboard commits, not just AstrBot's version number. Commit both repositories before packaging. Pass the plugin's exact Git SHA through `--plugin-revision` to the Dashboard packager; its metadata must match `plugin_revision` and `astrbot_revision` in the plugin manifest. Check the Dashboard ZIP SHA256 recorded by the plugin and each delivered `.zip.sha256`. The backend source ZIP requires an existing compatible AstrBot Python environment. The plugin contains private Windows x64 Python/Chromium only.

Stop the host before upgrading. Back up the matched components and data, move the old plugin out of `data/plugins`, then use the shipped installer; never load both copies. Keep `plugin_data` separate. Roll back all three components together while stopped. A host restart ends the current browser session; takeover within a running task retains it.

For a login task, wait for confirmed **Take control**, focus the remote field, enter the account/password and submit the website form, then explicitly **Hand back**. Disconnecting or hiding pauses control and requires explicit recovery. Input summaries hide content; the Agent can observe visible page content after handback.

Run `python scripts/browser_manual_control_acceptance.py --plugin-zip <complete-plugin.zip> --dashboard-dir <dist> --evidence-dir <new-directory> --login-handoff --pointer-input --text-input --lost-key-target --recovery`. For the separate one-step-budget regression, repeat with a new directory and `--handoff-boundaries`, omitting `--login-handoff`. It installs the final ZIP with the original PowerShell installer, relocates the complete data tree through Chinese/space paths, leaves executable overrides empty, and exercises real production ChatUI, the installed plugin, private Chromium and a deterministic local OpenAI-compatible model. It records screenshots, source/revision checks, latency/resource/cleanup measurements and network audits.

Only disposable interpreters receive a Python socket/DNS and installer guard; native Chromium uses a dead SOCKS proxy, loopback bypass, disabled external resolution and QUIC. A rejected external connection probe and per-process audit establish these constraints; process connections are sampled. This is process-level test instrumentation, not an OS firewall or physically offline target. Native networking outside those mechanisms, real vLLM, physical Windows IME candidate UI and other platforms remain separate verification conditions. Existing compatible host dependencies must already be installed.
