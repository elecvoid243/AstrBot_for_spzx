# Unknown tool result handling

- Unknown external effects recover only through guarantees declared by the tool adapter before execution: an authoritative query, an idempotency key or explicit safe retry.
- Verified results, verified failures and explicit new executions are durable, owner-scoped actions. Original evidence, Attempt, frozen configuration and replacement links remain readable; duplicate decisions and stale executors cannot replace current work.
- Independent workflow paths continue while an affected task waits. Saved output and result submission retain the existing per-call checkpoint behavior.
- Entry mapping: the unknown-call waiting reason in **Independent Runs → View Run** now leads to **Saved member process → expand the unknown call → reconcile / record a verified outcome / explicitly request a new execution**. Existing navigation remains available.
- This change covers T09 / #11 and the unknown-tool portions of US26/30/31/34 and AC12/15/16. It does not complete the composite parent acceptance criteria or promise exactly one effect for arbitrary external tools.
