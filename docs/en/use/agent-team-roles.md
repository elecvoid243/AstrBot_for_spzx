# Agent Teams role templates

Open **More Features → Agent Teams → Assets → Role templates**, then choose **New role**. A template stores responsibilities and capability requirements without creating a member chat session or starting a run.

Assets retain their pinned role versions until you explicitly edit those references; existing runs retain their frozen configuration. Create and configure role templates directly, without migrating previous members.

1. Enter a name and description. Under **Responsibility and prompt**, write the responsibility and optionally select a persona. The two remain separate; leaving persona empty does not inherit the profile default.
2. Under **Model**, select the profile that chooses the actual executor. The built-in executor uses the selected model; remote executors use the configured remote application and hide unused local model fields. Select `tool_use`, `image` or `audio` requirements as needed. Under **Required executor capabilities**, choose internal invocation checkpoints, original remote run query, local task/team tools or explicit task result submission.
3. Under **Tools, Skills, Knowledge bases**, choose **Inherit** or **Custom selection**. Select actual resources in a custom list; leave it empty to disable that category. Resource IDs, not display names, are saved.
4. Configure instances, parallelism, steps, iterations, nesting depth, tool timeout and context length under **Instances and execution limits**. Empty values inherit. Independent single-task runs freeze the effective configuration; see the [workflow instructions](./agent-team-workflows.md) for supported behavior and limits.
5. Choose **Preview configuration** to inspect effective values, inheritance and overrides. Missing, ambiguous, disabled or incompatible resources have field paths; click a path to expand its group, scroll the field into view and focus its input. Preview again after changing the form.
6. Choose **Save role**. Incomplete templates can be saved and are marked **Needs configuration**. Editing uses **Save new version**, preserving previous definitions and resource selections. **View historical version** reopens an old version; saving its edited content creates another new version.

The preview shows the responsibility and persona content separately, with their sources. A persona reference stores its ID rather than embedding its prompt. Complete asset package import and export is available from the cards and toolbar in Assets → Teams / Workflows; see the [asset workspace](./agent-team-workflows.md).

Tool and skill availability follows the selected profile's enabled plugins, and skills also use that profile's runtime. Custom selections respect the selected persona's tool and skill allowlists. No persona is inherited when none is selected. An excluded resource receives a field diagnostic instead of being shown as ready. Built-in tools follow their own profile availability, as in the existing main Agent pipeline.

File write tools also follow the profile's `provider_settings.file_tool_mode`: `patch` (the default) exposes `astrbot_apply_patch`; `legacy` exposes `astrbot_file_write_tool` and `astrbot_file_edit_tool`. Missing or invalid values use `patch`; explicitly saved `legacy` settings still apply. Both local and sandbox runtimes use this rule. Inherited tools include only the selected group. An explicit dependency on the other group blocks preview and launch with a diagnostic on its field; it never selects a replacement tool or expands the role's allowlist. The resource catalog reports the selected mode for each profile.

Existing runs retain their original file tool mode after profile edits and process restarts. Recovery checks the frozen mode against current resource authorization: disabling the runtime or a required tool blocks the affected task. A saved snapshot cannot restore revoked access, and a tool with an unknown outcome still requires reconciliation or confirmation before replay.

File operations also obey the current administrator requirement and administrator IDs, read-only mode, additional write roots and deletion blacklist. These permissions can deny an operation from a saved plan while preserving its original tool group, sender and snapshot. Removing administrator access also constrains a member whose model request is already in progress.

The preview distinguishes internal checkpoints, remote queries and an external call boundary. Remote executors cannot execute AstrBot's local tools, skills or knowledge bases, or enforce local `max_steps`, tool timeout or context-length overrides. Explicit unsupported requirements receive field errors. Autonomous coordinators require local task/team tools. Missing remote credentials are reported against the profile without exposing their values. Unsupported model-level token allowances and output ceilings are rejected; outer call-step limits still apply.

Dify, Coze and DashScope Application receive the frozen role/persona framing as a prefix to the supported task or user input. The remote application's own system instructions remain configured on that service.

## Previous entries and the new entry

| Existing entry | Current entry and purpose |
| --- | --- |
| Agent Teams → Members | Assets → Role templates; create and configure roles directly. |
| Creating a reusable role | Agent Teams → Assets → Role templates → New role; templates are independent of legacy teams and members. |
| Workflow Editor, Run Monitor, History | Assets → Workflows pins role versions; Runs and History display frozen snapshots. |

See [Asset and run workspace](./agent-team-workflows.md) for scheduling, invocation recovery, human tasks and bounded loops. Role, readiness and launch checks use the same executor rules. Edits do not change existing runs' pinned roles and configuration; recovery still checks current access and remote binding compatibility.

Previews use current local resources. Historical definitions never silently fall back to a default model. Deleting or disabling a resource produces a diagnostic on the next preview. See the workspace instructions for existing run and recovery behavior.

Previous entry: **Role templates → Assets → Role templates**. Template identity is separate from runtime member state; editing a template does not change the fixed configuration of an existing run.

![Actual role overview and template identity](/images/agent-teams-roles-t25-en.jpg)



![The error path expands the limits section and focuses the invalid field](/images/agent-teams-role-error-focus-en.jpg)
