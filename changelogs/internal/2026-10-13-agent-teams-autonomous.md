# Autonomous teams on the shared runtime

- Workflow Assets now supports versioned autonomous assets with a pinned coordinator, allowed role templates, instance/concurrency/task limits, an output contract and optional final version approval.
- Coordinators create independent member instances, dispatch tasks, wait without occupying active execution capacity, inspect full results and read immutable artifacts. Runtime authority and completion checks reject unauthorized decisions and premature success.
- Coordinators receive every declared launch input from the frozen run snapshot. Lightweight run overviews retain member identities and relationships without including complete role prompts; pending member waits use these overviews before reading complete results.
- Dynamic tasks use the existing scheduler, Agent main pipeline, frozen settings, result storage, controls and checkpoint recovery. Recovery retains same-member context across tasks and does not duplicate saved decisions or known external effects.
- Independent Runs displays dynamic members, task dependencies and separate dashed dispatch links. English and Chinese instructions include actual configuration/run screenshots and narrow-screen/theme checks.
- Entry mapping for the versioned path: team sidebar / Run Monitor → Workflow Assets → New Autonomous Team / Independent Runs → View Run. Existing legacy entries and records remain readable; this change does not perform migration, member messaging or nested asset execution.
