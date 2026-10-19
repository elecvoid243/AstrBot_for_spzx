# Agent Teams dependency workflows

Workflow Assets now includes a componentized dependency canvas with pinned roles, member contexts, input/output references and typed field conditions. Ready successors start as soon as capacity is available, skipped condition paths do not block merges, and failures affect their dependents while independent branches continue. Task claims, accepted results and completion events use the existing persistent task protocol; duplicate notifications and abandoned executor results cannot overwrite accepted progress.

The canvas adds role drag/drop, zoom, fit, minimap, undo/redo and automatic layout. Independent Runs displays the saved execution graph and per-task status, configuration, attempts and artifact versions. Existing single-task assets remain supported; human tasks, bounded loops and invocation checkpoints remain assigned to later tickets.

| Previous entry | Current entry |
| --- | --- |
| Workflow Assets → New Workflow | Retained for single-task assets; use New Canvas Workflow for dependency graphs. |
| Legacy Workflow Editor | Retained; versioned graphs use Workflow Assets → Edit Asset. |
| Independent Runs → Submitted Result | Retained; graph runs use Inspect task to select a task's versioned output. |

English and Chinese workflow, role and WebUI instructions include the new mapping and screenshots of the actual canvas. Canvas editing and the read-only run graph are separate so version edits cannot change an existing execution.
