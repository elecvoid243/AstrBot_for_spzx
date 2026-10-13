# Workflow assets, dependency canvas and independent runs

Open **More Features → Agent Teams → Workflow Assets**. First save a ready role under **Role Templates**, bind a local model that supports tool calls and select the built-in Agent executor.

## Autonomous teams

Choose **Workflow Assets → New Autonomous Team**. Enter the **Team name**, choose the **Coordinator role** and its pinned version, then select **Allowed role templates** and pin each version. Set **Maximum members** (including the coordinator), **Maximum concurrent tasks** and **Maximum member tasks**. Role instance and concurrency limits also apply. An optional **Final version approval question** requires approval of the coordinator's exact final artifact before the run can complete. **Output contract (JSON Schema)** applies to that final result; leaving it empty uses the standard text contract.

![Actual autonomous team configuration](/images/agent-autonomous-editor-en.jpg)

Save the asset, choose **Start Run**, enter a nonempty **goal**, and start. The coordinator uses `team_manage` to create allowed instances, dispatch tasks, inspect complete results and wait. Members submit contracted outputs through `submit_task_result`. A dependency pins the producer's original artifact for `read_task_artifact`; the coordinator can also read members' complete original artifacts. Plain completion text and premature submissions cannot complete the run. Calling a tool name does not grant a member coordinator authority.

If the asset declares additional inputs, fill each field when starting. All declared values reach the coordinator from the frozen run inputs, including multiline text and literal braces. Later asset edits do not change recovered inputs. Run overviews retain member identities and relationships; open the run detail to read complete frozen configuration.

In **Independent Runs → View Run**, inspect dynamically created tasks and **Members in this run**. **Expand execution graph (read-only)** shows dependencies and dashed **Dispatched** links from the coordinator. Dispatch links show who created a task; they are not execution prerequisites. Select a task to read its complete artifacts, frozen configuration and saved member process. Distinct instances and new runs have isolated conversations; a member continues its context across this run's tasks, including after checkpoint recovery.

![Actual autonomous team run with independent instances and a revision task](/images/agent-autonomous-run-en.jpg)

Waiting releases active execution capacity, including at a concurrency limit of one. **Waiting for members** is a local wait. Pause, continue, cancel, same-Attempt recovery, explicit retry and unknown-tool decisions retain the shared workflow semantics below. Failed member tasks and unknown calls prevent success. Edited templates affect new runs; dynamic members in an existing run use the original frozen templates and settings. Required final approval uses the existing persistent human-request controls.

For the versioned autonomous path, **team sidebar / Run Monitor → Workflow Assets → New Autonomous Team / Independent Runs → View Run**. Older records and entry points remain separate until migration. Member communication and workflow calls to autonomous teams use the versioned paths described below.

![Autonomous run on a narrow screen](/images/agent-autonomous-mobile.jpg)

## Coordinator calls to saved workflows

In **Workflow Assets → New Autonomous Team / Edit Asset → Allowed workflows**, choose **Allow a saved workflow**. Select its **Pinned workflow version** and **Return task**. The panel shows that version's required text inputs and the return task's output contract. Single-task workflows return their **result** task; canvas workflows return the task you select. Save the team as a new version. Workflows omitted from this list remain unavailable to its coordinator.

![Allowed workflow versions and explicit return contract in the actual WebUI](/images/agent-team-workflow-config-en.jpg)

Set **Root nesting depth limit** and **Maximum concurrent tasks**, then choose **Start Run** and enter the goal. The coordinator may call an allowed workflow with every declared input and optional visible, exact artifact references. Each call creates independent members; parent conversations and unrelated artifacts are not inherited. The coordinator waits for the call's task, then reads its complete result and immutable artifact. The chosen return task must have a validated result; a skipped return requires attention even if the workflow's other paths completed.

In **Independent Runs → View Run**, select the coordinator and choose **View child workflow** beside its call. This route also works for coordinators inside a nested team. The call shows the returned result, its own artifact and the exact child return reference. Choose **View child workflow** again to inspect the child tasks, or expand **Child scope** to see the saved workflow graph. **Human requests**, progress and **Saved member process** show the actual tasks and their waits, attempts and tool results.

![Coordinator, workflow tasks and exact returned artifacts in the actual WebUI](/images/agent-team-workflow-run-en.jpg)

Waiting releases the shared root capacity, so calls can finish with concurrency one. Team and workflow nesting share one bounded depth; role limits still apply. Whole-run pause, continue and cancellation reach the child workflow. A local human or failed branch leaves independent branches free to proceed. Continue keeps local waits; use the selected task's normal same-Attempt recovery or unknown-tool decision when required. Saved tool results are reused after a restart, and unknown external effects remain stopped for confirmation.

Running and recovered calls retain their original asset, role, input, resource configuration and exact result versions. Editing an allowed workflow affects new team versions and runs. Returned managed files remain fully readable, and an explicit revision can retain the exact files already handed off. Unsubmitted child files and unrelated files remain inaccessible; old artifacts, approvals and consumer input versions retain their meaning.

Version reads belong to the current selection and editor. A delayed older response cannot replace the latest version, required inputs or return contract. Changing another workflow row preserves each row's latest selection; saving waits for all current version reads. The saved version remains the one displayed after the latest choice.

![A delayed older version leaves the latest explicit workflow selection unchanged](/images/agent-team-workflow-selection-race-zh.jpg)

![A child approval saved during pause waits for whole-run continuation](/images/agent-team-workflow-wait-zh.jpg)

![A parent revision retains the returned file and the child v1 reference](/images/agent-team-workflow-file-revision-zh.jpg)

![Fixed workflow configuration at a narrow-screen breakpoint](/images/agent-team-workflow-mobile.jpg)

![Allowed workflow configuration in dark mode](/images/agent-team-workflow-dark.jpg)

## Dependency canvas

Choose **New Canvas Workflow**. Enter the workflow name, required text inputs and maximum concurrent tasks, then drag a role from the toolbox onto the canvas (or click it). Select a task to edit its name, instructions, pinned role version, member instance key, input references and output contract. Tasks sharing a member key use the same frozen role and configuration and advance one conversation serially; distinct member keys create isolated contexts and may run in parallel within the workflow and role limits.

Connect a task's right handle to another task's left handle. Choose a dependency by clicking its edge or using **Select dependency** and enable **Field condition** to compare a validated output field. Conditions use `eq`, `ne`, `gt`, `ge`, `lt` or `le` with a correctly typed value, without evaluating code. Fields must be required scalars in the source output contract; nested objects, local schema references and `allOf` may declare these fields. Ordered comparisons require numeric fields.

Use **Add input reference** to assign an alias to a workflow input or an upstream result field; insert `{alias}` into task instructions. Result references require a dependency path. The producer's original artifact version is pinned when the consumer starts, and its complete content remains available through `read_task_artifact`. Revising the producer does not change or rerun its consumers. Save validation reports specific field paths for invalid references, conditions and unbounded back edges. The canvas supports acyclic dependencies, persistent human requests, explicit bounded loop bodies and pinned autonomous team calls.

Use the canvas controls to zoom and fit; the toolbar provides minimap visibility, undo/redo and dependency-based automatic layout. Node positions and properties are saved with the asset version. On narrow screens the toolbox and properties collapse into sections. Choose **Save New Version**, then **Start Run** from the saved asset. Saving does not execute the graph, and later edits do not change an existing run.

![Actual dependency canvas with role, input and output properties](/images/agent-workflow-graph-en.jpg)

In **Independent Runs**, the graph is a read-only frozen snapshot. Select a task or use **Inspect task** to view its real status, waiting/failure reason, attempt, effective configuration and exact artifact versions. An unmatched condition skips its path; a merge waits for active paths and is not blocked by skipped ones. Failures block their dependents while unrelated ready tasks continue. The root reports completion only after all required active paths produce validated results. Duplicate scheduling notifications do not dispatch a task twice.

![Actual run graph with completed tasks, a skipped path and pinned input versions](/images/agent-workflow-graph-run-en.jpg)

At startup, accepted task submissions at saved safe points finish idempotently. Other abandoned task attempts become interrupted. Pending independent branches can still advance from persisted state. Select an interrupted task to continue its original attempt from a saved built-in model boundary. A new execution owner prevents the abandoned executor from publishing late results.

## Call an autonomous team from a workflow

Save a ready autonomous team first. In **Workflow Assets → New Canvas Workflow**, choose **Call autonomous team** in the role toolbox. Select the node to choose its **Pinned child team asset** and **Pinned team version**. Declare exactly the inputs required by that version, using workflow inputs or upstream result fields with dependency paths. The output contract comes from the chosen team version. Expand the team definition above the canvas to inspect its pinned coordinator and allowed roles; collapse it to keep the parent graph compact.

Set **Root nesting depth limit** and **Maximum concurrent tasks**, then **Save New Version**. Reopening retains the selected version, references and layout. A version response only applies to the latest choice in the current editor; an unavailable choice blocks saving. A run freezes every child definition, role configuration and resource binding before dispatch. Editing the child later affects new runs. Declared multiline inputs and literal braces reach its coordinator unchanged. Required inputs must remain nonempty after resolving upstream results; invalid values show input field errors before any child scope is created, while independent branches continue.

![A newly authored fixed-version child call in the actual canvas](/images/agent-workflow-child-editor-en.jpg)

In **Independent Runs → View Run**, progress includes the actual child coordinator and dynamically dispatched member tasks. Expand **Child scope** to see the calling parent, exact asset version, root run identity and child task graph. Select a child task from progress, its graph or **Inspect task** to read full artifacts, attempts and process records. The parent call returns the child's original validated artifact through the declared output contract; downstream inputs pin that returned version. A parent artifact revision can retain files already handed off in its current version. Unreturned files remain inaccessible; older content, consumer inputs and approvals keep their exact versions.

![The actual parent/child relationship and child execution graph](/images/agent-workflow-child-run-en.jpg)

![The latest v2 choice retains its input and contract after a delayed v1 response](/images/agent-workflow-child-selection-race-zh.jpg)

![A revised parent caption still previews the complete returned file](/images/agent-workflow-child-file-revision-zh.jpg)

Parent waits release root execution capacity, so a root limit of one still permits child tasks to complete. A child human request, provider failure or unknown external effect leaves independent parent branches ready. Handle child human requests in the root run's **Human requests** section; select the affected child task to recover, retry or resolve an unknown tool. Retrying a child task creates a new Attempt in the existing scope. Saved model-call and step counts include child tasks and prior attempts; unavailable counts display **Unknown**. Detailed budget reporting is added separately.

Whole-run pause, continue and cancel apply to every scope and executor. A paused answer remains saved until continuation; other local waits remain. Recovery uses the original child scope, Attempt and frozen inputs. Different scopes have independent members and conversations. Ancestry grants no cross-scope message or artifact access; only declared exact input/output references cross the boundary. Coordinator calls use the explicit permissions described under Coordinator calls to saved workflows.

![A child wait and its human request in the actual WebUI](/images/agent-workflow-child-wait.jpg)

![Saved child approval during a whole-run pause in the dark theme](/images/agent-workflow-child-dark.jpg)

![Long child task names and wait labels on a narrow screen](/images/agent-workflow-child-mobile.jpg)

![An unknown child tool blocks dependent paths while independent parent work completes](/images/agent-workflow-child-error.jpg)

## Run progress and member process

Open **Independent Runs → View Run**. The run list and history query saved runs. Expand **Run list and history** within the detail to switch runs. The detail starts with the goal, pinned asset version, completed task count and waiting reasons. Select a progress task or a task under **Needs attention** to inspect its artifacts and member process. **Expand execution graph (read-only)** retains selection of actual graph tasks and pinned inputs.

The run list loads goal previews of up to 200 characters, statuses, progress, waiting reasons, attempts and artifact entries. Complete goals, frozen input values, results and process records load for the selected run. Polling with an unchanged observation cursor returns no snapshot and does not read saved process history; changes and reconnection snapshots retain complete records.

The **Artifacts** section still reads complete results at an exact version and previews or downloads files. Expand **Saved member process** to inspect complete model responses, thinking actually returned by the provider, tool arguments and original tool results. If a tool returns partial results and then fails, each original result and the final error remain readable. Records show their task, attempt and member. Models returning no thinking have no thinking section. Long content remains scrollable without relying on a truncated summary.

![Goal, task progress, waiting reason and members in the actual WebUI](/images/agent-workflow-progress-en.png)

![Expanded complete model response, returned thinking and tool arguments](/images/agent-workflow-process-en.png)

Closing, refreshing or leaving the page does not cancel execution. Reopening reads a consistent persistent snapshot. Subsequent changes continue through a monotonic observation cursor; duplicate events cannot replace newer state, and an expired cursor reloads the snapshot. Completed responses and tool results remain readable after rebuilding the process. This cursor reconnects observation; it does not restore unfinished model or tool invocations.

![Run progress with the dark theme](/images/agent-workflow-progress-dark.png)

On narrow screens, progress, waiting reasons and members stack vertically. Long names and identities wrap, and artifact and process controls remain accessible.

![Progress, artifacts and process on a narrow screen](/images/agent-workflow-progress-mobile.png)

## Member communication

Open **Independent Runs → View Run → select a task → Saved member process → Member messages**. Each delivery shows its sender and recipient instance, scope, source task and Attempt. Expand it to read the complete message, see whether it is pending or consumed, and inspect the receiving task, Attempt and saved context position. **Read complete version** reads the exact artifact reference in the message; editing an artifact does not change that reference.

Members use `team_communicate` to inspect permitted recipients and send questions or feedback. Communication stays within the current run and execution scope. A member can share its own outputs, frozen common inputs or exact versions already shared with it; a message cannot grant access to another run or an unshared version. The coordinator retains member creation, dispatch and whole-run completion authority. Sending a message does not start a new task or complete one.

Messages arriving during a model request wait for the next safe request boundary. Their consumption position commits together with that request's effective context. Repeating a delivery or restarting after message, tool-result or context persistence does not append another copy of a consumed message. Existing pause, cancellation, capacity and same-Attempt recovery controls apply.

![Scoped delivery, saved consumption position and complete artifact version in the actual WebUI](/images/agent-team-messages-en.jpg)

Long identities and version controls wrap on narrow screens; the same records remain readable in the dark theme.

![Member message and complete version on a narrow screen](/images/agent-team-messages-mobile.jpg)

![Member message and complete version in the dark theme](/images/agent-team-messages-dark.jpg)

## Continue the original attempt

Open **Independent Runs → View Run**, select the interrupted task and choose **Continue same attempt**. The page shows whether the last saved boundary is a model request, a complete model response, or a started tool. Recovery keeps the original Attempt, asset version, member conversation, effective configuration, temporary framing, multimodal content, provider thinking/signatures and execution counters. Missing resources or changed permissions block execution instead of selecting defaults.

A complete saved model response is reused with its original tool plan. If no complete response was saved, the model request may be sent again; partial streaming generation and remote billing deduplication are not guaranteed. The built-in executor supports both full schemas and two-stage parameter generation. Other executors cannot use this workflow's internal recovery.

Each tool call retains its intent, execution owner, original result and context application position. Expand a tool entry under **Saved member process** to inspect this evidence. Saved results are reused and applied once; a crash after context application continues the next model step. Partial tool output is retained separately from completion: an interrupted or failed generator with unfinished execution remains unknown, even when some output is already saved. A partially completed batch recovers each call separately: saved results are reused, unstarted calls may execute, and unknown external effects remain pending confirmation. Dependent model calls are blocked until every result is known.

## Handle an unknown tool result

In **Independent Runs → View Run**, select the affected task and expand its unknown tool entry under **Saved member process**. Inspect the original arguments, partial output, invocation identity and any saved query evidence. Independent workflow paths continue while this task waits.

- If the adapter declared an authoritative query, an idempotency key or safe retry before execution, **Continue same attempt** can reconcile the original call. **Reconcile using declared tool capability** requests another check. An unresolved query remains unknown; tool names and timeouts never authorize replay.
- To supply a result independently verified outside AstrBot, choose **Record verified result and continue** or **Record verified failure and continue**, enter the observed evidence and complete tool result, and submit. A generic completion flag without evidence and output cannot resolve the call.
- To deliberately execute again, choose **Explicitly request a new execution** and record the basis for the decision. This may repeat an external effect. The new invocation links back to the original unknown invocation, which remains readable; the saved model plan, Attempt and frozen configuration continue.

Decisions and query outcomes survive refresh and restart. Repeating the same submitted decision cannot schedule another execution. A late result from the original execution cannot replace the selected new execution. Recovery follows the adapter's declared guarantees; AstrBot cannot promise exactly one effect for arbitrary external tools.

![Original unknown invocation, its arguments and explicit retry controls in the actual WebUI](/images/agent-workflow-tool-resolution-en.jpg)

![The new execution retains the original invocation link and its saved evidence after refresh](/images/agent-workflow-tool-resolution-completed.jpg)

Result submission is an internal idempotent operation for both the default text contract and custom contracts, including stable file references. Recovery finishes the existing intent or repeats only this submission, preserving one artifact version and one completion event. Saved acceptance or rejection results retain their original state and context position. Other calls in the same batch must reach a safe result boundary before completion; unknown external effects prevent finalization. Complete temporary files for large tool output are rebuilt before continuing, so cleared temporary storage does not leave unreadable result references.

![Saved tool result, original owner and context application position](/images/agent-workflow-tool-recovery-en.jpg)

![Actual interrupted attempt with its saved response and recovery action](/images/agent-workflow-recovery-en.jpg)

![Same original attempt completed after recovery](/images/agent-workflow-recovery-completed.jpg)

## Pause, continue, cancel and retry

Open **Agent Teams → Independent Runs → View Run**. **Whole run controls** applies to the root run and all its existing task executors; selecting a task does not turn pause into a local wait.

- **Pause run** saves the decision and stops admission of new tasks, models and tools. Already admitted external calls may still finish; the runner saves their outcome and parks at a safe boundary. Pausing does not undo an external effect, and paused time does not consume the reply timeout.
- **Continue run** releases the whole-run pause with the original snapshot and Attempt. Local failures and unknown tool waits remain. After a process restart, paused executions remain stopped; continuing resumes their original checkpoints. **Continue same attempt** is checkpoint recovery for an interrupted task, rather than a new Attempt.
- **Cancel run** asks the existing executors to stop and permanently stops new dispatch. Confirm the decision in the dialog. Completed results, accepted result evidence, external effects and history remain readable after refresh or restart. A late executor cannot replace the cancelled state.
- Select a failed task and choose **Retry failed task**. The dialog displays its original frozen text inputs and exact artifact references. **Confirm decision** creates a new Attempt with those inputs. It does not silently select a newer artifact or create a new loop iteration. Expand **Previous attempt** to inspect the failure, input references and saved token usage; full process records retain their Attempt associations. Graph retries use the existing capacity and member exclusions.

Resolve an unknown tool outcome under **Saved member process** first. Ordinary task retry cannot bypass the tool reconciliation or evidenced human-decision protocol. Repeated identical control or retry requests do not create additional decisions or Attempts. If another control changed the revision, refresh before making a new decision.

You can save a verified result, verified failure, explicit tool retry or reconciliation decision while the whole run is paused. The page confirms that the decision is saved and waits for **Continue run**; saving the decision does not admit external work. Continuing or restarting after an accepted continuation uses the original Attempt and call evidence, subject to local waits and capacity. A normal core shutdown preserves the same paused continuation as an unexpected process stop. A saved pending decision cannot be replaced by another decision before it is consumed.

![An accepted tool decision waits for whole-run continuation in the actual WebUI](/images/agent-workflow-paused-decision-en.jpg)

![Paused whole-run controls and the original Attempt in the actual WebUI](/images/agent-workflow-controls-en.jpg)

![A retried task retains the previous failure, exact inputs and saved usage](/images/agent-workflow-retry-history.jpg)

## Human requests and version approval

1. Open **Workflow Assets → New Canvas Workflow / Edit Asset**. Add **Human question** or **Version approval** from **Role toolbox**. Human nodes require no model or role. Enter the question, allowed answers (one per line, or empty for free text) and maximum answer length.
2. For approval, select the upstream **Artifact task** and exact **Artifact version**, and connect the artifact task as a dependency. Save a new asset version and start it; existing runs retain their definition.
3. Open **Independent Runs → View Run → Human requests**. Questions, constraints, task, Attempt and approval references persist. Waiting releases capacity and blocks only dependent paths. Independent branches continue; select their progress entries to read results.
4. Choose or enter an answer and select **Submit answer**. For approval, first **Preview approval version** to read the complete pinned content, then **Approve this version**. Files can be previewed. **Reject with feedback** requires feedback, preserves rejection evidence and blocks the affected path. This does not create a revision loop.
5. **Export approved version** downloads JSON containing complete content and exact file references; after previewing, download files of that version. File downloads for an artifact governed by an approval node reject unapproved versions. Creating v2 never inherits v1 approval, changes its preview or automatically reruns downstream tasks.
6. Refresh or reopen after restart to continue handling requests. Repeated identical answers are consumed once; another answer cannot replace one already saved. You may answer while globally paused: **Response saved. It will be consumed after the whole run continues.** Continuing preserves unanswered requests; cancellation retains requests and response history while preventing further consumption. Ordinary task retry cannot replace human answers.

If the requested revision does not exist yet, the request retains that exact version; preview and approval explain that it is unavailable. Explicitly create the required revision on the artifact task, then preview and approve the original request. The service does not substitute v1 or the latest version, and no new Attempt is needed.

![Persistent human requests and their pinned approval preview in the actual WebUI](/images/agent-workflow-human-en.jpg)

![An approved v1 remains the preview and export object after a v2 revision](/images/agent-workflow-human-approval-en.jpg)

![Rejection feedback remains visible with its task and exact version](/images/agent-workflow-human-rejected-en.jpg)

![Structured approval previews every contracted field before exporting the same object](/images/agent-workflow-human-structured-en.jpg)

## Single-task assets

1. Choose **New Workflow**, enter a name and select a role template and its version.
2. Enter comma-separated **Required text inputs (comma separated)**, such as `topic`. Reference an input with `{topic}` in the task; escape literal braces by doubling each brace. Only declared simple placeholders are supported.
3. Leave **Output contract (JSON Schema)** empty for a non-empty text result, or enter a JSON Schema with `type: object` for evidence, review flags or files. References must stay within this schema; remote schemas are not loaded. Choose **Save New Version**. Saving creates a definition without a member session; existing runs retain their original version.
4. Choose **Start Run**, fill every required input and optionally select exact versions under **Pinned input artifacts**. Summaries only help locate the desired content. Missing inputs or inaccessible resources produce field diagnostics without substituting defaults.
5. Open **Independent Runs → View Run**. Use **Artifact version** to read the complete text, evidence or structured result. Long content scrolls inside the result area without truncation. Files support **Preview file** and **Download file**. Expand **Output contract (JSON Schema)**, **Result submission history**, **Frozen effective configuration** and **Saved member process** to inspect the frozen contract and field errors.
6. Choose **Create artifact revision**, edit the complete result JSON, optionally attach ready files and choose **Save artifact version**. Revisions must satisfy the original contract and create immutable versions. For a version conflict, refresh and select the latest version first. Earlier results and consumer inputs stay unchanged; no downstream task is automatically rerun.

Every run creates a new member identity and isolated conversation. The role, inputs, persona prompt and effective configuration are frozen. Tool correction continues in the same member context. Later template edits affect new runs.

The Agent must call `submit_task_result` according to the contract. The default accepts `{"text":"Complete result"}`. For custom contracts, encode the complete result object as JSON text in the single `result_json` argument, for example `{"result_json":"{\"approved\":true}"}`. The parameter description supplies the complete frozen schema, including composed schemas and local references, in both `full` and two-stage `skills_like` mode. Providers receive a usable string argument while the service validates the decoded result against the original contract. Invalid submissions return specific field paths for correction within the remaining execution budget. Ordinary replies do not complete the task. A valid submission first persists an intent. At a saved safe point, the result, artifact version, completed status and key event commit in one transaction. Repeated submissions do not duplicate completion or versions.

Tasks call `read_task_artifact(artifact_id, version)` to read complete pinned inputs. Supplying a `file_id` from that version reads the complete file. Actual reads are recorded durably; the task cannot read an undeclared newer version. For file output, obtain the complete content through existing authorized tools and call `store_task_file(filename, content, encoding)` first and place its stable reference in `files: [{"file_id":"..."}]` only after success. Content uses UTF-8 text or base64 bytes. Complete bytes are stored in the system-managed database before submission, without temporary absolute paths. Preview and download continue after refresh or process restart. Each file is limited to 50 MiB.

Switching or closing the page does not stop core execution. Refreshing a URL with `workflow_run` reads that run again; the run list also reopens past executions. A core restart automatically finishes accepted result intents at persisted safe points, idempotently. Other unfinished executions become **Interrupted**. Choose **Continue same attempt** for supported model boundaries; tools with uncertain results remain pending.

![Artifact versions, complete output and submission field errors in the actual WebUI](/images/agent-workflow-artifacts-en.jpg)

## Configure a bounded review and revision loop

Open **Agent Teams → Workflow Assets → New Canvas Workflow / Edit Asset**. Configure the writer and independent review tasks, an acyclic dependency from writer to reviewer, and the review output contract with a required Boolean `approved` field. Use **Add bounded loop** to choose the explicit body, exit result task, field comparison (`approved eq true`) and maximum iterations (1–100, within the effective member limit). Arbitrary expressions, graph back edges, overlapping bodies and a body that leaves and reenters its group are rejected before execution.

For the writer, declare first-round draft and feedback inputs. Under the loop, **Add feedback input** explicitly maps the previous writer's text to the next writer's draft and the previous review's text to its feedback. Ordinary dependency inputs within each round read that round's output. Collapse or expand the body to inspect the group on the actual canvas. Saving appends an asset version; existing runs retain their definition and configuration.

Each iteration creates new Task instances with new Attempts, frozen input references and separate model/tool records. Its revised result has its own immutable artifact identity: compare both `artifact_id` and `version`, not the version number alone. A retry keeps the Task and its original round inputs but creates a new Attempt. Recovery continues the original Attempt from its saved checkpoint. Earlier content and review/approval references remain unchanged.

In **Independent Runs → View Run → Loop iterations**, expand a round to inspect inputs, results, the exact exit result reference and task model-call counts. Unknown usage is labelled unknown. Click a task to open its complete saved process, artifacts and controls; expand its round graph for the real task states. Consumers of an executed body wait for a matching validated exit. An unmatched final round shows **Iteration limit reached** and the run needs attention without a final successful result. Independent paths still run while a body waits. Whole-run pause or cancellation also applies across round boundaries; restarting does not create duplicate rounds or repeat saved effects.

If conditions skip the entire first-round body, the loop and round show **Skipped (unselected path)**. They produce no exit artifact or additional round. Merges ignore that unselected path and use their active branch inputs. A loop that already executed but has a skipped exit, failed work or an exhausted limit still blocks its dependent consumers; skipped task inputs never produce substitute results.

Optional conditional body paths may be skipped when the exit still produces a passing validated result. If a rejected exit needs feedback from a skipped task, the loop requires attention because that round has no result to pin.

A loop runs independently without a human node. To confirm a report, add a **Version approval** task after its review dependency and select the writer artifact task. The todo pins the passing iteration's concrete artifact. Export uses that approved reference. New manual revisions never automatically reschedule consumers or inherit old approvals.

![Bounded loop configuration in the actual WebUI](/images/agent-workflow-loop-editor-en.png)

![Review failure followed by a passing revision in the actual WebUI](/images/agent-workflow-loop-run-en.jpg)

![An unselected loop stays skipped while its active branch completes the merge](/images/agent-workflow-loop-skip-en.png)

## Previous and new entry points

| Previous entry | Current purpose |
| --- | --- |
| Agent Teams → Members, Workflow Editor, Run Monitor, History | Retains legacy teams, long-lived conversations and run entries. |
| Saving a reusable single-task workflow | Agent Teams → Workflow Assets → New Workflow; independent of a legacy team. |
| Editing a reusable dependency workflow | Agent Teams → Workflow Assets → New Canvas Workflow / Edit Asset; the legacy Workflow Editor remains separate. |
| Starting and inspecting a new workflow run | Workflow Assets → Start Run, then Independent Runs → View Run → Execution progress to select a task; the Inspect task selector remains available. |
| Default graph and reply JSON within the detail | Progress appears first. The graph moves to Expand execution graph (read-only), and replies move to expandable Saved member process records. |
| Editing a template and inspecting run configuration | Workflow Assets appends versions; Independent Runs displays frozen snapshots. |
| An interrupted task only exposed process records | Independent Runs → View Run → select task → Continue same attempt; saved tool results are reused, and unknown effects require confirmation. |
| Unknown tool effects only displayed a waiting reason | Independent Runs → View Run → select task → Saved member process → expand the unknown call → reconcile, record a verified outcome or explicitly request a new execution. |
| Run details only showed checkpoint recovery | Independent Runs → View Run → Whole run controls; select the failed task → Retry failed task. |
| Member communication appeared only in tool results | Independent Runs → View Run → select task → Saved member process → Member messages; expand a reference to read its complete version. |
| Coordinators could only dispatch member tasks | Workflow Assets → New Autonomous Team / Edit Asset → Allowed workflows; Independent Runs → View Run → select coordinator → View child workflow. |
| Revision used only ordinary task dependencies | Workflow Assets → Edit Asset → Add bounded loop; Independent Runs → View Run → Loop iterations. |
| New workflows had no persistent human entry | Workflow Assets → Edit Asset → Human question / Version approval; Independent Runs → View Run → Human requests. Legacy Workflow Editor human entries remain separate. |

Single-task assets remain compatible. Dependency assets provide acyclic scheduling, field conditions and persistent human requests through the same task result protocol. Bounded revision loops and autonomous teams share the same scheduler. Unknown tool decisions are available inside the selected task's process details.
