# Agent Teams assets and run workbench

To build a parallel workflow from fixed source documents, see the [multi-document report tutorial](./agent-team-report-tutorial.md).

The canvas and workbench screenshots come from the real Dashboard, an isolated database and a controlled model service.

Open **More Features → Agent Teams → Assets**:

- **Teams** are autonomous teams whose coordinator assigns work toward a goal and creates members from presets.
- **Workflows** arrange one or more Agents, control nodes and child assets on a canvas.
- **Role templates** hold reusable responsibilities and local resource configuration for teams and workflows.

Asset cards show the fixed version, a task or member summary, and draft status, with edit and run actions below. The lower-left icons on team and workflow cards open chat access, export, and deletion respectively; hover to read their names or use the keyboard to activate them. Light and dark themes are supported.

## First run with the release brief

Open **Agent Teams → Assets → Workflows** to find **Release brief** pinned at the top. Select **View walkthrough** on the card to follow the original v1 brief from preparation through approval and delivery. The example runs on a fresh deployment using built-in nodes and a human approval.

1. Select **Open canvas** to inspect the three nodes. The preparation node supplies the project and summary to the delivery node. The approval node controls when delivery starts. Return to the workflow list.
2. Select **Start Run**, leave the input as `{}`, and confirm. The project defaults to `Agent Teams` and the summary to `Ready for release.`. To try your own values, enter an object such as `{"project":"Website update","summary":"The new help page is ready."}`.
3. Select **Open in Chat** in the workbench. Under **Inbox & artifacts**, preview the brief and approve it. The delivery node returns the approved project and summary. You can also handle the same request in the Bot workbench.

![The animated release brief walkthrough at the human approval step](/images/agent-teams-release-guide-en.png)

To remove the example, select the delete button on the card and confirm. Deletion is remembered for your account across reloads and server restarts.

### Connect execution and data ports

Hover over a blue handle to see **Execution input** or **Execution output**. Drag a named green port to highlight compatible data inputs. The default single-Agent text workflow has two data lines: workflow `input` to Agent `input`, then Agent `text` to the workflow output. The Agent has no predecessor and can start directly. To chain two Agents, connect A’s execution output to B’s execution input, then connect A’s `text` to B’s `input`.

![Dragging a workflow input highlights the compatible Agent data port](/images/agent-teams-port-targets-en.png)

Publication and full-graph trials identify required data bindings that depend on opposite outcomes of the same decision. They also reject enum or constant ports with no common value. The asset list identifies unsupported versions by asset ID. If one section fails to load, use its **Retry** button to reload that section.

## Create and configure

Choose **Assets → Workflows → New Workflow** to open a dedicated workflow editor. Enter a name at the top and select a default model in **Workflow settings**. Add Agents directly on the canvas without first creating a role template. Select an Agent to configure its task, responsibilities, profile, persona, model and resources; nearby hints explain their purpose and inheritance rules. An unselected persona means no persona; an Agent without its own model uses the workflow default. Before launch, every local Agent must resolve to an allowed model.

Alternatively, select a role template and its **fixed version** in the Agent properties. Enable an override to replace a template value; disable it to inherit. An empty responsibility clears it, clearing the persona means no persona, and clearing the model returns to the workflow default. Tools, Skills and knowledge bases independently inherit or use a complete custom selection; an empty custom selection disables that category. Upgrade the reference explicitly when a new template version is available; existing runs do not change automatically. You can save a directly configured Agent as a template, then explicitly select it if you want to use it.

Choose **Assets → Teams → New Autonomous Team**, enter a name and default model, and configure the coordinator and member presets. Each can be configured directly or use a fixed role version with overrides; the same template can be used more than once with different overrides. The coordinator can create only allowed presets. Expand **Execution limits, subflows & final approval** to set member limits (including the coordinator), concurrency, member task limits, optional final approval and an output contract. Enter the goal at launch.

![Live workflow canvas](/images/agent-teams-editor-page-en.jpg)

Add nodes on the left, connect them on the canvas, and edit the selected node in the right inspector. **Reusable assets** contains saved teams, workflows and roles. **Workflow settings** opens the default model and execution budgets; **Loops & subflows** opens bounded-loop settings and nested-call previews. Both panels collapse without consuming canvas height. On narrow screens, expand the toolbox and inspector when needed.

Navigation mapping: **New Workflow / Edit Asset → canvas dialog** is now a **dedicated workflow editor** (`#/agent-teams/workflows/<asset ID>`). The dialog's **Close** action is replaced by **Back to workflows** at the top left; test and version actions remain at the top. Each draft has its own URL and can be reopened after a refresh. Returning to the list or using browser Back waits for draft saving. Save failures or conflicts keep the editor open so you can retry or explicitly discard unsaved changes.

On the new canvas, each declared workflow input has a named `string` output port. A task placeholder such as `{input}` creates a required Agent input port named `input`. Drag the workflow input onto that port, then drag the Agent's `text · string` output to the matching port on **Outputs**. Dashed lines labeled **Data** carry bound values. Solid control lines determine which paths run. Select a data line to disconnect it in the inspector, or use Undo and Redo. To rename a workflow input, edit its name and leave the field before adding or removing other inputs. An unconnected required port can be saved in a draft. A full trial or version save highlights the port that needs attention. Older fixed versions use their original field bindings.

### Structured ports

Choose **Use structured ports** in the editor. Select the workflow input node and declare an inline JSON Schema object with `properties`. Put mandatory field names in `required`. Set `default` to fill in an omitted input. To accept an explicit `null`, include `"null"` in its type. For example, a table can be an `array` whose `items` schema describes an object for each row.

The canvas labels declared ports **Required** or **Optional** and displays defaults as JSON, including `false`, `0`, `""`, and `null`. A declared input default supplies a value when that port is omitted. An output default is a schema annotation. Fixed workflow calls and installed components display the contract of the selected version. If a drop has incompatible types or an obvious mismatch in object fields or array items, the receiving port is highlighted. Full-graph trial and publication check the structured connections.

1. Add **Parse JSON object** and connect a declared string input to `text`. Declare the parser's output fields, such as an object named `query` and a row list named `rows`.
2. Add **Map fields** and declare the destination names and types in its input and output contracts. Draw the solid control line from the parser to the mapper. Connect each named data port with a dashed line. The mapper passes the selected objects and lists through unchanged. Use **Convert JSON to text** before an Agent placeholder that needs a string.
3. Connect the mapper's **result · object** to **Outputs**. Reopen the saved draft to check the links. In **Test draft** or **Start Run**, enter the workflow inputs as a JSON object. To test from the mapper, supply the parser's output in **Advanced object data (JSON)**. A trial with root-level constraints such as `minProperties` shows the full workflow input contract.

For the example above, use `{"raw":"{\"query\":{\"limit\":0},\"rows\":[{\"id\":1}]}"}` when `raw` is the workflow input name.

The structured port contract is version 2. It also supports fixed child workflow calls and bounded loop feedback. Text port and legacy fixed versions retain their bindings. Top-level `files` in a result is reserved for managed attachments. Ordinary inputs may declare a field with that name.

![Structured workflow with required and optional ports, JSON defaults, and object and row-list links](/images/agent-teams-structured-ports-en.jpg)


### Decision nodes

Add and test a System One or OpenAI Decisions instance under **Providers → Decision** in the Dashboard. Enable **structured ports** in the workflow editor and add a **Decision** node. Choose a local provider and question type, then bind text or JSON to the `state` input. Boolean questions return `matched` and `probability`, choices return `choice` and `probability`, and rubrics return `score`. Connect the desired output to a condition. Map decision provider dependencies when importing the asset on another instance.

Timeouts, refusals, and invalid replies block the node and can be retried from the run details. A valid `false` follows the corresponding branch. Run details show the provider and model used. Use human requests for artifact approval. Plugins can call `ctx.decision.ask()`, `ctx.decision.choose()`, or `ctx.decision.yes_no()`, with the default decision provider for plugins set in the default profile.

![Decision node with its bound provider and typed ports](/images/agent-teams-decision-en.jpg)

### Installed plugin components

An AstrBot administrator installs and enables the plugin under **Plugins**. In a workflow with **Use structured ports** enabled, find the component and its version in the left toolbox. Add it to the canvas, connect its named inputs and outputs, and select the local knowledge base when the component requests one. Test the draft with structured JSON inputs and publish the validated version.

To run a tool as an independent workflow step, the plugin registers it as a workflow component with a declared input and output contract. For tools on an Agent node, the model decides when to call the tools on its allowlist.

![An installed component and its fixed ports on the workflow canvas](/images/agent-teams-plugin-component-en.jpg)

A published workflow keeps the exact component version and contract. The editor or run owner needs access to the workflow and its selected local resources. The current Dashboard permits these asset operations through its administrator role and applies ownership separately to each asset and run. If a component is disabled, removed, or incompatible, restore the required version or edit the draft and publish a new one.

Export a component workflow as an AstrBot native version 4 package. On the destination, install and enable the required plugin through **Plugins**, preview the package, and select its local resource bindings before importing. The package carries definitions and versioned references. Plugin code, plugin settings, and credentials stay with each installation.

If a connector stops with an uncertain external effect, open the same run in the workbench and select the affected task. **Reconcile** uses the original call ID when the connector supports it. A saved receipt resumes the original attempt. For manual confirmation, enter the observed JSON object matching the displayed output contract. If the effect cannot be established, leave it pending. Chat links to the same run workbench.

Plugin authors can override `Star.workflow_components()` to return `WorkflowComponent` declarations. Declare a stable local name, exact version, object schemas, the effect, and any knowledge-base read requirement. The async operation receives structured inputs and the server-authorized binding and invocation ID. Use `ToolRecovery` for authoritative queries or replay guarantees, and bump the component version when its behavior changes. The fixture in `tests/agent_teams/fixtures/workflow_ledger_plugin` illustrates the contract.


### Messages, artifacts, and managed files

Declare named object ports with one of these formats. Connect ports with the same format.

| Format | Value |
| --- | --- |
| `astrbot-message` | `{"text": "Selected message"}` |
| `astrbot-artifact` | `{"artifact_id": "...", "version": 2}` |
| `astrbot-file` | `{"artifact_id": "...", "version": 2, "file_id": "..."}` |

In a structured workflow, connect **$artifact** to pass the producing node's exact result version. If that node declares managed attachments in its output contract, **$file** passes the single file in that result version. For versions with several files, declare a workflow input with format `astrbot-file`, select the version and file in the launch form, then connect that named input. Use an Agent input contract or a **Map fields** node as the destination. Agent prompt placeholders accept text ports. Bound messages and references are available to the Agent as separate run inputs.

In **Test draft**, an upstream node outside the selected scope can use an **Exact historical artifact**. Choose the required version before starting. The run freezes that version and the bound fields. In the workbench, open **Bound port sources** to see the version and to preview or download linked files. For an approval-governed attachment, downloading requires approval of that exact version. Later revisions preserve the frozen references in existing runs.

![A bound managed file and its exact source version in the run workbench](/images/agent-teams-reference-ports-en.jpg)

## Reuse a workflow canvas

Use **Search nodes** in the toolbox. Drag a match onto the canvas or focus its button and press Enter. **Start from** offers the **Parse a JSON title** template and draft generation from a description. The template accepts the workflow input `text`, for example `{"title":"Quarterly report"}` encoded as a string. For generation, choose a model with tool use enabled and available to the default local workflow profile. AstrBot sends your description to that model and proposes a sequence of text-processing Agents. Review the tasks and bindings on the canvas, then use **Test draft** and **Save version**.

Hold Shift and drag on empty canvas space to select multiple nodes, or Ctrl/Cmd-click to add a node. Choose **Group selection** to label the set. **Add note** creates plain text you can edit in the inspector and move on the canvas. Copy and paste using the toolbar or Ctrl/Cmd+C and Ctrl/Cmd+V. Internal data and control connections follow the pasted nodes. If a dependency was outside the selection, use the diagnostic to find the pasted node and reconnect it. After reconnecting a missing control link, remove its diagnostic placeholder. Ctrl/Cmd+Z undoes the complete paste and Shift+Ctrl/Cmd+Z redoes it. With a canvas node focused, Enter selects it and the arrow keys move it.

![Real Dashboard canvas with a group and a note](/images/agent-workflow-reuse-en.jpg)

### Document workflow example

From an empty instance, configure a tool-capable chat model and an embedding model. In **Knowledge Base**, create a local knowledge base with the embedding model. Upload `threshold.md` containing “The inspection threshold is 40.”

In **More Features → Agent Teams → Assets → Workflows → New Workflow**, create a one-Agent workflow with the chat model. Set the Agent as the result node and declare a structured result with `text` (string) and `files` (array). Give it the task “Use `store_task_file` to save `inspection.md` with the exact text ‘The gauge value is 42.’ Submit a short text summary and include the returned file ID as `files: [{file_id: ...}]`.” Run a full draft test and verify the attachment on the completed task. Its exact artifact version supplies the file picker below.

1. On the canvas, select **Use structured ports** and declare required inputs `document` (`{"type":"object","format":"astrbot-file"}`) and `query` (string). Add **Read document** and **Retrieve knowledge**. Connect `document → file` and `query → query`, then select the local knowledge base.
2. Connect both `text` outputs to separate Agent inputs and write a task prompt asking it to compare the gauge value with the threshold. Select an existing role, model, Skills, and any required tools. Declare integer output fields `gauge` and `threshold`. Connect them to a **Map fields** node with matching input and output schemas, and select it as the workflow result. Connect the control paths through the Agent and map. The retrieval node also exposes a structured `matches` array with knowledge base, document, and chunk IDs.
3. Save the draft and choose **Test draft → Full workflow**. Enter the exact file reference and the query `inspection threshold`. Inspect the parser and retrieval results, the Agent's actual context and tool calls, and the final `{"gauge":42,"threshold":40}`. Then choose **From selected node**, start at the Agent, and select the exact output artifacts of both upstream nodes. Only the Agent and field mapping run.
4. Publish and start the fixed version with the same inputs. The fixed version keeps the local knowledge base ID, role version, and resource selections. Retrieval uses the current index contents. The retrieval node exposes a `warnings` array when vector retrieval or reranking fails and the remaining search still returns usable results.

![The document workflow canvas with the local knowledge base selected](/images/agent-teams-native-document-en.jpg)

Document reading uses the existing Markdown, text, PDF, EPUB, DOCX, and XLS/XLSX parsers. The node extracts up to 100,000 characters from a managed file up to 10 MiB. An approval-governed file requires approval for the exact artifact version. Check the affected node or its tool timeline for resource, file, and tool failures. Correct the draft or input and test again.

Start workflows containing these document or knowledge nodes from the Dashboard. Platform chat grants do not supply the Dashboard owner's file or knowledge base access.

Both native nodes count toward the root run's concurrency and step limits. Parsing uses zero model tokens. Retrieval shows unknown token usage because the knowledge base interface supplies no embedding or reranking consumption. An active token budget blocks explicit retrieval before the call. If a restart interrupts a recorded call before its output is saved, retry the failed node with the original inputs. A retry counts as another call under the root limit.


## Drafts, fixed versions and tests

Edits to assets and canvas layouts are saved automatically as drafts, including new drafts without a saved version. Check the save status: retry a failure, or retain local content or explicitly load the server draft when there is a conflict. If a fixed version changes, compare the original base and latest version, then keep the whole draft against the selected base or load the latest version and edit again. Fields are not merged.

Select **Save version** to validate the draft and create a fixed version. Published runs, chat grants and new references use saved versions; later edits do not change a run's frozen definition, inputs or configuration.

In the editor choose **Test draft** and check the displayed draft revision. For a workflow, select **Full workflow**, **Selected node only**, or **From selected node**; an autonomous team test includes the coordinator and dynamic members. The preview lists the steps and required inputs. For upstream data outside the scope, choose an **Exact historical artifact** and version or enter typed test data as prompted; sources requiring a pinned artifact must use an artifact. Review and choose **Start test**.

Tests make real model and tool calls and may incur costs or external effects. They use the selected draft revision without publishing a fixed version. Filter the run list by **Draft test** or **Published run**, and inspect both in the same workbench.

Approval targets and recommendations require real artifacts. An isolated ALL join, an incomplete loop, or a partial scope missing a parallel predecessor cannot start. Testing an Agent inside a loop on its own runs it once and requires explicit initial feedback.

![Live partial draft trial](/images/agent-teams-live-trial-en.png)

Open **Fixed versions** in a published workflow's editor, select any two versions, and expand changed sections to compare them. The comparison includes task order and control links. **Restore to draft** copies the selected version into the draft after confirmation. If another editor saved the draft or published a version in the meantime, reload and review before retrying. The restored draft can be tested and published as a new version. Existing runs retain their original definitions.

![Fixed version comparison in the Dashboard](/images/agent-workflow-versions-en.jpg)

## Connect inputs, outputs and control paths

Declare inputs in the input node. A typed Agent can bind a workflow input or a required string field from a task upstream on a control path. Use `{alias}` in the task to insert the bound value. Control lines determine execution order and path activation. Agents receive the fields explicitly bound to their inputs. Older definitions can also bind required scalar fields through the inspector. The default output is text. With the version 1 text contract, include a required string field named `text` on the result task. The text port checks that field, and the run returns the result task's complete validated artifact. Select the result task in the output node. After deleting nodes or changing fields, fix invalid references using the save or launch diagnostics.

The canvas supports zoom, fit, minimap, undo/redo and automatic layout; expand the toolbox and properties on narrow screens. Add a **Condition**, select an input or required upstream scalar, a comparison and a value of the same type, then connect its true and false ports. **All join** waits for every entered path to succeed and ignores skipped paths; if no path enters, the join is skipped. A local failure blocks dependent successors while independent paths can continue.

In a V2 graph, mark a task as a **Control entry task** or draw from the workflow input's control handle to that task. Once you declare entries, a task needs a control path from an entry to run. Connect each step's named data ports separately. The run graph shows which control paths were taken, and the task status explains skipped paths or unavailable inputs. An ALL join in this mode exposes an array named `results`. It includes the results of successful entered paths in the order their control edges were added. You can connect this array downstream or select the join as the workflow result.

![V2 condition with a separate control entry and boolean data link](/images/agent-teams-v2-control-en.jpg)

Add **List dispatch**, choose its Agent model and connect an array to `items`. Use `{item}` in the task prompt to read the current item. Set the item and concurrency limits in the node properties. Each limit is at most 30. Item tasks reuse the available role instances and respect the workflow and role concurrency limits. Each item submits a text result. The result lists the original positions as `item-0`, `item-1`, and so on, even when items finish out of order or have identical values. An empty list yields an empty result array. If an item fails, open the dispatch scope in the run workbench to see the failed item and retry its original input.

Add a saved workflow or autonomous team as a call node from **Reusable assets** and select a fixed version. On a structured canvas, drag matching named inputs and outputs to the call node. Optional inputs can be left open to use the child version’s defaults. A connected explicit null is preserved when the contract allows it. Autonomous teams expose the declared text inputs of their fixed version. Draw control links to activate the call and its successors.

Each call creates a scope inside the same root run and receives the declared values linked on the canvas. It returns the selected public output fields. The public result has its own exact artifact reference when internal fields are omitted or the caller configures a separate approval. Its explicit managed file references retain their source. Inspect the child scope in the run to see its tasks and approvals. Child scopes share root limits and retain their own limits. Publishing another version leaves active runs on their frozen dependencies.

### Human nodes and bounded revision

Add a **Human question** with a question and optional choices; leave choices empty for free text. You can explicitly select upstream required string fields for a recommendation and reason. Add **Version approval** and connect its artifact task and approval/rejection paths. Approval binds a specific artifact and version; rejection requires feedback and does not automatically rerun the author.

For repeated review, open **Loops & subflows** and add a bounded loop with the call and its approval in the body. Select the approval’s `approved` field as the exit condition and compare it with `true`. Set a maximum such as two rounds. Map the approval’s `text` feedback to the call’s next-round revision input and give that input a first-round binding or default. Feedback transfers the selected value with its original type. A missing optional value uses the destination’s default when provided. In a single-node trial, the optional feedback source can also be omitted.

![Structured call with a two-round approval loop](/images/agent-teams-fixed-rework-en.jpg)

An approval targeting a fixed call uses the exact version returned by that call. On rejection, the loop keeps the old request and artifact and gives the next round a new approval. Work outside the loop reads the passed round. When the limit is reached, the run retains the history and reports that the loop needs attention. In the run workbench, open an artifact from a specific round to view the exact saved version. A pending child approval can be reopened from the original root run after a restart.

Human questions, dynamic choices and approvals wait indefinitely by default. You can set a deadline to keep waiting or use the valid recommendation displayed when the request was created. If the recommendation is missing, invalid or unsupported by available evidence, the request still waits for a person; no new model decision is requested at the deadline.

## Start and inspect a run

Choose **Start Run** on the asset card and fill in declared inputs and the team goal. Check dependency and executor readiness and resolve unavailable resources or permissions before starting. A run uses the asset, roles, inputs and configuration frozen at launch. Asset edits affect later versions and runs.

**Runs → In progress** shows unfinished runs; **Needs attention** groups runs needing attention and human requests in **Needs my attention**; **History** pages through accessible asset runs. Choose **View Run** from the list.

The desktop workbench organizes tasks, process, and todos and artifacts into columns. Select a task to read saved model responses, thinking actually supplied by the provider, tool activity and artifacts. Load earlier process records or expand actual model context and its assembly sources as needed. Context may be truncated or unavailable for a remote executor; sources precede plugin changes and may not all remain in the final request. Switch to the execution graph for the read-only run snapshot and task states; expand child scopes and loop rounds. On narrow screens, switch panels to see the same content. Background refresh preserves answers being entered.

![Live run workbench](/images/agent-teams-live-workbench-en.png)

Select a task on the left, read its process in the center, and handle human requests and artifacts on the right. Expand **Load older process** for separate process, message, event, and attempt history controls. On narrow screens, switching panels preserves the current view and answers being entered.

Waits for predecessors, capacity or people show their reasons; a human wait blocks only the affected path. Choose **Open process** on a todo to check the evidence, then answer. For approval, first preview the specified version and files, then approve or reject with feedback. If the specified version is unavailable, create that version first; the system will not substitute the latest. After approval, export that approved version and its files. Answers can be saved while paused and are consumed after the run continues; cancellation closes requests and prevents late advancement.

Closing or refreshing the page does not cancel execution. Reopen the run to read saved process and results. Permission to answer a todo does not grant the run owner's process permissions.

### Open the same run in Dashboard Chat

In the Bot run workbench, choose **Open in Chat**. Dashboard Chat shows one conversation per root run, even when the same asset runs several times. Open a conversation from the Chat sidebar or its direct link (`#/chat/runs/<root-run-id>`) to see the asset, fixed version, root ID and current state. For draft tests, Chat shows the draft revision. Choose **Open run workbench** to return to that exact run.

In Chat, the task list expands child workflows, team scopes and loop rounds. The execution panel shows the selected task's saved events, attempts and recorded input sources. Use **Load older process** to page back. On narrow screens, switch panels with **Tasks**, **Execution**, and **Inbox & artifacts**. The right panel opens exact artifact versions and downloads authorized files.

In **Inbox & artifacts**, answer a specific human request or preview the exact version before approving or returning it with feedback. Bot and Chat show the same requests, including those from nested workflows. If another tab has already decided, the card refreshes to show the saved decision. A conflict from the Bot inbox opens the corresponding run if it is not already open. If the workflow sends revised work for review, open its new request and preview that version before deciding.

If the connection drops, the open conversation will resume from the last applied event. After closing or refreshing Chat or signing in again, choose the **Workflow runs** icon (branch) at the right of the **This instance** row in the Chat sidebar. The sidebar then lists workflow runs only; the **Chats** icon (speech bubble) lists ordinary conversations only, and neither includes the other. Scroll down or use **Load more** to find older runs. Use the root ID shown beside the name to tell repeated runs apart.

![Chat sidebar filtered to workflow runs with the selected root run open](/images/agent-teams-chat-filter-en.png)

![Open a run conversation from the Bot workbench](/images/agent-teams-run-bot-en.png)

![Run conversation showing saved execution, an artifact, and the link back to the workbench](/images/agent-teams-run-chat-en.png)

In **Inbox & artifacts**, use **Whole run controls** to pause, continue or cancel the run. **Cancel run** moved here from beneath the Chat conversation heading. Select a failed task to retry it in the same conversation. The controls show the outcome and explain when you need to review a saved result or resolve a tool call in the Bot workbench.

![Shared run controls and the failed task retry in Chat](/images/agent-teams-chat-controls-en.png)

After a published run completes, choose **Run again** and confirm the displayed version. The new conversation uses the original fixed asset version and inputs, including exact input artifact versions. Open the original conversation to compare its process and artifacts with the new run. If the launch response is lost, reopen the original conversation in the same browser and choose **Run again** to recover the same launch.

The run owner can open the conversation with an administrator Dashboard session. The external source-authorized link described below has its own access checks. **Archive Session** moves the conversation to **View Archive** and execution continues. Restore the conversation from the archive to show it in the sidebar again.

## Recovery and usage

In run details, pause, continue or cancel the whole run. Pause stops new work from being admitted, but already dispatched external calls may finish; cancellation cannot undo effects already produced. After checking frozen inputs, **Retry failed task** creates a new attempt. For tasks that use the built-in executor, select **Continue same attempt** when a resumable checkpoint is available. Continuing reuses saved tool results. If a model request failed after a tool result was saved, continuing can resend that request with the saved result in context. Model requests may incur charges. When the current attempt has saved tool results, the workbench disables ordinary retry. If continuation is unavailable, review the saved results and external ledger before deciding whether to start a new run.

When a tool result is unknown, select the task and expand the call in **Agent process**. Check its parameters, partial output and external evidence. Only a query or safe reconciliation explicitly supported by the tool can establish the outcome; an inconclusive query remains pending. If the complete result or failure has been verified externally, record the evidence and result. You can explicitly request a new execution, which may repeat external effects. An ordinary retry cannot bypass this decision. For a remote executor supporting **Query original remote run**, query the original identity first; an unavailable or unknown status does not prove nothing happened. Inspect the remote service before deciding on a new attempt.

In the asset's **Scope limits**, optionally set call steps, members, loop iterations and token limits; save a new version for new runs. **Runs → View Run → Usage and limits** shows root and child usage, including past attempts. Usage not reported by a provider is unknown, not free; unmetered charges inside plugins or tools are not hard limited here. Estimates and concurrent reservations cannot guarantee zero overspend. When a limit is reached, new calls stop and saved results and effects remain available.

## Invoke and answer from chat

1. Ask the caller to send `/asset whoami` in the intended platform conversation. The asset owner opens **Chat access** on its card, pastes the complete response, checks the platform, sender, source conversation and profile, and chooses **Open this version**. The grant binds that sender, exact conversation and fixed version; pasted text alone proves no identity and grants no management permission. A group grant allows the final result to be visible in that group.
2. All reachable roles, allowed members and nested workflows must use the source profile and obey its model, persona, tool, skill and knowledge-base restrictions. Import does not grant chat access. Revoking access blocks future calls and result delivery; existing execution records remain.
3. The caller uses `/asset list` to see accessible assets, then follows the card's syntax, `/asset <name or ID> | {"input":"value"}`. Ambiguous names or missing inputs are clarified first; ordinary chat does not automatically select an asset. Use `/asset status <run ID>` in the original authorized conversation to retrieve notifications and the link for an existing run.
4. Open the run link and keep the page open. The **original sender in the original conversation** sends the displayed `/asset connect <challenge>` command. The connection is limited to this run and lasts 30 minutes. During a brief outage, keep the page open and wait for it to retry or select **Check connection / refresh** to return to the same run and pending requests. Reloading the page, connection expiry, or a server restart requires confirming a new connection for the same run. The link or challenge alone does not give another group member the sender's identity.
5. Answer the same human todo or approve its exact artifact version in the restricted page, or follow the answer command in the original conversation's notification. Approval exposes only the requested version and its declared files; after completion the caller can read the root run's published final result and files. The owner's process, thinking, tool records and resource configuration remain private. Revocation or a source profile change also prevents an existing connection from reading or writing.

Satori conversations and Misskey note conversations cannot currently retain the required unique source identity and cannot invoke assets; Misskey direct messages and chat rooms can. A WeCom intelligent bot needs an available original conversation response channel: HTTP callback mode requires webhook-only sending to be disabled and a nonempty initial response. If the channel expires or the service restarts, send `/asset status <run ID>` in the original authorized conversation to retrieve pending content.

## Start the same fixed version from Bot, a platform or the API

All three entries check the published input contract and freeze its effective inputs and fixed dependencies at launch. Send a JSON object with the declared names and types. A missing field receives its declared default. An explicit `null` remains null and must be allowed by the input type. In platform chat, `/asset list` shows the granted fixed version, required names and the types of structured inputs. Platform redelivery with the same upstream message ID reuses the original run. Sending the command as a separate message starts a separate run. An adapter without a stable upstream ID cannot identify redelivery.

For Bot, reopen the same asset and version in the original browser tab and resubmit the same inputs after a lost response. The Dashboard checks the pending request before checking local resources again. Each independent start gets its own run and Chat conversation. Fixed child calls use the root's budgets and appear inside that run conversation.

![Three independent starts in the live Dashboard Chat](/images/agent-teams-authorized-launch-en.jpg)

### Authorize a public API caller

1. In Dashboard **Settings → OpenAPI**, create a key with the `workflow` scope. Copy the key secret when issued. Use your signed-in Dashboard administrator session to read `GET /api/apikey/list` and obtain its `key_id`. Use the identifier when granting access and the secret when calling the public API.
2. As the Dashboard asset owner, send `PUT /api/v1/workflow_assets/{asset_id}/api_access/{key_id}` with `{"version":1}`. Authenticate this management request with the owner's Dashboard session, using its cookie or Dashboard JWT. The scope and the fixed-version grant are both required. Get the asset ID and its published version from its Dashboard card or the owner-only `GET /api/v1/workflow_assets` endpoint.
3. With `Authorization: ApiKey <key-secret>`, send `POST /api/v1/workflows/{asset_id}/runs` with `{"version":1,"request_id":"inspection-001","inputs":{"query":{"gauge":42}}}`, adapting `inputs` to the asset. Keep the same `request_id` for a retry with the same effective inputs. A changed effective payload under a committed ID returns HTTP 409. Use a new ID for an independent launch.
4. Poll `GET /api/v1/workflows/runs/{run_id}` with the originating key. The response gives the root status and its published result upon completion. The Dashboard owner can open the root's Chat conversation. The API key has no access to the Dashboard workbench, child runs, or platform connection credentials.

Revoke an individual grant with the owner's Dashboard session using `DELETE /api/v1/workflow_assets/{asset_id}/api_access/{key_id}`, or revoke the key in **Settings → OpenAPI**. Revocation blocks replays, result reads and further work at the next admission check. Calls already admitted can finish. External launch grants do not include historical Dashboard artifact or file inputs, native document or knowledge nodes, or installed workflow plugins, including inside fixed child calls. AstrBot's existing reminder and Agent schedule do not start fixed workflow versions.

## Import, export and delete

Under **Assets → Teams / Workflows**, choose **Export asset JSON**. The package includes the entry's fixed child assets, roles and resource requirements, but not credentials, resource contents, grants or run history. On the destination, choose **Import asset package → Validate and preview** and explicitly select compatible local profiles, models, personas, tools, skills and knowledge bases for every requirement, even if IDs match. Configure missing resources first, then confirm installation. The complete dependency closure is installed or the operation fails as a whole; import does not install plugins, execute scripts or start a run.

Choose **Delete** on a team, workflow or role card and confirm the impact. All versions disappear from new selections and cannot be started directly, including through existing chat grants. Existing pinned references, active runs, history and artifacts remain. Existing parent assets can use their fixed references but cannot add new references. Exporting an active parent still includes its pinned dependencies; reimporting a package corresponding to a deleted local asset conflicts and does not restore it. Deletion cannot be undone.

![Team deletion confirmation](/images/agent-teams-delete-asset-en.jpg)

This image uses the production component with isolated sample data; it is not a live backend integration test.

## Previous entry points

| Previous entry | Current entry |
| --- | --- |
| Team sidebar, Members, Workflow Editor | Assets → Teams / Workflows / Role templates |
| New Workflow and New Canvas Workflow | Assets → Workflows → New Workflow |
| Run Monitor and independent runs | Runs → All runs / In progress / Needs attention, then View Run |
| Previous History tab | Runs → History |
| Human questions only in run details | Runs → Needs attention → Needs my attention, or the same todo in View Run |
| Previous chat run link | Use `/asset status <run ID>` in the original conversation for the restricted chat run; the owner can still open full details after signing in |

Legacy definition migration and legacy run checks have been removed. The new system does not automatically convert or delete old Agent Teams data.
