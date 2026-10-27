# Agent workflow tool-result recovery

- Persist each built-in tool intent, start, original result and context application position with the active attempt checkpoint.
- Reuse saved tool results and continue partially completed batches without replaying unknown external effects. Database failures stop dependent work.
- Preserve partial generator output without declaring completion until execution ends. Upgrade original T07 saved responses at the same durable boundary without replanning.
- Preserve saved rejection results, applied positions and original ownership when an earlier result submission is already accepted.
- Rebuild complete temporary tool-output files before recovery continues, including saved results and already-applied context boundaries.
- Finish default, custom and file result submissions through the existing idempotent artifact protocol, including submissions mixed with other tool calls.
- Inspect tool states, complete results and execution ownership inside the selected task's saved process details. Navigation remains unchanged: Independent Runs → View Run → selected task.
- Confirmation and reconciliation interactions remain scoped to T09. T08 covers AC11/AC13 and the tool/submission portions of AC14/AC15/AC16.
