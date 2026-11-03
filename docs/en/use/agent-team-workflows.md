# Agent Teams assets and run workbench

The canvas and workbench screenshots come from the real Dashboard, an isolated database and a controlled model service.

Open **More Features → Agent Teams → Assets**:

- **Teams** are autonomous teams whose coordinator assigns work toward a goal and creates members from presets.
- **Workflows** arrange one or more Agents, control nodes and child assets on a canvas.
- **Role templates** hold reusable responsibilities and local resource configuration for teams and workflows.

Asset cards show the fixed version, a task or member summary, and draft status, with edit and run actions below. The lower-left icons on team and workflow cards open chat access, export, and deletion respectively; hover to read their names or use the keyboard to activate them. Light and dark themes are supported.

## Create and configure

Choose **Assets → Workflows → New Workflow**. Enter a name and optionally select a default model. Add Agents directly on the canvas without first creating a role template. Select an Agent to configure its task, responsibilities, profile, persona, model and resources. An unselected persona means no persona; an Agent without its own model uses the workflow default. Before launch, every local Agent must resolve to an allowed model.

Alternatively, select a role template and its **fixed version** in the Agent properties. Enable an override to replace a template value; disable it to inherit. An empty responsibility clears it, clearing the persona means no persona, and clearing the model returns to the workflow default. Tools, Skills and knowledge bases independently inherit or use a complete custom selection; an empty custom selection disables that category. Upgrade the reference explicitly when a new template version is available; existing runs do not change automatically. You can save a directly configured Agent as a template, then explicitly select it if you want to use it.

Choose **Assets → Teams → New Autonomous Team**, enter a name and default model, and configure the coordinator and member presets. Each can be configured directly or use a fixed role version with overrides; the same template can be used more than once with different overrides. The coordinator can create only allowed presets. Set member limits (including the coordinator), concurrency and member task limits; enter the goal at launch. Final version approval and an output contract are optional.

![Live workflow canvas](/images/agent-teams-live-canvas-en.png)

Add nodes on the left, connect them on the canvas, and edit the selected node in the right inspector. Expand **Execution settings and budgets** above the canvas to adjust limits; draft save status is shown above. Collapse either side panel to make more room for the canvas.

## Drafts, fixed versions and tests

Edits to assets and canvas layouts are saved automatically as drafts, including new drafts without a saved version. Check the save status: retry a failure, or retain local content or explicitly load the server draft when there is a conflict. If a fixed version changes, compare the original base and latest version, then keep the whole draft against the selected base or load the latest version and edit again. Fields are not merged.

Select **Save version** to validate the draft and create a fixed version. Published runs, chat grants and new references use saved versions; later edits do not change a run's frozen definition, inputs or configuration.

In the editor choose **Test draft** and check the displayed draft revision. For a workflow, select **Full workflow**, **Selected node only**, or **From selected node**; an autonomous team test includes the coordinator and dynamic members. The preview lists the steps and required inputs. For upstream data outside the scope, choose an **Exact historical artifact** and version or enter typed test data as prompted; sources requiring a pinned artifact must use an artifact. Review and choose **Start test**.

Tests make real model and tool calls and may incur costs or external effects. They use the selected draft revision without publishing a fixed version. Filter the run list by **Draft test** or **Published run**, and inspect both in the same workbench.

Approval targets and recommendations require real artifacts. An isolated ALL join, an incomplete loop, or a partial scope missing a parallel predecessor cannot start. Testing an Agent inside a loop on its own runs it once and requires explicit initial feedback.

![Live partial draft trial](/images/agent-teams-live-trial-en.png)

## Connect inputs, outputs and control paths

Declare inputs in the input node. Add input references to an Agent, choosing a workflow input or a required scalar field of an upstream task on a dependency path, and use `{alias}` in its task. Connections determine order; they do not share upstream conversations or tool history. The default output is text; define output fields and types for structured data. Select the result task in the output node. After deleting nodes or changing fields, fix invalid references using the save or launch diagnostics.

The canvas supports zoom, fit, minimap, undo/redo and automatic layout; expand the toolbox and properties on narrow screens. Add a **Condition**, select an input or required upstream scalar, a comparison and a value of the same type, then connect its true and false ports. **All join** waits for every entered path to succeed and ignores skipped paths; if no path enters, the join is skipped. A local failure blocks dependent successors while independent paths can continue.

Add a saved workflow or autonomous team as a call node: select a fixed version and map each declared input to a workflow input or upstream field. Each call has its own scope and receives only explicitly mapped values; enter that scope in the run to inspect tasks and results. An autonomous coordinator can call only fixed workflow versions explicitly allowed in its configuration. Set nesting depth and concurrency limits; child calls share root limits.

### Human nodes and bounded revision

Add a **Human question** with a question and optional choices; leave choices empty for free text. You can explicitly select upstream required string fields for a recommendation and reason. Add **Version approval** and connect its artifact task and approval/rejection paths. Approval binds a specific artifact and version; rejection requires feedback and does not automatically rerun the author.

For repeated review, add a **bounded loop**: select the body, exit result and comparison, and maximum iterations, then explicitly map the previous draft and review feedback into the next iteration. Each round retains separate tasks and artifacts. Reaching the limit without passing needs attention and is not success. Approval of one version does not approve a later revision, and revision does not automatically rerun consumers.

Human questions, dynamic choices and approvals wait indefinitely by default. You can set a deadline to keep waiting or use the valid recommendation displayed when the request was created. If the recommendation is missing, invalid or unsupported by available evidence, the request still waits for a person; no new model decision is requested at the deadline.

## Start and inspect a run

Choose **Start Run** on the asset card and fill in declared inputs and the team goal. Check dependency and executor readiness and resolve unavailable resources or permissions before starting. A run uses the asset, roles, inputs and configuration frozen at launch. Asset edits affect later versions and runs.

**Runs → In progress** shows unfinished runs; **Needs attention** groups runs needing attention and human requests in **Needs my attention**; **History** pages through accessible asset runs. Choose **View Run** from the list.

The desktop workbench organizes tasks, process, and todos and artifacts into columns. Select a task to read saved model responses, thinking actually supplied by the provider, tool activity and artifacts. Load earlier process records or expand actual model context and its assembly sources as needed. Context may be truncated or unavailable for a remote executor; sources precede plugin changes and may not all remain in the final request. Switch to the execution graph for the read-only run snapshot and task states; expand child scopes and loop rounds. On narrow screens, switch panels to see the same content. Background refresh preserves answers being entered.

![Live run workbench](/images/agent-teams-live-workbench-en.png)

Select a task on the left, read its process in the center, and handle human requests and artifacts on the right. Expand **Load older process** for separate process, message, event, and attempt history controls. On narrow screens, switching panels preserves the current view and answers being entered.

Waits for predecessors, capacity or people show their reasons; a human wait blocks only the affected path. Choose **Open process** on a todo to check the evidence, then answer. For approval, first preview the specified version and files, then approve or reject with feedback. If the specified version is unavailable, create that version first; the system will not substitute the latest. After approval, export that approved version and its files. Answers can be saved while paused and are consumed after the run continues; cancellation closes requests and prevents late advancement.

Closing or refreshing the page does not cancel execution. Reopen the run to read saved process and results. Permission to answer a todo does not grant the run owner's process permissions.

## Recovery and usage

In run details, pause, continue or cancel the whole run. Pause stops new work from being admitted, but already dispatched external calls may finish; cancellation cannot undo effects already produced. After checking frozen inputs, **Retry failed task** creates a new attempt. An interrupted built-in executor task can **Continue same attempt** at supported checkpoints. Saved model responses and known tool results are reused; an unsaved model request may be sent again and incur charges.

When a tool result is unknown, select the task and expand the call in **Agent process**. Check its parameters, partial output and external evidence. Only a query or safe reconciliation explicitly supported by the tool can establish the outcome; an inconclusive query remains pending. If the complete result or failure has been verified externally, record the evidence and result. You can explicitly request a new execution, which may repeat external effects. An ordinary retry cannot bypass this decision. For a remote executor supporting **Query original remote run**, query the original identity first; an unavailable or unknown status does not prove nothing happened. Inspect the remote service before deciding on a new attempt.

In the asset's **Scope limits**, optionally set call steps, members, loop iterations and token limits; save a new version for new runs. **Runs → View Run → Usage and limits** shows root and child usage, including past attempts. Usage not reported by a provider is unknown, not free; unmetered charges inside plugins or tools are not hard limited here. Estimates and concurrent reservations cannot guarantee zero overspend. When a limit is reached, new calls stop and saved results and effects remain available.

## Invoke and answer from chat

1. Ask the caller to send `/asset whoami` in the intended platform conversation. The asset owner opens **Chat access** on its card, pastes the complete response, checks the platform, sender, source conversation and profile, and chooses **Open this version**. The grant binds that sender, exact conversation and fixed version; pasted text alone proves no identity and grants no management permission. A group grant allows the final result to be visible in that group.
2. All reachable roles, allowed members and nested workflows must use the source profile and obey its model, persona, tool, skill and knowledge-base restrictions. Import does not grant chat access. Revoking access blocks future calls and result delivery; existing execution records remain.
3. The caller uses `/asset list` to see accessible assets, then follows the card's syntax, `/asset <name or ID> | {"input":"value"}`. Ambiguous names or missing inputs are clarified first; ordinary chat does not automatically select an asset. Use `/asset status <run ID>` in the original authorized conversation to retrieve notifications and the link for an existing run.
4. Open the run link and keep the page open. The **original sender in the original conversation** sends the displayed `/asset connect <challenge>` command. The connection is limited to this run and lasts 30 minutes. After expiry, refresh or server restart, reconnect to continue the same run. The link or challenge alone does not give another group member the sender's identity.
5. Answer the same human todo or approve its exact artifact version in the restricted page, or follow the answer command in the original conversation's notification. Approval exposes only the requested version and its declared files; after completion the caller can read the root run's published final result and files. The owner's process, thinking, tool records and resource configuration remain private. Revocation or a source profile change also prevents an existing connection from reading or writing.

Satori conversations and Misskey note conversations cannot currently retain the required unique source identity and cannot invoke assets; Misskey direct messages and chat rooms can. A WeCom intelligent bot needs an available original conversation response channel: HTTP callback mode requires webhook-only sending to be disabled and a nonempty initial response. If the channel expires or the service restarts, send `/asset status <run ID>` in the original authorized conversation to retrieve pending content.

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
