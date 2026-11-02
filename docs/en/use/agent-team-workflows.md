# Agent Teams asset and run workspace

Open **More Features → Agent Teams → Assets**. **Teams** contains versioned autonomous teams, **Workflows** contains graph workflows, and **Role templates** configures reusable responsibilities and local resources. A workflow can configure its Agent directly without a role template. Import, export, chat access, editing and launch actions remain on the asset cards. After installation, the workspace opens the Teams or Workflows directory for the imported entry.

Workflow and autonomous team edits, including canvas layout, save automatically as drafts. Newly created drafts appear in the asset list even before a version exists. Check the save status; retry a failed save, or keep local content and deliberately load the server copy after a conflict. **Save version** validates the current draft and creates a fixed version. Formal runs and chat access use saved versions, not drafts.

When a newer fixed version causes a conflict, review the old and current base versions. You can keep the entire draft on the reviewed latest base, or load that version to edit it; no fields are merged. If the draft or latest version changes during review, review the conflict again.

![The workflow asset workspace](/images/agent-teams-current-en.jpg)

The workspace and canvas screenshots use the actual components with isolated sample assets and a simulated resource catalog. They do not depict a real model run.

In the Agent inspector, choose **Direct configuration** or **Role template**. A template reference fixes both the role and its version. A newer version is offered for an explicit upgrade; existing workflows and runs do not change automatically. Turn on an override to replace the template value, or turn it off to inherit it. An empty responsibility clears it; clearing the persona selects no persona, and clearing the model uses the workflow default. Tools, Skills, and knowledge bases can each inherit or use a complete custom selection; an empty custom selection disables that category. A directly configured Agent can be saved as a role template without its task or the workflow default model; select the saved template explicitly to use it.

![A pinned role version and its upgrade action](/images/agent-teams-role-version-en.jpg)

![Role overrides and effective configuration preview](/images/agent-teams-role-overrides-en.jpg)

## Conditions and ALL joins

Add **Condition** or **ALL join** from the workflow canvas toolbox. Select a condition and choose its `value` from a declared workflow input or a required scalar field of an upstream task. Set a comparison and a value of the same type, then connect the named **True** and **False** handles to the paths to run. Select an existing dependency to change its condition exit. The condition is a control step: it does not call a model or produce an artifact, and cannot be selected as the workflow result or referenced as ordinary data.

Connect parallel paths to an **ALL join**. It waits for all entered paths to succeed, ignores skipped paths, and is skipped if no path was entered. A failed path blocks its dependants while unrelated paths continue. The run view distinguishes waiting for predecessors (with their node names), waiting for capacity, skipped paths and dependency failures; these waits do not create human requests. Select an Agent after the join and explicitly choose any upstream fields it needs.

![Condition exits and an ALL join in the workflow canvas](/images/agent-teams-branches-en.jpg)

The screenshot shows the actual canvas with isolated example data, not an actual model run.

## Answer human questions

On the workflow canvas, add a **Human question**, enter the question and either list allowed answers or leave the list empty for free text. Optionally select an upstream Agent task and its required string fields for the recommended answer and reason. Connect that task as a dependency. The request records the exact source artifact version and selected fields; invalid or missing recommendations do not prevent a manual answer. No model is called to create a recommendation, and there is no default deadline.

Open **Runs → Needs attention → Needs my attention** to see pending requests you can answer, including requests beyond the recent-run list. The same card appears in **View Run**. Use **Open process** to inspect the associated task and its saved process, then enter an answer. Pausing a run permits saving an answer, but execution consumes it only after continuing. Cancelling closes pending requests and prevents later progress. Independent branches continue while a question waits.


![Human request inbox with an explicit recommendation](/images/agent-teams-human-inbox-en.jpg)

The screenshot uses the production components with isolated sample data; it does not show a real backend run.

## Navigation and previous entry points

| Previous entry | Current entry |
| --- | --- |
| Human questions only in a run detail | Runs → Needs attention → Needs my attention, or the same card in View Run |
| Autonomous teams within Workflow Assets | Assets → Teams |
| New Workflow / New Canvas Workflow | Assets → Workflows → New Workflow (one canvas entry) |
| Role templates | Assets → Role templates |
| Independent Runs | Runs → All runs, In progress, Needs attention, History |
| Previous team sidebar, Workflow Editor, Members, Run Monitor | Assets → Teams / Workflows / Role templates; Runs → View Run |
| Previous History tab | Runs → History (asset runs only) |
| Migrate previous definitions / active legacy run check | Removed; configure assets and start runs directly |

The workspace uses versioned assets. A workflow can be saved and started without a role template; autonomous teams still use their configured roles. Definition migration and legacy run checks are no longer part of the workflow.

## Run history

![The current asset run history](/images/agent-teams-history-current-en.jpg)

Screenshots use an isolated frontend preview with sample run records and the same components as the WebUI.

**Runs → In progress** includes unfinished runs and runs requiring intervention. **Needs attention** includes human waits, blocks, failures, interruptions and pauses. **All runs** returns to the recent run list, which retains its 50-record limit.

Open **Runs → History** to browse asset runs accessible to the signed-in user by date. **Load more history** reads the next page; **Refresh** reloads from the first page while keeping the existing records visible. **View Run** opens the run's frozen snapshot, artifacts and saved member process. Legacy engine records, long-term member conversations and migration actions are no longer displayed.

## Read a live run

![Live run workspace with sample data](/images/agent-teams-live-process-en.jpg)

The screenshot shows the actual workspace components with isolated sample data; it is not a real model run.


Open **Runs → View Run** and select a task in the left column. Its saved model and tool observations appear in the middle; existing human requests and artifacts appear on the right. The execution graph remains available in the overview. A running local Agent displays real text and reasoning as its provider returns them; if the provider supplies only a final response, the workspace says so. Tool cards show the parameters and progress actually returned by the tool. No reasoning or internal tool progress is invented when unavailable.

Reopening the run reads saved observations. An interrupted delivery remains separate from a later retry. The page follows new content while you remain at the bottom; scrolling away keeps your position. Leaving the page stops observation, not execution. The status shown comes from the saved run state.

## Inspect v2 runs

Choose **View Run** on a v2 record to see the goal, task progress and waiting reasons, then handle **Human requests** or select a task to read its artifacts. Expand usage details and the execution graph as needed. The graph is labelled **Run Snapshot (read only)** and has no definition toolbox or property editor. Editing through **Assets** affects future runs. Role cards identify a **Role template**, while runtime details identify a **Run member**.

![Progress, human waits and a fixed snapshot in the real WebUI](/images/agent-workbench-run-en.jpg)

On narrow screens, expand the collapsed toolbox or properties heading when needed; selecting a node exposes its properties. Toolbars wrap and dialog actions stay in their action area. Tasks, requests and artifacts stack vertically, and long names wrap. Status uses text and icons as well as AstrBot theme colors. The workspace and its dialogs disable transitions and animations when the system requests reduced motion. Import resource selections and nested panels follow the same spacing, rounding and narrow-screen rules.

![The actual canvas in the dark theme](/images/agent-workbench-dark-en.jpg)

![Human requests on a narrow screen](/images/agent-workbench-mobile-en.jpg)

## Move a complete nested asset to another instance

On instance A, choose **Assets → Teams / Workflows → Export asset JSON** and download the package. Version 3 includes the entry's exact nested team and workflow versions, pinned roles, inline configurations and their explicit resource requirements. It preserves inherited, null and empty selections. Credentials, resource content, authorization, conversations and run history are not exported. Older slot-based packages are rejected; rebuild them in the current format.

On instance B, open **Import asset package**, paste or select the JSON, and choose **Validate and preview**. For every source profile, model, persona, tool, skill and knowledge base, select a compatible local resource explicitly, even when its ID matches the source. Review disabled candidates and their reasons, effective configuration and source fields. A different source fingerprint is a prompt to review, and a compatible local model may still be selected. Profile permissions and resource availability are checked again at confirmation. Configure missing resources on the target instance before retrying.

![Resource choices and effective configuration in an isolated component example](/images/agent-teams-package-resources-en.jpg)

Choose **Confirm installation** when all requirements are ready. The complete closure and import receipts are committed together; failure leaves no partial installation. Identical reimports reuse their fixed identities; a different definition or local selection for an already imported source version is rejected. The resulting local definitions contain actual local IDs and use the normal **Start Run** action. Re-export reads those local definitions. Imports do not install plugins or services, grant chat access or run package scripts.

## Explicit invocation from ordinary chat

In the intended platform conversation, send `/asset whoami` and paste the entire response into **Chat access**. For example:

```json
{"platform_id":"qq-main","sender_id":"12345","source":"qq-main:GroupMessage:88888","profile_id":"default"}
```

Check the read-only fields before opening the fixed version. The group UMO identifies the conversation, not its sender. Copying this text does not prove identity or bypass asset management permissions. A display name or a sender ID that matches a Dashboard account does not link those identities.

An asset owner opens **Agent Teams → Assets** and chooses **Teams** for an autonomous team or **Workflows** for a single-task or canvas workflow. On the asset card, open **Chat access**, paste the complete response, check the read-only fields and choose **Open this version**. The grant pins the displayed asset version and allows its final result to be delivered to that exact conversation. A group conversation grant therefore explicitly allows the result to be visible in that group. Every reachable role, allowed team member and nested workflow must use the source profile; existing model/persona pools and tool, skill and knowledge-base restrictions still apply. Importing a package does not open it to chat callers. Use **Revoke access** to stop subsequent external work and result delivery; already delivered work remains recorded.

Use `/asset list` to see accessible names, IDs, pinned versions and required input names. Invoke an asset explicitly, for example:

```text
/asset Research report | {"topic":"wind energy"}
```

Use the asset ID when a name is ambiguous. If required input is missing, supply the named fields in the next explicit command; no run starts until the selection and inputs are valid. Ordinary chat does not automatically choose an asset. `/asset status <run ID>` reconnects to the accepted root run in the same authorized source conversation.

The source receives acceptance, a notice when human input is needed, and the final contracted result. Long results are sent in text parts. Notices include the run ID and `/#/asset-run?workflow_run=...` under this instance's WebUI address. This opens **Chat run**. The page displays `/asset connect <connection code>`. The actual initiator must send that command in the original conversation while keeping the browser open. Once confirmed, the initiator can view this run, answer its existing persistent human requests and read the final result without a Dashboard account. A group-visible link or connection code does not authenticate another group member as the initiator.

The connection is limited to this run and lasts 30 minutes. After expiry, a page refresh or a server restart, reconnect and confirm again to continue the same run and todo; this does not launch the asset again. Revocation or a source-profile change also invalidates access through existing connections. Approval previews are limited to the exact version named by that request and files declared by that version; other child artifacts, versions, export and editing remain unavailable. Owner process, thinking, tool records and resource configuration are not shared.

Entry mapping: chat notices now use the restricted initiator entry `/#/asset-run?workflow_run=...` instead of `/#/agent-teams?workflow_run=...`. The latter remains the owner's full **Runs** detail page after Dashboard login. For an old message containing a run ID, send `/asset status <run ID>` from the original authorized source to obtain the new entry.

After completion, use **Preview File** to read a final text file in this page or **Download File** to retrieve its complete bytes with the original filename. Binary files use the download action. The list includes only files declared by the immutable result version actually published by this root run, including results without an approval request. New owner revisions, unrelated child files and files from other runs are excluded. The same source confirmation and live grant checks apply to each file request.

Chat invocation records the platform-qualified initiator and source while the asset owner retains Dashboard control of the delegated run. Its synthetic member turns remain ordinary members, including when IDs match an administrator. Only the root run's completed result is returned to chat; child results and unapproved versions cannot be read through chat status. Restart resumes saved notification cursors and existing human requests. A crash between platform acceptance and saving its receipt can repeat a message; it does not start another asset run.

The source must identify one audience and account. Satori conversations and Misskey note conversations are currently unavailable for asset invocation because their saved source identity does not preserve that distinction. Misskey direct chats and rooms remain supported. Use a dedicated supported conversation or ask the owner to start the asset in WebUI instead.

WeCom AI Bot asset replies require an available original response channel. HTTP callback mode requires webhook-only delivery to be disabled and a nonempty initial response text so the platform receives a pollable stream; otherwise `/asset` does not create a run. Long-connection replies can use the original request even when ordinary replies use webhook-only mode. Asset notices never use the global group webhook. After an original response expires or the process restarts, send `/asset status <run ID>` from the same authorized conversation with an available response channel to retrieve pending notices and results. Pending messages are retained when an exact source channel is unavailable.

## Autonomous teams

![Autonomous team preset configuration with sample data](/images/agent-teams-autonomous-presets-en.jpg)

Choose **Assets → Teams → New Autonomous Team**. Enter the **Team name** and choose a default model. Configure the coordinator and add member presets; each can be configured directly or reference a fixed role template version. Direct configurations select a profile, responsibility, persona and resources. Template presets can override those settings, including clearing a persona or selecting an empty resource list. A template can be used by several presets with different overrides. The coordinator can create only the listed member presets; the goal is entered at launch. Set **Maximum members** (including the coordinator), **Maximum concurrent tasks** and **Maximum member tasks**. Role instance and concurrency limits also apply. An optional **Final version approval question** requires approval of the coordinator's exact final artifact before the run can complete. **Output contract (JSON Schema)** applies to that final result; leaving it empty uses the standard text contract.

Save the asset, choose **Start Run**, enter a nonempty **goal**, and start. The coordinator uses `team_manage` to create allowed instances, dispatch tasks, inspect complete results and wait. Members submit contracted outputs through `submit_task_result`. A dependency pins the producer's original artifact for `read_task_artifact`; the coordinator can also read members' complete original artifacts. Plain completion text and premature submissions cannot complete the run. Calling a tool name does not grant a member coordinator authority.

If the asset declares additional inputs, fill each field when starting. All declared values reach the coordinator from the frozen run inputs, including multiline text and literal braces. Later asset edits do not change recovered inputs. Run overviews retain member identities and relationships; open the run detail to read complete frozen configuration.

In **Runs → View Run**, inspect dynamically created tasks and **Members in this run**. **Expand execution graph (read-only)** shows dependencies and dashed **Dispatched** links from the coordinator. Dispatch links show who created a task; they are not execution prerequisites. Select a task to read its complete artifacts, frozen configuration and saved member process. Distinct instances and new runs have isolated conversations; a member continues its context across this run's tasks, including after checkpoint recovery.

Waiting releases active execution capacity, including at a concurrency limit of one. **Waiting for members** is a local wait. Pause, continue, cancel, same-Attempt recovery, explicit retry and unknown-tool decisions retain the shared workflow semantics below. Failed member tasks and unknown calls prevent success. Edited templates affect new runs; dynamic members in an existing run use the original frozen templates and settings. Required final approval uses the existing persistent human-request controls.

For the versioned autonomous path, **team sidebar / Run Monitor → Assets → Teams → New Autonomous Team / Runs → View Run**. Member communication and workflow calls to autonomous teams use the versioned paths described below.

## Fixed child calls on the canvas

Add a saved workflow or autonomous team from the canvas toolbox, then select its fixed version in the node properties. Map every declared input to a workflow input or an upstream field. The node exposes the pinned asset’s public output contract; the child workflow chooses its own result at its output node. Each call runs in a separate scope and receives only the mapped values. In the run workspace, select the call and open its child scope to inspect its tasks and results.

![Fixed child asset calls on the workflow canvas](/images/agent-teams-nested-assets-en.jpg)

## Coordinator calls to saved workflows

In **Assets → Teams → New Autonomous Team / Edit Asset → Allowed workflows**, choose **Allow a saved workflow**. Select its **Pinned workflow version**. The panel shows that version's required text inputs and public output contract. A workflow returns the task selected by its own output node. Save the team as a new version. Workflows omitted from this list remain unavailable to its coordinator.

Set **Root nesting depth limit** and **Maximum concurrent tasks**, then choose **Start Run** and enter the goal. The coordinator may call an allowed workflow with every declared input and optional visible, exact artifact references. Each call creates independent members; parent conversations and unrelated artifacts are not inherited. The coordinator waits for the call's task, then reads its complete result and immutable artifact. The chosen return task must have a validated result; a skipped return requires attention even if the workflow's other paths completed.

In **Runs → View Run**, select the coordinator and choose **View child workflow** beside its call. This route also works for coordinators inside a nested team. The call shows the returned result and the exact original child artifact reference. Choose **View child workflow** again to inspect the child tasks, or expand **Child scope** to see the saved workflow graph. **Human requests**, progress and **Saved member process** show the actual tasks and their waits, attempts and tool results.

Waiting releases the shared root capacity, so calls can finish with concurrency one. Team and workflow nesting share one bounded depth; role limits still apply. Whole-run pause, continue and cancellation reach the child workflow. A local human or failed branch leaves independent branches free to proceed. Continue keeps local waits; use the selected task's normal same-Attempt recovery or unknown-tool decision when required. Saved tool results are reused after a restart, and unknown external effects remain stopped for confirmation.

Running and recovered calls retain their original asset, role, input, resource configuration and exact result versions. Editing an allowed workflow affects new team versions and runs. Returned managed files remain fully readable, and an explicit revision can retain the exact files already handed off. Unsubmitted child files and unrelated files remain inaccessible; old artifacts, approvals and consumer input versions retain their meaning.

Version reads belong to the current selection and editor. A delayed older response cannot replace the latest version, required inputs or return contract. Changing another workflow row preserves each row's latest selection; saving waits for all current version reads. The saved version remains the one displayed after the latest choice.

## Explicit workflow data

![Selecting an upstream field in the actual workflow canvas](/images/agent-teams-explicit-data-en.jpg)

This isolated component preview uses sample configuration; it does not show a model run.

In the canvas, connect tasks to set their execution order. Select a downstream Agent and add an input: choose a workflow input or an upstream task, then select a required scalar field by name and type. Use the input alias in the task instructions, for example `{evidence}`. Optional fields, arrays and complex schemas cannot always be selected safely. For structured results, enable **Structured** on the producing Agent and define required fields and their types; otherwise the output is plain text. Select the output node and choose the task whose complete validated result and original artifact the workflow should return.

A control edge does not share the upstream conversation or tool history. A graph task can read only the fields selected from each upstream artifact. Removing a task or changing a field can invalidate references; correct the displayed diagnostic before starting, or undo the edit. Saved versions and running tasks retain their own configuration. Each graph task and loop iteration uses its own conversation; resuming the same task retains its conversation.

## Dependency canvas

Choose **New Workflow**. The canvas opens with input, Agent and output views. Enter a workflow name and choose its **Default model**. Select the input view to declare input names; the Agent uses an explicit input reference and its `{input}` placeholder. Select the Agent to edit its task, responsibility, profile, optional persona, model override and resource choices. An unselected persona means no persona, and an empty custom resource list disables that category. An Agent without its own model uses the workflow default; if each local Agent selects a model, a default is optional. Each local Agent must resolve to an allowed model before launch. Save a version, reopen it, then start it with the declared inputs. The output shows the Agent's original text result and artifact.

![Directly configured Agent in the workflow canvas](/images/agent-teams-inline-workflow-en.jpg)

The earlier **New Workflow** and **New Canvas Workflow** buttons now lead to the one **New Workflow** canvas. Role templates remain a separate asset; a new workflow does not require one. Existing fixed-role graph nodes can still be edited. Tasks sharing a member key use the same frozen role and configuration and execute serially, while each graph task has its own conversation. Distinct member keys may run in parallel within workflow and role limits.

Connect a task's right handle to another task's left handle. Choose a dependency by clicking its edge or using **Select dependency** and enable **Field condition** to compare a validated output field. Conditions use `eq`, `ne`, `gt`, `ge`, `lt` or `le` with a correctly typed value, without evaluating code. Fields must be required scalars in the source output contract; nested objects, local schema references and `allOf` may declare these fields. Ordered comparisons require numeric fields.

Use **Add input reference** to assign an alias to a workflow input or an upstream result field; insert `{alias}` into task instructions. Result references require a dependency path. The producer's original artifact version is pinned when the consumer starts, and `read_task_artifact` returns only its selected fields to the graph task. Revising the producer does not change or rerun its consumers. Save validation reports specific field paths for invalid references, conditions and unbounded back edges. The canvas supports acyclic dependencies, persistent human requests, explicit bounded loop bodies and pinned autonomous team calls.

Use the canvas controls to zoom and fit; the toolbar provides minimap visibility, undo/redo and dependency-based automatic layout. Node positions and properties are saved with the asset version. On narrow screens the toolbox and properties collapse into sections. Choose **Save New Version**, then **Start Run** from the saved asset. Saving does not execute the graph, and later edits do not change an existing run.

In **Runs**, the graph is a read-only frozen snapshot. Select a task or use **Inspect task** to view its real status, waiting/failure reason, attempt, effective configuration and exact artifact versions. An unmatched condition skips its path; a merge waits for active paths and is not blocked by skipped ones. Failures block their dependents while unrelated ready tasks continue. The root reports completion only after all required active paths produce validated results. Duplicate scheduling notifications do not dispatch a task twice.

At startup, accepted task submissions at saved safe points finish idempotently. Other abandoned task attempts become interrupted. Pending independent branches can still advance from persisted state. Select an interrupted task to continue its original attempt from a saved built-in model boundary. A new execution owner prevents the abandoned executor from publishing late results.

## Call an autonomous team from a workflow

Save a ready autonomous team first. In **Assets → Workflows → New Workflow**, choose **Call autonomous team** in the role toolbox. Select the node to choose its **Pinned child team asset** and **Pinned team version**. Declare exactly the inputs required by that version, using workflow inputs or upstream result fields with dependency paths. The output contract comes from the chosen team version. Expand the team definition above the canvas to inspect its pinned coordinator and allowed roles; collapse it to keep the parent graph compact.

Set **Root nesting depth limit** and **Maximum concurrent tasks**, then **Save New Version**. Reopening retains the selected version, references and layout. A version response only applies to the latest choice in the current editor; an unavailable choice blocks saving. A run freezes every child definition, role configuration and resource binding before dispatch. Editing the child later affects new runs. Declared multiline inputs and literal braces reach its coordinator unchanged. Required inputs must remain nonempty after resolving upstream results; invalid values show input field errors before any child scope is created, while independent branches continue.

In **Runs → View Run**, progress includes the actual child coordinator and dynamically dispatched member tasks. Expand **Child scope** to see the calling parent, exact asset version, root run identity and child task graph. Select a child task from progress, its graph or **Inspect task** to read full artifacts, attempts and process records. The parent call returns the child's original validated artifact through the declared output contract; downstream inputs pin that returned version. A parent artifact revision can retain files already handed off in its current version. Unreturned files remain inaccessible; older content, consumer inputs and approvals keep their exact versions.

Parent waits release root execution capacity, so a root limit of one still permits child tasks to complete. A child human request, provider failure or unknown external effect leaves independent parent branches ready. Handle child human requests in the root run's **Human requests** section; select the affected child task to recover, retry or resolve an unknown tool. Retrying a child task creates a new Attempt in the existing scope. Usage and limits include child tasks and prior attempts; unavailable consumption displays **Unknown**. See **Root usage and limits** below for accounting details.

Whole-run pause, continue and cancel apply to every scope and executor. A paused answer remains saved until continuation; other local waits remain. Recovery uses the original child scope, Attempt and frozen inputs. Different scopes have independent members and conversations. Ancestry grants no cross-scope message or artifact access; only declared exact input/output references cross the boundary. Coordinator calls use the explicit permissions described under Coordinator calls to saved workflows.

## Run progress and member process

Open **Runs → View Run**. The run list and history query saved runs. Expand **Run list and history** within the detail to switch runs. The detail starts with the goal, pinned asset version, completed task count and waiting reasons. Select a progress task or a task under **Needs attention** to inspect its artifacts and member process. **Expand execution graph (read-only)** retains selection of actual graph tasks and pinned inputs.

The run list loads goal previews of up to 200 characters, statuses, progress, waiting reasons, attempts and artifact entries. Complete goals, frozen input values, results and process records load for the selected run. Polling with an unchanged observation cursor returns no snapshot and does not read saved process history; changes and reconnection snapshots retain complete records.

The **Artifacts** section still reads complete results at an exact version and previews or downloads files. Expand **Saved member process** to inspect complete model responses, thinking actually returned by the provider, tool arguments and original tool results. If a tool returns partial results and then fails, each original result and the final error remain readable. Records show their task, attempt and member. Models returning no thinking have no thinking section. Long content remains scrollable without relying on a truncated summary.

Closing, refreshing or leaving the page does not cancel execution. Reopening reads a consistent persistent snapshot. Subsequent changes continue through a monotonic observation cursor; duplicate events cannot replace newer state, and an expired cursor reloads the snapshot. Completed responses and tool results remain readable after rebuilding the process. This cursor reconnects observation; it does not restore unfinished model or tool invocations.

On narrow screens, progress, waiting reasons and members stack vertically. Long names and identities wrap, and artifact and process controls remain accessible.

## Member communication

Open **Runs → View Run → select a task → Saved member process → Member messages**. Each delivery shows its sender and recipient instance, scope, source task and Attempt. Expand it to read the complete message, see whether it is pending or consumed, and inspect the receiving task, Attempt and saved context position. **Read complete version** reads the exact artifact reference in the message; editing an artifact does not change that reference.

Members use `team_communicate` to inspect permitted recipients and send questions or feedback. Communication stays within the current run and execution scope. A member can share its own outputs, frozen common inputs or exact versions already shared with it; a message cannot grant access to another run or an unshared version. The coordinator retains member creation, dispatch and whole-run completion authority. Sending a message does not start a new task or complete one.

Messages arriving during a model request wait for the next safe request boundary. Their consumption position commits together with that request's effective context. Repeating a delivery or restarting after message, tool-result or context persistence does not append another copy of a consumed message. Existing pause, cancellation, capacity and same-Attempt recovery controls apply.

Long identities and version controls wrap on narrow screens; the same records remain readable in the dark theme.

## Continue the original attempt

Open **Runs → View Run**, select the interrupted task and choose **Continue same attempt**. The page shows whether the last saved boundary is a model request, a complete model response, or a started tool. Recovery keeps the original Attempt, asset version, member conversation, effective configuration, temporary framing, multimodal content, provider thinking/signatures and execution counters. Missing resources or changed permissions block execution instead of selecting defaults.

A complete saved model response is reused with its original tool plan. If no complete response was saved, the model request may be sent again; partial streaming generation and remote billing deduplication are not guaranteed. The built-in executor supports both full schemas and two-stage parameter generation. Other executors cannot use this workflow's internal recovery.

Each tool call retains its intent, execution owner, original result and context application position. Expand a tool entry under **Saved member process** to inspect this evidence. Saved results are reused and applied once; a crash after context application continues the next model step. Partial tool output is retained separately from completion: an interrupted or failed generator with unfinished execution remains unknown, even when some output is already saved. A partially completed batch recovers each call separately: saved results are reused, unstarted calls may execute, and unknown external effects remain pending confirmation. Dependent model calls are blocked until every result is known.

## Handle an unknown tool result

In **Runs → View Run**, select the affected task and expand its unknown tool entry under **Saved member process**. Inspect the original arguments, partial output, invocation identity and any saved query evidence. Independent workflow paths continue while this task waits.

- If the adapter declared an authoritative query, an idempotency key or safe retry before execution, **Continue same attempt** can reconcile the original call. **Reconcile using declared tool capability** requests another check. An unresolved query remains unknown; tool names and timeouts never authorize replay.
- To supply a result independently verified outside AstrBot, choose **Record verified result and continue** or **Record verified failure and continue**, enter the observed evidence and complete tool result, and submit. A generic completion flag without evidence and output cannot resolve the call.
- To deliberately execute again, choose **Explicitly request a new execution** and record the basis for the decision. This may repeat an external effect. The new invocation links back to the original unknown invocation, which remains readable; the saved model plan, Attempt and frozen configuration continue.

Decisions and query outcomes survive refresh and restart. Repeating the same submitted decision cannot schedule another execution. A late result from the original execution cannot replace the selected new execution. Recovery follows the adapter's declared guarantees; AstrBot cannot promise exactly one effect for arbitrary external tools.

Result submission is an internal idempotent operation for both the default text contract and custom contracts, including stable file references. Recovery finishes the existing intent or repeats only this submission, preserving one artifact version and one completion event. Saved acceptance or rejection results retain their original state and context position. Other calls in the same batch must reach a safe result boundary before completion; unknown external effects prevent finalization. Complete temporary files for large tool output are rebuilt before continuing, so cleared temporary storage does not leave unreadable result references.

## Pause, continue, cancel and retry

Open **Agent Teams → Runs → View Run**. **Whole run controls** applies to the root run and all its existing task executors; selecting a task does not turn pause into a local wait.

- **Pause run** saves the decision and stops admission of new tasks, models and tools. Already admitted external calls may still finish; the runner saves their outcome and parks at a safe boundary. Pausing does not undo an external effect, and paused time does not consume the reply timeout.
- **Continue run** releases the whole-run pause with the original snapshot and Attempt. Local failures and unknown tool waits remain. After a process restart, paused executions remain stopped; continuing resumes their original checkpoints. **Continue same attempt** is checkpoint recovery for an interrupted task, rather than a new Attempt.
- **Cancel run** asks the existing executors to stop and permanently stops new dispatch. Confirm the decision in the dialog. Completed results, accepted result evidence, external effects and history remain readable after refresh or restart. A late executor cannot replace the cancelled state.
- Select a failed task and choose **Retry failed task**. The dialog displays its original frozen text inputs and exact artifact references. **Confirm decision** creates a new Attempt with those inputs. It does not silently select a newer artifact or create a new loop iteration. Expand **Previous attempt** to inspect the failure, input references and saved token usage; full process records retain their Attempt associations. Graph retries use the existing capacity and member exclusions.

Resolve an unknown tool outcome under **Saved member process** first. Ordinary task retry cannot bypass the tool reconciliation or evidenced human-decision protocol. Repeated identical control or retry requests do not create additional decisions or Attempts. If another control changed the revision, refresh before making a new decision.

You can save a verified result, verified failure, explicit tool retry or reconciliation decision while the whole run is paused. The page confirms that the decision is saved and waits for **Continue run**; saving the decision does not admit external work. Continuing or restarting after an accepted continuation uses the original Attempt and call evidence, subject to local waits and capacity. A normal core shutdown preserves the same paused continuation as an unexpected process stop. A saved pending decision cannot be replaced by another decision before it is consumed.

## Human requests and version approval

A running Agent may ask a dynamic choice in the same inbox. Read its context and recommendation, select an option and optionally add a note, or choose your own answer and enter text. Each question is answered separately. The selected label and note remain visible after the original tool consumes the answer.

![Dynamic choice component with sample data](/images/agent-teams-dynamic-choice-en.jpg)

This component preview uses sample data; it does not demonstrate a backend integration test.

1. Open **Assets → Workflows → New Workflow / Edit Asset**. Add **Human question** or **Version approval** from **Role toolbox**. Human nodes require no model or role. Enter the question, allowed answers (one per line, or empty for free text) and maximum answer length.
2. For approval, select the upstream **Artifact task** and exact **Artifact version**, and connect the artifact task as a dependency. Save a new asset version and start it; existing runs retain their definition.
3. Open **Runs → View Run → Human requests**. Questions, constraints, task, Attempt and approval references persist. An optional recommendation and reason come from explicitly selected upstream Agent fields. Waiting releases capacity and blocks only dependent paths. Independent branches continue; select their progress entries to read results.
4. Choose or enter an answer and select **Submit answer**. For approval, first **Preview approval version** to read the complete pinned content, then **Approve this version**. Files can be previewed. **Return for revision** requires a preview and feedback. Connect the approval node’s named Approve and Reject outlets to explicit paths; rejection does not rerun the author. Place the author and approval in a bounded loop and map the rejection feedback to the next round for rework.
5. **Export approved version** downloads JSON containing complete content and exact file references; after previewing, download files of that version. File downloads for an artifact governed by an approval node reject unapproved versions. Creating v2 never inherits v1 approval, changes its preview or automatically reruns downstream tasks.
6. Refresh or reopen after restart to continue handling requests. Repeated identical answers are consumed once; another answer cannot replace one already saved. You may answer while globally paused: **Response saved. It will be consumed after the whole run continues.** Continuing preserves unanswered requests; cancellation retains requests and response history while preventing further consumption. Ordinary task retry cannot replace human answers.

If the requested revision does not exist yet, the request retains that exact version; preview and approval explain that it is unavailable. Explicitly create the required revision on the artifact task, then preview and approve the original request. The service does not substitute v1 or the latest version, and no new Attempt is needed.

![Named approval and return outlets in the canvas](/images/agent-teams-approval-canvas-en.jpg)

![Approval request and its pinned version in an isolated component example](/images/agent-teams-approval-en.jpg)

This screenshot uses sample data in the actual component; it is not a full backend integration run.

## Single-task assets

1. Choose **New Workflow**, enter a name and select a role template and its version.
2. Enter comma-separated **Required text inputs (comma separated)**, such as `topic`. Reference an input with `{topic}` in the task; escape literal braces by doubling each brace. Only declared simple placeholders are supported.
3. Leave **Output contract (JSON Schema)** empty for a non-empty text result, or enter a JSON Schema with `type: object` for evidence, review flags or files. References must stay within this schema; remote schemas are not loaded. Choose **Save New Version**. Saving creates a definition without a member session; existing runs retain their original version.
4. Choose **Start Run**, fill every required input and optionally select exact versions under **Pinned input artifacts**. Summaries only help locate the desired content. Missing inputs or inaccessible resources produce field diagnostics without substituting defaults.
5. Open **Runs → View Run**. Use **Artifact version** to read the complete text, evidence or structured result. Long content scrolls inside the result area without truncation. Files support **Preview file** and **Download file**. Expand **Output contract (JSON Schema)**, **Result submission history**, **Frozen effective configuration** and **Saved member process** to inspect the frozen contract and field errors.
6. Choose **Create artifact revision**, edit the complete result JSON, optionally attach ready files and choose **Save artifact version**. Revisions must satisfy the original contract and create immutable versions. For a version conflict, refresh and select the latest version first. Earlier results and consumer inputs stay unchanged; no downstream task is automatically rerun.

Every run creates a new member identity and isolated conversation. The role, inputs, persona prompt and effective configuration are frozen. Tool correction continues in the same member context. Later template edits affect new runs.

The Agent must call `submit_task_result` according to the contract. The default accepts `{"text":"Complete result"}`. For custom contracts, encode the complete result object as JSON text in the single `result_json` argument, for example `{"result_json":"{\"approved\":true}"}`. The parameter description supplies the complete frozen schema, including composed schemas and local references, in both `full` and two-stage `skills_like` mode. Providers receive a usable string argument while the service validates the decoded result against the original contract. Invalid submissions return specific field paths for correction within the remaining execution budget. Ordinary replies do not complete the task. A valid submission first persists an intent. At a saved safe point, the result, artifact version, completed status and key event commit in one transaction. Repeated submissions do not duplicate completion or versions.

Autonomous team tasks can call `read_task_artifact(artifact_id, version)` to read complete pinned inputs and may supply a `file_id` to read an authorized file. Graph tasks receive only selected scalar fields and cannot read files through that reference. Actual reads are recorded durably; the task cannot read an undeclared newer version. For file output, obtain the complete content through existing authorized tools and call `store_task_file(filename, content, encoding)` first and place its stable reference in `files: [{"file_id":"..."}]` only after success. Content uses UTF-8 text or base64 bytes. Complete bytes are stored in the system-managed database before submission, without temporary absolute paths. Preview and download continue after refresh or process restart. Each file is limited to 50 MiB.

Switching or closing the page does not stop core execution. Refreshing a URL with `workflow_run` reads that run again; the run list also reopens past executions. A core restart automatically finishes accepted result intents at persisted safe points, idempotently. Other unfinished executions become **Interrupted**. Choose **Continue same attempt** for supported model boundaries; tools with uncertain results remain pending.

## Configure a bounded review and revision loop

![Bounded loop configuration with sample data](/images/agent-teams-bounded-loops-en.jpg)

The screenshot shows the actual editor component with sample data.

Choose the exit and feedback fields from the required scalar outputs of each task. The summary shows each previous-round result and its next-round input; expanding an iteration shows its actual feedback inputs and pinned result.

Open **Agent Teams → Assets → Workflows → New Workflow / Edit Asset**. Configure the writer and independent review tasks, an acyclic dependency from writer to reviewer, and the review output contract with a required Boolean `approved` field. Use **Add bounded loop** to choose the explicit body, exit result task, field comparison (`approved eq true`) and maximum iterations (1–100, within the effective member limit). Arbitrary expressions, graph back edges, overlapping bodies and a body that leaves and reenters its group are rejected before execution.

For the writer, declare first-round draft and feedback inputs. Under the loop, **Add feedback input** explicitly maps the previous writer's text to the next writer's draft and the previous review's text to its feedback. Ordinary dependency inputs within each round read that round's output. Collapse or expand the body to inspect the group on the actual canvas. Saving appends an asset version; existing runs retain their definition and configuration.

Each iteration creates new Task instances with new Attempts, frozen input references and separate model/tool records. Its revised result has its own immutable artifact identity: compare both `artifact_id` and `version`, not the version number alone. A retry keeps the Task and its original round inputs but creates a new Attempt. Recovery continues the original Attempt from its saved checkpoint. Earlier content and review/approval references remain unchanged.

In **Runs → View Run → Loop iterations**, expand a round to inspect inputs, results, the exact exit result reference and task model-call counts. Unknown usage is labelled unknown. Click a task to open its complete saved process, artifacts and controls; expand its round graph for the real task states. Consumers of an executed body wait for a matching validated exit. An unmatched final round shows **Iteration limit reached** and the run needs attention without a final successful result. Independent paths still run while a body waits. Whole-run pause or cancellation also applies across round boundaries; restarting does not create duplicate rounds or repeat saved effects.

If conditions skip the entire first-round body, the loop and round show **Skipped (unselected path)**. They produce no exit artifact or additional round. Merges ignore that unselected path and use their active branch inputs. A loop that already executed but has a skipped exit, failed work or an exhausted limit still blocks its dependent consumers; skipped task inputs never produce substitute results.

Optional conditional body paths may be skipped when the exit still produces a passing validated result. If a rejected exit needs feedback from a skipped task, the loop requires attention because that round has no result to pin.

A loop runs independently without a human node. To confirm a report, add a **Version approval** task after its review dependency and select the writer artifact task. The todo pins the passing iteration's concrete artifact. Export uses that approved reference. New manual revisions never automatically reschedule consumers or inherit old approvals.

## Inspect actual model context

Open **Runs → View Run**, select a task, and expand **Actual model context** on a model delivery. The message roles and text, model and tool schemas reflect the final request observed immediately before the built-in provider call. Parameter and repair calls have their own receipt and context. Expand the assembly sources and plugin injection trail to see the persona, role, task, explicit input framing, skills and retrieved knowledge that were actually assembled. These sources precede plugin changes and do not guarantee that every fragment survived replacement or trimming.

Records have an 8192-character content budget; the panel identifies truncation and the original record length. Binary media is omitted, remote media URLs are stripped of credentials and query/fragment, and local paths without an authorized file reference are omitted. An absent persona is shown as absent; older runs and remote executors may not provide context. Context follows the run owner's existing permissions: permission to answer a human request does not grant access to the owner's process. Reading the process does not inject it into downstream agents.

![Actual model context with assembly sources](/images/agent-teams-actual-context-en.jpg)

## Executor readiness and remote recovery

Open **Assets → Teams / Workflows → Start Run**. The dialog checks the saved version's dependencies and executor capabilities before enabling **Start Run**. **Check readiness** refreshes the same check used by launch. Blocked requirements show field paths. Roles, canvas workflows and autonomous teams share these rules; coordinators need local task/team tools.

| Executor | Integrated recovery boundary |
| --- | --- |
| Built-in | Complete model/context and per-tool invocation checkpoints. |
| Dify workflow | Original `workflow_run_id` and configured workflow outputs. |
| Coze with saved history enabled | Original `chat_id` plus `conversation_id` and per-chat answers. Disabling saved history removes query capability. |
| Current DeerFlow API | Original thread/run IDs, exact status and paginated run-owned messages. Missing routes or output require confirmation. |
| Dify chat/agent/chatflow, DashScope Application | External call boundary. Chatflow status alone cannot recover its canonical moderated chat answer. |

In **Runs → View Run**, select the affected task. Its executor panel shows the boundary and original remote identity. **Query original remote run** keeps the Attempt and queries that identity without creating another operation. Saved complete responses are reused. Missing identity, unreachable queries, mismatched identity, an active/unknown status or missing output leave the result unconfirmed. Inspect the original service before deliberately choosing a new Attempt; failed queries do not prove the operation never happened.

Dify workflows submit structured objects from the configured output key through the normal contract validator. Plain remote text and displayed JSON do not complete tasks. Coze and DeerFlow answers are saved as process output and remain **Awaiting result** when explicit result submission is missing; a required `task_result` capability rejects those executors. Remote queries do not restore remote internal tools. Uninstrumented plugin/tool-internal calls have only boundaries actually saved by AstrBot. Thinking and signatures are retained when supplied; models without thinking work normally.

Dify Agent's original `agent_thought` events appear under **Saved member process → Remote process event**, with the provider's thought ID, thinking, tool input and observation. Workflow and chatflow process events retain their actual node execution IDs, inputs, process data and outputs, including supplied reasoning and agent logs. Later observations for the same thought remain separate snapshots; repeated snapshots and recovery do not duplicate records, while identical unsequenced reasoning deltas retain their arrival order. Repeated execution of one node remains distinguishable by its original execution and iteration IDs. These are observations of remote work. A storage failure blocks the task before accepting a complete answer; recovery replays saved observations before querying the original run. Team DashScope calls also stop for confirmation after an ambiguous connection loss or redirect instead of resending a creation request.

## Root usage and limits

Open **Assets → Teams / Workflows → Edit Asset → Scope limits** to configure total call steps, subtree member instances, rounds per loop, token allowance and output tokens per model call. Single tasks, canvas workflows and autonomous teams share this form. Blank fields add no ceiling. Save a new version and start a run; existing runs retain their frozen limits. Existing concurrency, depth and local team instance limits still apply, together with ancestor scope limits.

Each admitted model, tool or status-query delivery uses one call step, including the coordinator, members, parameter generation, compression, retries and nested calls. Human, child and reservation waits release active capacity. Entering a reservation wait automatically wakes ready independent work, subject to root and ancestor scope limits. A parked Agent turn retains its member context; another task using that member waits until the turn exits. Recovery continues the original Attempt; an explicit retry creates a new Attempt without resetting consumption or allowances. The iteration ceiling applies to each loop and cannot be bypassed by recovery or retry.

Open **Runs → View Run → Usage and limits** for the root total. Select a task or child scope for its detail. Expand **Call details** to trace the scope, Task, Attempt, Invocation and actual delivery receipt. Parent and child views refer to the same receipts and do not double count. Reading an input artifact from another root does not transfer that run's historical consumption.

Token totals separate the provider-reported portion, estimates, in-flight reservations and calls without confirmed usage. Missing reports remain unknown. Tools without token reports also remain unknown; this does not mean they have no other charges. The token allowance governs estimable model requests connected to this protocol. Uninstrumented charges inside plugins or tools cannot be hard limited. If no output ceiling is configured, the existing request ceiling is used, or 2048 when the request has none. Reservations constrain concurrent admission, but inaccurate estimates or provider output enforcement can still cause reported consumption to exceed the allowance. There is no unconditional zero-overspend guarantee.

Runs created before usage tracking retain their original process records. The panel separately shows saved historical model responses and historical uncertainty, even after recovery records new usage. Historical responses and checkpoint steps are not admission receipts; reported totals cover the metered portion. The total number of calls with unknown consumption remains unknown for these runs.

An exhausted limit stops new calls and exposes the limit, value and affected task. Saved results and effects remain traceable. A validated result and fully applied tool batch can finish even when the allowance rejects a subsequent narration request. Interrupted consumption retains conservative uncertainty; late reports update the original receipt without taking execution ownership. Allowance held only by other in-flight calls waits for release without occupying active capacity.

## Deliver a reviewed report on two instances

1. In **Assets → Workflows**, prepare a report workflow with a research child call, a bounded writer/reviewer loop and a final **Version approval** task. The research team can call a saved graph with two independent researchers followed by an evidence editor. Configure the allowed workflows on its coordinator. Pin every role and child asset version, declare `topic` and `feedback` inputs, and explicitly map the previous draft and review into the next loop round.
2. Start the report from its asset card. In **Runs → View Run**, inspect the research child scopes and the writer/reviewer rounds. A failed first review must lead to a revised draft and another review; an exhausted loop needs attention rather than reporting success. The final human request identifies the actual artifact ID and version. A second loop round can create a different artifact at version 1: the round number is not the artifact version.
3. Choose **Preview approval version** and inspect its complete text and file. Approve that specific version, then use **Export approved version** and **Download file · filename** in the same human request. The file preview dialog is for reading; close it to return to the download button. The exported artifact and Markdown must refer to the approved object; approving an earlier draft does not approve another revision.
4. Export the root asset with **Export asset JSON**. On an isolated instance B with different local resource IDs, use **Import asset package → Validate and preview**. Choose a local resource for every source requirement in the dependency form, including matching IDs. **Confirm installation** installs the pinned nested closure in one transaction; it does not start the report. Start the imported root with the same inputs and handle its own human approval. Closing its browser page leaves the server run and its pending request intact; reopen the run link to continue.
5. To use the same asset from ordinary chat, open **Chat access** on the configured root and grant the intended platform sender/source. In that source, `/asset whoami` shows the identity to grant. Use the invocation syntax shown by the card, supply required inputs after any clarification, and follow the accepted run link. Confirm the browser challenge from that same source using `/asset connect <challenge>`. The source identity can read and answer its own human requests and receive its final result without gaining the owner's process or configuration access.

![Actual report approval bound to its exact artifact and version](/images/agent-teams-report-t25-en.jpg)

The report must use real provider/tool resources in your installation. The documented QA screenshots use isolated instances and controlled external model responses; their sample report is test content. Recovery and unknown-effect handling retain the executor limits described above.

## Previous and new entry points

| Previous entry | Current purpose |
| --- | --- |
| Agent Teams → Members, Workflow Editor, Run Monitor | Assets → Teams / Workflows / Role templates; Runs → View Run / History. Migration and legacy run checks are removed. |
| Previous History | Runs → History → asset run details. |
| Saving a reusable single-task workflow | Agent Teams → Assets → Workflows → New Workflow; independent of a legacy team. |
| Editing a reusable dependency workflow | Agent Teams → Assets → Workflows → New Workflow / Edit Asset; the previous execution editor is retired. |
| Starting and inspecting a new workflow run | Assets → Workflows → Start Run, then Runs → View Run → Execution progress to select a task; the Inspect task selector remains available. |
| Default graph and reply JSON within the detail | Progress appears first. The graph moves to Expand execution graph (read-only), and replies move to expandable Saved member process records. |
| Editing a template and inspecting run configuration | Assets → Teams / Workflows appends versions; Runs displays frozen snapshots. |
| An interrupted task only exposed process records | Runs → View Run → select task → Continue same attempt; saved tool results are reused, and unknown effects require confirmation. |
| Continue same attempt for every executor | Query original remote run for queryable remote executors; Continue same attempt remains the built-in checkpoint action. |
| Dify Agent runs only displayed the final answer | Saved member process → Remote process event preserves original thought and tool observation snapshots. |
| Unknown tool effects only displayed a waiting reason | Runs → View Run → select task → Saved member process → expand the unknown call → reconcile, record a verified outcome or explicitly request a new execution. |
| Run details only showed checkpoint recovery | Runs → View Run → Whole run controls; select the failed task → Retry failed task. |
| Usage only showed checkpoint model/step counters | Assets → Teams / Workflows → Edit Asset → Scope limits; Runs → View Run → Usage and limits → Call details. |
| Member communication appeared only in tool results | Runs → View Run → select task → Saved member process → Member messages; expand a reference to read its complete version. |
| Coordinators could only dispatch member tasks | Assets → Teams → New Autonomous Team / Edit Asset → Allowed workflows; Runs → View Run → select coordinator → View child workflow. |
| Revision used only ordinary task dependencies | Assets → Workflows → Edit Asset → Add bounded loop; Runs → View Run → Loop iterations. |
| New workflows had no persistent human entry | Assets → Workflows → Edit Asset → Human question / Version approval; Runs → View Run → Human requests. |

Single-task assets remain compatible. Dependency assets provide acyclic scheduling, field conditions and persistent human requests through the same task result protocol. Bounded revision loops and autonomous teams share the same scheduler. Unknown tool decisions are available inside the selected task's process details.

## Human wait deadlines

Human questions and approvals wait indefinitely by default. Set a deadline on the human node and choose whether to keep waiting or automatically use the valid recommendation shown when the request was created. For dynamic choices, configure the asking role or Agent’s human wait policy. Template-based Agents can inherit the fixed template policy or override it, including resetting it to indefinite waiting. This setting is independent of ordinary chat timeouts.

The todo displays the persisted deadline and decision source. Missing or invalid recommendations, or unavailable evidence, leave the request waiting for a person. No new model decision is requested at the deadline.
