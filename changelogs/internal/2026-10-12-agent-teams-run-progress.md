# Agent Teams: persistent run progress and member process

- Run snapshots now read graph tasks, artifacts, complete process records and a monotonic event cursor consistently. State transitions and their events commit together. Reconnection deduplicates observations and reloads snapshots for expired cursors.
- Complete provider responses, available thinking/signatures and original tool results remain associated with a run, task, attempt and member. Process records do not serve as execution checkpoints.
- Independent Runs → View Run now starts with the goal, task progress, waiting reasons and members. Select a task for artifacts and expandable process records. The former default graph moves to Expand execution graph (read-only); reply JSON moves to Saved member process. Artifact version and file controls remain available.
- Chinese and English workflow instructions include the entry mapping and actual desktop, dark and narrow-screen screenshots. Closing the page does not stop core execution; invocation-boundary recovery remains a separate task.
