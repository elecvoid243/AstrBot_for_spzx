# Agent Teams v2 run controls

- Run details now persist whole-run pause, continue and cancellation, sharing admission and execution fences with existing graph tasks. In-flight external effects remain visible and are not described as undone.
- Explicit failed-task retry creates a new Attempt with the original frozen inputs and artifact versions, retaining prior failure, checkpoint, usage and process evidence. Unknown tool effects require the existing resolution protocol first.
- Controls and retry requests survive restart and deduplicate repeat submissions. Graph retries retain capacity and member exclusions; late executors cannot overwrite current state.
- Evidenced tool decisions can be saved during a whole-run pause and wait for continuation, including a pause racing with the decision commit. Normal shutdown preserves later control and resume decisions without replacing them with stale execution state.
- Previous entry → current entry: checkpoint-only run details → Agent Teams → Independent Runs → View Run → Whole run controls / select failed task → Retry failed task. Existing checkpoint recovery and tool resolution remain inside task details.
