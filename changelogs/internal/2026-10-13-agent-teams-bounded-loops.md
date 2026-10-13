# Bounded review and revision loops

- Workflow Assets now configure disjoint task bodies, typed exit conditions, finite iteration limits and explicit previous-round feedback in collapsible canvas groups.
- Each round has independent Tasks, Attempts, frozen artifact references and process/usage history. Retries and checkpoint recovery retain their existing meanings.
- Consumers of an executed body wait for a passing exit. Reaching the bound without passing requires attention and cannot produce a successful final result. Pause, cancellation, local waits and process recovery use the existing scheduler.
- A wholly unselected first-round body remains explicitly skipped without an exit artifact. Active branches still complete their merges; executed, failed or exhausted loops retain their result gates.
- Version approval after a loop pins the passing round's concrete artifact; export reads that approved reference. Earlier reviews and approvals remain immutable, and later manual artifacts do not rerun consumers.
- Previous entry → new entry: ordinary task dependencies for revision → Workflow Assets → Edit Asset → Add bounded loop; Independent Runs → View Run → Loop iterations. Legacy workflow entry points remain available.

This implements T12 / #14 only. Autonomous orchestration, nested teams/workflows and cross-machine asset packages remain separate tasks; the parent specification and composite acceptance criteria are not marked complete.
