# Single-task workflow assets and independent runs

Open **More Features → Agent Teams → Workflow Assets**. First save a ready role under **Role Templates**, bind a local model that supports tool calls and select the built-in Agent executor.

1. Choose **New Workflow**, enter a name and select a role template and its version.
2. Enter comma-separated **Required text inputs (comma separated)**, such as `topic`. Reference an input with `{topic}` in the task; escape literal braces by doubling each brace. Only declared simple placeholders are supported.
3. Leave **Output contract (JSON Schema)** empty for a non-empty text result, or enter a JSON Schema with `type: object` for evidence, review flags or files. References must stay within this schema; remote schemas are not loaded. Choose **Save New Version**. Saving creates a definition without a member session; existing runs retain their original version.
4. Choose **Start Run**, fill every required input and optionally select exact versions under **Pinned input artifacts**. Summaries only help locate the desired content. Missing inputs or inaccessible resources produce field diagnostics without substituting defaults.
5. Open **Independent Runs → View Run**. Use **Artifact version** to read the complete text, evidence or structured result. Long content scrolls inside the result area without truncation. Files support **Preview file** and **Download file**. Expand **Output contract (JSON Schema)**, **Result submission history**, **Frozen effective configuration** and **Saved member process** to inspect the frozen contract and field errors.
6. Choose **Create artifact revision**, edit the complete result JSON, optionally attach ready files and choose **Save artifact version**. Revisions must satisfy the original contract and create immutable versions. For a version conflict, refresh and select the latest version first. Earlier results and consumer inputs stay unchanged; no downstream task is automatically rerun.

Every run creates a new member identity and isolated conversation. The role, inputs, persona prompt and effective configuration are frozen. Tool correction continues in the same member context. Later template edits affect new runs.

The Agent must call `submit_task_result` according to the contract. The default accepts `{"text":"Complete result"}`. For custom contracts, encode the complete result object as JSON text in the single `result_json` argument, for example `{"result_json":"{\"approved\":true}"}`. The tool description supplies the complete frozen schema, including composed schemas and local references, so providers receive a usable string argument while the service validates the decoded result against the original contract. Invalid submissions return specific field paths for correction within the remaining execution budget. Ordinary replies do not complete the task. A valid submission first persists an intent. At a saved safe point, the result, artifact version, completed status and key event commit in one transaction. Repeated submissions do not duplicate completion or versions.

Tasks call `read_task_artifact(artifact_id, version)` to read complete pinned inputs. Supplying a `file_id` from that version reads the complete file. Actual reads are recorded durably; the task cannot read an undeclared newer version. For file output, obtain the complete content through existing authorized tools and call `store_task_file(filename, content, encoding)` first and place its stable reference in `files: [{"file_id":"..."}]` only after success. Content uses UTF-8 text or base64 bytes. Complete bytes are stored in the system-managed database before submission, without temporary absolute paths. Preview and download continue after refresh or process restart. Each file is limited to 50 MiB.

Switching or closing the page does not stop core execution. Refreshing a URL with `workflow_run` reads that run again; the run list also reopens past executions. A core restart automatically finishes accepted result intents at persisted safe points, idempotently. Other unfinished executions become **Interrupted; not replayed**. This path does not restore model-response checkpoints or replay unfinished tasks; intents without a saved safe point remain pending.

![Artifact versions, complete output and submission field errors in the actual WebUI](/images/agent-workflow-artifacts-en.jpg)

## Previous and new entry points

| Previous entry | Current purpose |
| --- | --- |
| Agent Teams → Members, Workflow Editor, Run Monitor, History | Retains legacy teams, long-lived conversations and run entries. |
| Saving a reusable single-task workflow | Agent Teams → Workflow Assets; independent of a legacy team. |
| Starting and inspecting a new single-task run | Workflow Assets → Start Run, then Independent Runs. |
| Editing a template and inspecting run configuration | Workflow Assets appends versions; Independent Runs displays frozen snapshots. |

This path supports one role executing one task with contracted results and pinned artifact versions. Approval binding, bounded revision loops, a live event workbench, invocation checkpoints and team orchestration connect to this version protocol in subsequent tasks.
