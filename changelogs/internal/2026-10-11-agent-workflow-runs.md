# Independent single-task workflow runs

- Add immutable workflow assets referencing pinned role versions, required text inputs and task templates.
- Persist independent run, task, member, attempt, message and conversation identities before dispatch through the existing Agent pipeline.
- Freeze effective configuration and persona content; reject unavailable bindings without substituting resources.
- Persist explicit default text submissions and finish accepted intents idempotently at saved safe points, including on core restart. Ordinary replies do not complete tasks.
- Add **Agent Teams → Workflow Assets → Start Run** and **Independent Runs** for persisted progress, read-only snapshots and complete results. Existing **Members**, **Workflow Editor**, **Run Monitor** and **History** retain their legacy behavior; there is no automatic migration.
- Add bilingual instructions and screenshots. Browser disconnection does not stop core execution; interrupted tasks are not replayed on restart. Invocation checkpoint recovery and structured output contracts remain subsequent work.
