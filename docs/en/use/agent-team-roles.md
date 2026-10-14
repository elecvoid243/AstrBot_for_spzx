# Agent Teams role templates

Open **More Features → Agent Teams → Role templates**, then choose **New role**. A template stores responsibilities and capability requirements without creating a member chat session or starting a run.

1. Enter a name and description. Under **Responsibility and prompt**, write an inline prompt or define a logical persona slot, bind a local persona and select **Inherit local persona prompt**.
2. Under **Model**, select the profile that chooses the actual executor. The built-in executor uses a logical model slot and a local model binding; remote executors use the configured remote application and hide unused local model fields. Select `tool_use`, `image` or `audio` requirements as needed. Under **Required executor capabilities**, choose internal invocation checkpoints, original remote run query, local task/team tools or explicit task result submission.
3. Under **Tools, Skills, Knowledge bases**, choose **Inherit**, **Disable all** or **Local override**. For an override, type logical slot names and press Enter, then bind each slot to a local resource. Slots express portable requirements; bindings record identities on this machine.
4. Configure instances, parallelism, steps, iterations, nesting depth, tool timeout and context length under **Instances and execution limits**. Empty values inherit. Independent single-task runs freeze the effective configuration; see the [workflow instructions](./agent-team-workflows.md) for supported behavior and limits.
5. Choose **Preview configuration** to inspect effective values, inheritance and overrides. Missing, ambiguous, disabled or incompatible resources have field paths; click a path to expand its group. Preview again after changing the form.
6. Choose **Save role**. Incomplete templates can be saved and are marked **Needs configuration**. Editing uses **Save new version**, preserving previous definitions and bindings. **View historical version** reopens an old version; saving its edited content creates another new version.

Inline prompts are portable definition content and appear explicitly in the preview. A persona reference declares a dependency; its local prompt is not embedded in the definition. Complete asset package import and export are delivered by a subsequent ticket.

Tool and skill availability follows the selected profile's enabled plugins, and skills also use that profile's runtime. Local overrides respect the persona's tool and skill allowlists; without an explicit persona binding, these capabilities inherit the profile's default persona. An excluded binding receives a field diagnostic instead of being shown as ready. Built-in tools follow their own profile availability, as in the existing main Agent pipeline.

The preview distinguishes internal checkpoints, remote queries and an external call boundary. Remote executors cannot execute AstrBot's local tools, skills or knowledge bases, or enforce local `max_steps`, tool timeout or context-length overrides. Explicit unsupported requirements receive field errors. Autonomous coordinators require local task/team tools. Missing remote credentials are reported against the profile without exposing their values. Unsupported model-level token allowances and output ceilings are rejected; outer call-step limits still apply.

Dify, Coze and DashScope Application receive the frozen role/persona framing as a prefix to the supported task or user input. The remote application's own system instructions remain configured on that service.

![Role configuration and missing-resource diagnostics in the actual WebUI](/images/agent-team-roles-en.jpg)

![Required internal recovery rejected for an actual remote executor](/images/agent-teams-runner-role-en.jpg)

## Previous entries and the new entry

| Existing entry | Entry and purpose in this ticket |
| --- | --- |
| Agent Teams → Members | Retains legacy member configuration and long-lived sessions; no automatic role migration. |
| Creating a reusable role | Agent Teams → Role templates → New role; templates are independent of legacy teams and members. |
| Workflow Editor, Run Monitor, History | Legacy entries remain; new single-task and dependency workflow assets reference pinned roles and are inspected under Independent Runs. |

See [Workflow assets, dependency canvas and independent runs](./agent-team-workflows.md) for scheduling, invocation recovery, human tasks and bounded loops. Role, readiness and launch checks use the same executor rules. Edits do not change existing runs' pinned roles and configuration; recovery still checks current access and remote binding compatibility.

Previews use current local resources. Historical definitions never silently fall back to a default model. Deleting or disabling a resource produces a diagnostic on the next preview. This ticket does not complete the parent specification's run recovery, migration or entire workbench acceptance criteria.
