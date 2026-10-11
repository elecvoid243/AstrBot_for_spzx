# Single-task workflow assets and independent runs

Open **More Features → Agent Teams → Workflow Assets**. First save a ready role under **Role Templates**, bind a local model that supports tool calls and select the built-in Agent executor.

1. Choose **New Workflow**, enter a name and select a role template and its version.
2. Enter comma-separated **Required text inputs (comma separated)**, such as `topic`. Reference an input with `{topic}` in the task; escape literal braces by doubling each brace. Only declared simple placeholders are supported.
3. Choose **Save New Version**. Saving creates a definition without a member session. Editing appends an immutable version; existing runs retain their original version.
4. Choose **Start Run** on the asset and fill every required input. Missing inputs and deleted, disabled or disallowed resources produce field diagnostics without substituting a default model or persona.
5. Open **Independent Runs → View Run**. Inspect the asset version, run member, attempt, read-only snapshot and complete submitted text. Expand **Frozen effective configuration** and **Saved member process** for the execution data.

Every run creates a new member identity and isolated conversation. The role, inputs, persona prompt and effective configuration are frozen. Tool correction continues in the same member context. Later template edits affect new runs.

The Agent must call `submit_task_result(text)` with non-empty text. An ordinary reply does not complete the task; it leaves the run **Awaiting explicit result**. A valid submission first persists an intent. The result and completed status are written consistently after execution reaches a saved safe point. The complete text is retained instead of inferring a result from the last natural-language reply.

Switching or closing the page does not stop core execution. Refreshing a URL with `workflow_run` reads that run again; the run list also reopens past executions. A core restart automatically finishes accepted result intents at persisted safe points, idempotently. Other unfinished executions become **Interrupted; not replayed**. This path does not restore model-response checkpoints or replay unfinished tasks; intents without a saved safe point remain pending.

![Independent run and read-only result in the actual WebUI](/images/agent-workflow-run-en.jpg)

## Previous and new entry points

| Previous entry | Current purpose |
| --- | --- |
| Agent Teams → Members, Workflow Editor, Run Monitor, History | Retains legacy teams, long-lived conversations and run entries. |
| Saving a reusable single-task workflow | Agent Teams → Workflow Assets; independent of a legacy team. |
| Starting and inspecting a new single-task run | Workflow Assets → Start Run, then Independent Runs. |
| Editing a template and inspecting run configuration | Workflow Assets appends versions; Independent Runs displays frozen snapshots. |

This path delivers one role executing one task with a default text result. Custom output contracts, structured artifacts, a live event workbench, invocation checkpoints and team orchestration belong to subsequent tasks.
