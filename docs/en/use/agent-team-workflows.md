# Agent Teams asset and run workspace

Open **More Features → Agent Teams → Assets**. **Teams** contains versioned autonomous teams, **Workflows** contains single-task and canvas workflows, and **Role templates** configures reusable responsibilities and local resources. Save a ready role before creating an asset. Import, export, chat access, editing and launch actions remain on the asset cards. After installation, the workspace opens the Teams or Workflows directory for the imported entry.

![The actual asset workspace](/images/agent-workbench-assets-en.jpg)

## Navigation and previous entry points

| Previous entry | Current entry |
| --- | --- |
| Autonomous teams within Workflow Assets | Assets → Teams |
| Single-task and canvas flows within Workflow Assets | Assets → Workflows |
| Role templates | Assets → Role templates |
| Independent Runs | Runs → All runs, In progress, Needs attention, History |
| Previous team sidebar, Workflow Editor, Members, Run Monitor, History | Expand Previous team workspace at the bottom → original operation |
| Previous team/member and workflow definitions | Migrate previous definitions → Assets → Teams / Workflows / Role templates |

The previous team workspace remains separate from versioned assets. Expand it, select a team and use the original tabs for creation, member configuration, save-and-run and history inspection. Definition migration is an explicit action described below; it does not take over previous runs or change the default execution entry.

## Preview and migrate previous definitions

![Actual migration preview with pending definitions and a previous active run](/images/agent-teams-migration-en.png)

1. Open **More Features → Agent Teams → Migrate previous definitions**, select a previous team and choose **Preview migration**. The preview lists its team and saved workflows, generated roles, final effective configuration, inheritance sources, local bindings and old-to-new identities. Expand **Review definitions, layout and identity mapping** to inspect the complete proposal. No assets or member sessions are created by previewing.
2. Review the listed previous active runs. Finish or stop them in **Previous team workspace → Run Monitor** when appropriate. Migration only copies definitions; it neither stops those runs nor continues them in the new engine. Previous history remains available through **Previous team workspace → History**.
3. Resolve or retain the reported pending items. Member settings are applied before node overrides; Custom member framing remains separate from persona instructions, which the new run freezes once. Existing session model selections precede profile defaults; inherited capability lists and numeric limits are made explicit. An unavailable model stays bound to its original identity. No replacement model, profile, tool or knowledge base is chosen. Missing or ambiguous profiles/personas also require explicit role review because their inherited configuration cannot be reconstructed safely.
4. Mappable links, their predecessor result inputs, coordinates, output contracts and finite declarative loops are retained. Declarative inline subflows, or saved workflow references within the same team, can be flattened when they share root inputs and have one terminal result. Nested call overrides, extra input contracts/limits, ambiguous results, arbitrary old conditions, unbounded cycles and missing references stay pending, with their original graph and edges retained. The previous editor only writes plain DAGs; recognized stored extensions do not imply that the previous engine supported every nested shape.
5. The previous coordinator's round protocol and controls cannot be copied into the new coordinator. The preview reports these fields, including inherited defaults, for review. Customized workflow failure, timeout and truncation policies that cannot be represented are also reported. Choose **Save pending definitions and bindings; keep unresolved items blocked from execution** to retain such definitions, then **Confirm migration**. Otherwise confirmation is available only for a ready proposal. Confirmation saves all roles, asset versions and the identity receipt atomically; it does not start a run.
6. In **Assets → Role templates**, restore missing resources or edit the migrated role's bindings and configuration. Review any retained migration issues and check **I have reviewed these issues and configured this definition for the new engine** before **Save new version**. In **Assets → Teams / Workflows → Edit Asset**, select the intended fixed role versions, correct the graph/limits and acknowledge its migration issues before saving a new asset version. Removing a cycle or choosing different members must be deliberate; migration never deletes dependencies. Launch readiness still validates current resources and capabilities.
7. Choose **Start Run** on the configured asset and supply its declared inputs (`input` is the previous workflow input; autonomous teams use `goal`). The new run freezes the selected version and creates fresh member conversations. Previous long-term sessions and unfinished runs are never attached. Later template edits affect future runs only.

Repeated confirmation reuses the saved migration receipt and its original fixed versions, including after a process restart. It does not duplicate assets or overwrite subsequent asset edits. Changes to a previous definition before first confirmation require a fresh preview. Once that source has been migrated, edit its new versioned assets; changing the previous source does not silently remigrate it over the existing receipt. A failed transaction or interrupted uncommitted transaction leaves no executable partial migration. This migration entry does not remove the previous engine or switch its default entry.

**Runs → In progress** includes unfinished runs and runs requiring intervention. **Needs attention** includes human waits, blocks, failures, interruptions and pauses. **History** contains completed, cancelled, skipped, failed and interrupted records, so a recoverable failure can also appear under Needs attention. **All runs** returns to the full recent list; the existing service still returns its latest 50 records.

Choose **View Run** to see the goal, task progress and waiting reasons, then handle **Human requests** or select a task to read its artifacts. Expand usage details and the execution graph as needed. The graph is labelled **Run Snapshot (read only)** and has no definition toolbox or property editor. Editing through **Assets** affects future runs. Role cards identify a **Role template**, while runtime details identify a **Run member**.

![Progress, human waits and a fixed snapshot in the real WebUI](/images/agent-workbench-run-en.jpg)

On narrow screens, expand the collapsed toolbox or properties heading when needed; selecting a node exposes its properties. Toolbars wrap and dialog actions stay in their action area. Tasks, requests and artifacts stack vertically, and long names wrap. Status uses text and icons as well as AstrBot theme colors. The workspace and its dialogs disable transitions and animations when the system requests reduced motion. Import bindings and nested panels follow the same spacing, rounding and narrow-screen rules.

![The actual canvas in the dark theme](/images/agent-workbench-dark-en.jpg)

![Human requests on a narrow screen](/images/agent-workbench-mobile-en.jpg)

## Move a complete nested asset to another instance

![Actual nested-package and local-dependency preview](/images/agent-workbench-import-en.jpg)

In instance A, choose **Assets → Teams / Workflows → Export asset JSON** on a single-task workflow, canvas workflow or autonomous team. Review the actual included prompts and complete JSON, then choose **Download JSON**. Resolve missing source resources before exporting. The versioned `astrbot.workflow` package includes its identity, fixed role versions, contracts, conditions, bounded loops, layout and logical dependencies. Format 2 includes the complete closure of exact team and workflow versions; a child never relies on an asset that exists only on the source machine. Existing format 1 single-layer packages remain supported. Effective inherited prompts and resource allowlists are made explicit; `prompt_source` identifies whether the included prompt came from a role, persona, profile or task override. Knowledge-base content, credentials, local binding IDs, conversations and run history are excluded.

In instance B, choose **Assets → Teams / Workflows → Import asset package**. Select the JSON file or paste its complete contents, then choose **Validate and preview**. Format, contracts, all pinned references, return tasks, depth, cycles and loop limits are validated before any installation. Unknown format versions and script fields are rejected. **Included exact asset versions** lists each definition and whether confirmation will install it, reuse identical content or add a new version.

Review the included prompts and expand **Review complete definition, contracts, layout and dependencies**. Each role groups its local profile and resource slots, retaining original logical purposes and resource descriptions. Review the required tool input schema and the selected candidate's description and schema; a declared tool input schema must match. Model automatic matching requires the same declared adapter, model and source fingerprint, required capabilities and a unique candidate; profiles must permit a compatible model and satisfy the runner requirements. Matching display names never establishes equivalence. Resources without source evidence, including tools, skills and knowledge bases, require deliberate local review and selection. Local profile and persona restrictions still apply; the package grants no access. **Configure local resources** opens the existing configuration area when a candidate is missing; install required plugins or configure services yourself.

Readiness and every new launch recheck all declared package dependencies, including base resources that a task override does not use. Rebuilding the services retains these requirements. Running tasks also recheck the original package requirements and their frozen bindings before further external work, including after a human wait, pause or checkpoint recovery. If a bound resource keeps its ID but changes to an incompatible model source or tool input schema, execution is blocked. Restore the original compatible resource before continuing or retrying the affected task. Editing the asset or rebinding its current installation only affects new runs; it does not replace an existing run's frozen bindings or rewrite its snapshot.

An independently exported child keeps its role defaults and explicit tool, skill and knowledge-base requirements. Task-only requirements belong to the exact asset that uses them. Adding a tool override to a new parent version therefore reuses unchanged children and retains their compatible saved bindings. Different effective requirements use separate local installations, including coordinator capabilities. Explicit unused requirements and legacy declarations still require compatible, authorized bindings; they do not expand executable allowlists. If tasks supply a missing role prompt, each included task retains its own complete instructions.

Dependency review also checks reused installations. An unavailable resource can be replaced automatically when its verified source has one compatible match. Compatible saved choices are retained; if reused installations have different compatible choices, select and confirm the shared binding before installation. Missing or ambiguous choices remain unready. Saving pending configuration keeps existing bindings for unresolved fields, including when the dialog sends its preview proposals. New bindings affect future runs; existing runs keep their frozen choices. A child reached through several paths must satisfy the depth limits of every path.

If a package pins another version of its own entry asset, installation keeps that child reference exact and makes the selected entry the default local version for editing and new runs.

Dependency selections stay with the reviewed package until you edit or replace its JSON. Preview checks loop iterations and distinct member counts against the effective role settings, including inherited values from the selected local profile. Choose a compatible profile or correct the source asset when a limit blocks installation.

Every included member's own depth limit is checked at its reference depth, including terminal members and shared children reached by different paths. Nullable depth limits inherit the selected local profile. The coordinator's limit also bounds its declared child calls; other members retain their own placement limits. Readiness and new launches of imported entries validate the current definition's full closure, including later local versions. Ordinary local entries retain their runtime call-boundary checks. A parent saved pending configuration remains blocked until its closure is ready; an independently valid child can still run from its own entry. A pending upgrade or independent package sharing a role keeps its graph limits separate from an unchanged valid version. Each launch checks its selected pinned closure and the original portable resource requirements.

An incompatibility found after a model response or at a tool/usage boundary remains a resource block with details of the affected binding. A genuine checkpoint or accounting storage failure still stops dependent work. After restoring the original resource, **Continue same attempt** reuses a complete saved response, including a streamed OpenAI response. **Retry failed task** creates a new Attempt and retains the blocked history.

With all dependencies ready, choose **Confirm installation** once. Every included definition and reviewed local binding is saved in one transaction; validation or storage failure leaves no executable partial installation. Then use the asset's normal **Start Run** action and provide its declared inputs. If dependencies are missing or ambiguous, **Save pending configuration** retains the whole closure, and affected entries cannot start. Import the same JSON again to fill its local bindings; identical versions are reused. An upgrade appends a selectable local version under the same asset identity and remaps each child to its exact installed version. Conflicting content under the same asset or role identity and version is rejected. Existing runs, children, artifacts and approvals keep their original snapshots, including after restart. Editing a local role version does not remove the original imported compatibility requirements or current permission checks.

The existing **Assets → Teams / Workflows → Edit Asset / Start Run** entries remain. **Export workflow JSON / Import workflow package → Export asset JSON / Import asset package** now covers nested teams and workflows from the same workspace. These controls use the shared asset, readiness and run services. Importing does not execute package scripts, install plugins or deploy models and services. Historical HTML demo exports are design examples, with no production format compatibility promise.

## Explicit invocation from ordinary chat

In the intended platform conversation, send `/asset whoami`. It returns the actual platform instance ID, sender ID, source conversation (UMO) and source profile ID. A display name or a sender ID that matches a Dashboard account does not link those identities.

An asset owner opens **Agent Teams → Assets** and chooses **Teams** for an autonomous team or **Workflows** for a single-task or canvas workflow. On the asset card, open **Chat access**, enter the four values above and choose **Open this version**. The grant pins the displayed asset version and allows its final result to be delivered to that exact conversation. A group conversation grant therefore explicitly allows the result to be visible in that group. Every reachable role, allowed team member and nested workflow must use the source profile; existing model/persona pools and tool, skill and knowledge-base restrictions still apply. Importing a package does not open it to chat callers. Use **Revoke access** to stop subsequent external work and result delivery; already delivered work remains recorded.

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

Choose **Assets → Teams → New Autonomous Team**. Enter the **Team name**, choose the **Coordinator role** and its pinned version, then select **Allowed role templates** and pin each version. Set **Maximum members** (including the coordinator), **Maximum concurrent tasks** and **Maximum member tasks**. Role instance and concurrency limits also apply. An optional **Final version approval question** requires approval of the coordinator's exact final artifact before the run can complete. **Output contract (JSON Schema)** applies to that final result; leaving it empty uses the standard text contract.

Save the asset, choose **Start Run**, enter a nonempty **goal**, and start. The coordinator uses `team_manage` to create allowed instances, dispatch tasks, inspect complete results and wait. Members submit contracted outputs through `submit_task_result`. A dependency pins the producer's original artifact for `read_task_artifact`; the coordinator can also read members' complete original artifacts. Plain completion text and premature submissions cannot complete the run. Calling a tool name does not grant a member coordinator authority.

If the asset declares additional inputs, fill each field when starting. All declared values reach the coordinator from the frozen run inputs, including multiline text and literal braces. Later asset edits do not change recovered inputs. Run overviews retain member identities and relationships; open the run detail to read complete frozen configuration.

In **Runs → View Run**, inspect dynamically created tasks and **Members in this run**. **Expand execution graph (read-only)** shows dependencies and dashed **Dispatched** links from the coordinator. Dispatch links show who created a task; they are not execution prerequisites. Select a task to read its complete artifacts, frozen configuration and saved member process. Distinct instances and new runs have isolated conversations; a member continues its context across this run's tasks, including after checkpoint recovery.

Waiting releases active execution capacity, including at a concurrency limit of one. **Waiting for members** is a local wait. Pause, continue, cancel, same-Attempt recovery, explicit retry and unknown-tool decisions retain the shared workflow semantics below. Failed member tasks and unknown calls prevent success. Edited templates affect new runs; dynamic members in an existing run use the original frozen templates and settings. Required final approval uses the existing persistent human-request controls.

For the versioned autonomous path, **team sidebar / Run Monitor → Assets → Teams → New Autonomous Team / Runs → View Run**. Older records and entry points remain separate until migration. Member communication and workflow calls to autonomous teams use the versioned paths described below.

## Coordinator calls to saved workflows

In **Assets → Teams → New Autonomous Team / Edit Asset → Allowed workflows**, choose **Allow a saved workflow**. Select its **Pinned workflow version** and **Return task**. The panel shows that version's required text inputs and the return task's output contract. Single-task workflows return their **result** task; canvas workflows return the task you select. Save the team as a new version. Workflows omitted from this list remain unavailable to its coordinator.

Set **Root nesting depth limit** and **Maximum concurrent tasks**, then choose **Start Run** and enter the goal. The coordinator may call an allowed workflow with every declared input and optional visible, exact artifact references. Each call creates independent members; parent conversations and unrelated artifacts are not inherited. The coordinator waits for the call's task, then reads its complete result and immutable artifact. The chosen return task must have a validated result; a skipped return requires attention even if the workflow's other paths completed.

In **Runs → View Run**, select the coordinator and choose **View child workflow** beside its call. This route also works for coordinators inside a nested team. The call shows the returned result, its own artifact and the exact child return reference. Choose **View child workflow** again to inspect the child tasks, or expand **Child scope** to see the saved workflow graph. **Human requests**, progress and **Saved member process** show the actual tasks and their waits, attempts and tool results.

Waiting releases the shared root capacity, so calls can finish with concurrency one. Team and workflow nesting share one bounded depth; role limits still apply. Whole-run pause, continue and cancellation reach the child workflow. A local human or failed branch leaves independent branches free to proceed. Continue keeps local waits; use the selected task's normal same-Attempt recovery or unknown-tool decision when required. Saved tool results are reused after a restart, and unknown external effects remain stopped for confirmation.

Running and recovered calls retain their original asset, role, input, resource configuration and exact result versions. Editing an allowed workflow affects new team versions and runs. Returned managed files remain fully readable, and an explicit revision can retain the exact files already handed off. Unsubmitted child files and unrelated files remain inaccessible; old artifacts, approvals and consumer input versions retain their meaning.

Version reads belong to the current selection and editor. A delayed older response cannot replace the latest version, required inputs or return contract. Changing another workflow row preserves each row's latest selection; saving waits for all current version reads. The saved version remains the one displayed after the latest choice.

## Dependency canvas

Choose **New Canvas Workflow**. Enter the workflow name, required text inputs and maximum concurrent tasks, then drag a role from the toolbox onto the canvas (or click it). Select a task to edit its name, instructions, pinned role version, member instance key, input references and output contract. Tasks sharing a member key use the same frozen role and configuration and advance one conversation serially; distinct member keys create isolated contexts and may run in parallel within the workflow and role limits.

Connect a task's right handle to another task's left handle. Choose a dependency by clicking its edge or using **Select dependency** and enable **Field condition** to compare a validated output field. Conditions use `eq`, `ne`, `gt`, `ge`, `lt` or `le` with a correctly typed value, without evaluating code. Fields must be required scalars in the source output contract; nested objects, local schema references and `allOf` may declare these fields. Ordered comparisons require numeric fields.

Use **Add input reference** to assign an alias to a workflow input or an upstream result field; insert `{alias}` into task instructions. Result references require a dependency path. The producer's original artifact version is pinned when the consumer starts, and its complete content remains available through `read_task_artifact`. Revising the producer does not change or rerun its consumers. Save validation reports specific field paths for invalid references, conditions and unbounded back edges. The canvas supports acyclic dependencies, persistent human requests, explicit bounded loop bodies and pinned autonomous team calls.

Use the canvas controls to zoom and fit; the toolbar provides minimap visibility, undo/redo and dependency-based automatic layout. Node positions and properties are saved with the asset version. On narrow screens the toolbox and properties collapse into sections. Choose **Save New Version**, then **Start Run** from the saved asset. Saving does not execute the graph, and later edits do not change an existing run.

In **Runs**, the graph is a read-only frozen snapshot. Select a task or use **Inspect task** to view its real status, waiting/failure reason, attempt, effective configuration and exact artifact versions. An unmatched condition skips its path; a merge waits for active paths and is not blocked by skipped ones. Failures block their dependents while unrelated ready tasks continue. The root reports completion only after all required active paths produce validated results. Duplicate scheduling notifications do not dispatch a task twice.

At startup, accepted task submissions at saved safe points finish idempotently. Other abandoned task attempts become interrupted. Pending independent branches can still advance from persisted state. Select an interrupted task to continue its original attempt from a saved built-in model boundary. A new execution owner prevents the abandoned executor from publishing late results.

## Call an autonomous team from a workflow

Save a ready autonomous team first. In **Assets → Workflows → New Canvas Workflow**, choose **Call autonomous team** in the role toolbox. Select the node to choose its **Pinned child team asset** and **Pinned team version**. Declare exactly the inputs required by that version, using workflow inputs or upstream result fields with dependency paths. The output contract comes from the chosen team version. Expand the team definition above the canvas to inspect its pinned coordinator and allowed roles; collapse it to keep the parent graph compact.

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

1. Open **Assets → Workflows → New Canvas Workflow / Edit Asset**. Add **Human question** or **Version approval** from **Role toolbox**. Human nodes require no model or role. Enter the question, allowed answers (one per line, or empty for free text) and maximum answer length.
2. For approval, select the upstream **Artifact task** and exact **Artifact version**, and connect the artifact task as a dependency. Save a new asset version and start it; existing runs retain their definition.
3. Open **Runs → View Run → Human requests**. Questions, constraints, task, Attempt and approval references persist. Waiting releases capacity and blocks only dependent paths. Independent branches continue; select their progress entries to read results.
4. Choose or enter an answer and select **Submit answer**. For approval, first **Preview approval version** to read the complete pinned content, then **Approve this version**. Files can be previewed. **Reject with feedback** requires feedback, preserves rejection evidence and blocks the affected path. This does not create a revision loop.
5. **Export approved version** downloads JSON containing complete content and exact file references; after previewing, download files of that version. File downloads for an artifact governed by an approval node reject unapproved versions. Creating v2 never inherits v1 approval, changes its preview or automatically reruns downstream tasks.
6. Refresh or reopen after restart to continue handling requests. Repeated identical answers are consumed once; another answer cannot replace one already saved. You may answer while globally paused: **Response saved. It will be consumed after the whole run continues.** Continuing preserves unanswered requests; cancellation retains requests and response history while preventing further consumption. Ordinary task retry cannot replace human answers.

If the requested revision does not exist yet, the request retains that exact version; preview and approval explain that it is unavailable. Explicitly create the required revision on the artifact task, then preview and approve the original request. The service does not substitute v1 or the latest version, and no new Attempt is needed.

## Single-task assets

1. Choose **New Workflow**, enter a name and select a role template and its version.
2. Enter comma-separated **Required text inputs (comma separated)**, such as `topic`. Reference an input with `{topic}` in the task; escape literal braces by doubling each brace. Only declared simple placeholders are supported.
3. Leave **Output contract (JSON Schema)** empty for a non-empty text result, or enter a JSON Schema with `type: object` for evidence, review flags or files. References must stay within this schema; remote schemas are not loaded. Choose **Save New Version**. Saving creates a definition without a member session; existing runs retain their original version.
4. Choose **Start Run**, fill every required input and optionally select exact versions under **Pinned input artifacts**. Summaries only help locate the desired content. Missing inputs or inaccessible resources produce field diagnostics without substituting defaults.
5. Open **Runs → View Run**. Use **Artifact version** to read the complete text, evidence or structured result. Long content scrolls inside the result area without truncation. Files support **Preview file** and **Download file**. Expand **Output contract (JSON Schema)**, **Result submission history**, **Frozen effective configuration** and **Saved member process** to inspect the frozen contract and field errors.
6. Choose **Create artifact revision**, edit the complete result JSON, optionally attach ready files and choose **Save artifact version**. Revisions must satisfy the original contract and create immutable versions. For a version conflict, refresh and select the latest version first. Earlier results and consumer inputs stay unchanged; no downstream task is automatically rerun.

Every run creates a new member identity and isolated conversation. The role, inputs, persona prompt and effective configuration are frozen. Tool correction continues in the same member context. Later template edits affect new runs.

The Agent must call `submit_task_result` according to the contract. The default accepts `{"text":"Complete result"}`. For custom contracts, encode the complete result object as JSON text in the single `result_json` argument, for example `{"result_json":"{\"approved\":true}"}`. The parameter description supplies the complete frozen schema, including composed schemas and local references, in both `full` and two-stage `skills_like` mode. Providers receive a usable string argument while the service validates the decoded result against the original contract. Invalid submissions return specific field paths for correction within the remaining execution budget. Ordinary replies do not complete the task. A valid submission first persists an intent. At a saved safe point, the result, artifact version, completed status and key event commit in one transaction. Repeated submissions do not duplicate completion or versions.

Tasks call `read_task_artifact(artifact_id, version)` to read complete pinned inputs. Supplying a `file_id` from that version reads the complete file. Actual reads are recorded durably; the task cannot read an undeclared newer version. For file output, obtain the complete content through existing authorized tools and call `store_task_file(filename, content, encoding)` first and place its stable reference in `files: [{"file_id":"..."}]` only after success. Content uses UTF-8 text or base64 bytes. Complete bytes are stored in the system-managed database before submission, without temporary absolute paths. Preview and download continue after refresh or process restart. Each file is limited to 50 MiB.

Switching or closing the page does not stop core execution. Refreshing a URL with `workflow_run` reads that run again; the run list also reopens past executions. A core restart automatically finishes accepted result intents at persisted safe points, idempotently. Other unfinished executions become **Interrupted**. Choose **Continue same attempt** for supported model boundaries; tools with uncertain results remain pending.

## Configure a bounded review and revision loop

Open **Agent Teams → Assets → Workflows → New Canvas Workflow / Edit Asset**. Configure the writer and independent review tasks, an acyclic dependency from writer to reviewer, and the review output contract with a required Boolean `approved` field. Use **Add bounded loop** to choose the explicit body, exit result task, field comparison (`approved eq true`) and maximum iterations (1–100, within the effective member limit). Arbitrary expressions, graph back edges, overlapping bodies and a body that leaves and reenters its group are rejected before execution.

For the writer, declare first-round draft and feedback inputs. Under the loop, **Add feedback input** explicitly maps the previous writer's text to the next writer's draft and the previous review's text to its feedback. Ordinary dependency inputs within each round read that round's output. Collapse or expand the body to inspect the group on the actual canvas. Saving appends an asset version; existing runs retain their definition and configuration.

Each iteration creates new Task instances with new Attempts, frozen input references and separate model/tool records. Its revised result has its own immutable artifact identity: compare both `artifact_id` and `version`, not the version number alone. A retry keeps the Task and its original round inputs but creates a new Attempt. Recovery continues the original Attempt from its saved checkpoint. Earlier content and review/approval references remain unchanged.

In **Runs → View Run → Loop iterations**, expand a round to inspect inputs, results, the exact exit result reference and task model-call counts. Unknown usage is labelled unknown. Click a task to open its complete saved process, artifacts and controls; expand its round graph for the real task states. Consumers of an executed body wait for a matching validated exit. An unmatched final round shows **Iteration limit reached** and the run needs attention without a final successful result. Independent paths still run while a body waits. Whole-run pause or cancellation also applies across round boundaries; restarting does not create duplicate rounds or repeat saved effects.

If conditions skip the entire first-round body, the loop and round show **Skipped (unselected path)**. They produce no exit artifact or additional round. Merges ignore that unselected path and use their active branch inputs. A loop that already executed but has a skipped exit, failed work or an exhausted limit still blocks its dependent consumers; skipped task inputs never produce substitute results.

Optional conditional body paths may be skipped when the exit still produces a passing validated result. If a rejected exit needs feedback from a skipped task, the loop requires attention because that round has no result to pin.

A loop runs independently without a human node. To confirm a report, add a **Version approval** task after its review dependency and select the writer artifact task. The todo pins the passing iteration's concrete artifact. Export uses that approved reference. New manual revisions never automatically reschedule consumers or inherit old approvals.

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

## Previous and new entry points

| Previous entry | Current purpose |
| --- | --- |
| Agent Teams → Members, Workflow Editor, Run Monitor, History | Retains legacy teams, long-lived conversations and run entries. |
| Saving a reusable single-task workflow | Agent Teams → Assets → Workflows → New Workflow; independent of a legacy team. |
| Editing a reusable dependency workflow | Agent Teams → Assets → Workflows → New Canvas Workflow / Edit Asset; the legacy Workflow Editor remains separate. |
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
| New workflows had no persistent human entry | Assets → Workflows → Edit Asset → Human question / Version approval; Runs → View Run → Human requests. Legacy Workflow Editor human entries remain separate. |

Single-task assets remain compatible. Dependency assets provide acyclic scheduling, field conditions and persistent human requests through the same task result protocol. Bounded revision loops and autonomous teams share the same scheduler. Unknown tool decisions are available inside the selected task's process details.
