# Coordinator calls to fixed workflow versions

- Autonomous assets explicitly allow saved workflow versions and declare their return task. The coordinator can call only these visible versions with validated required inputs and exact artifact references.
- Calls share the existing scope scheduler, immutable artifacts, root capacity, role limits, bounded depth, persistent waits, controls and recovery. Concurrency one can complete mixed nesting without sharing member conversations or replaying saved tool effects.
- **Workflow Assets → New Autonomous Team / Edit Asset → Allowed workflows** adds configuration. **Independent Runs → View Run → select coordinator → View child workflow** replaces the previous member-dispatch-only route for these calls and shows actual child tasks and exact return references. Existing team calls and legacy entry points retain their existing navigation.
- Chinese and English workflow instructions and actual WebUI screenshots cover fixed workflow configuration and result tracing.
- Independent workflow rows preserve concurrent pinned-version choices, and nested team coordinators expose the same child-workflow navigation.
- Explicit artifact handoffs validate the active Attempt and executor, committing read evidence with the workflow call. Failed reads leave no dependent call, and child tools retain the complete pinned text and file version after recovery.
