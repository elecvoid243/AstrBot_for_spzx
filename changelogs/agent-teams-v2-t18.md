# Agent Teams v2: executor capabilities and remote recovery

- Role configuration, asset readiness and both orchestration launches share actual runner capability checks. Unsupported local tools/settings, required recovery protocols and remote model-level limits produce field diagnostics.
- Persist remote execution identities and original complete output. Recovery queries Dify workflow, saved-history Coze and current DeerFlow exact-run interfaces; unknown outcomes never recreate the original remote operation.
- Keep ordinary remote text separate from explicit contracted task results. Preserve provider thinking, signatures and reported totals without inventing missing token components.
- Preserve reply budgets across observed pause and capacity-wait polling transitions.
- Retain original Dify Agent thought/tool observation snapshots in the selected task's process records, including later observations under the same thought ID. Duplicate frames and recovery do not duplicate records or create local tool checkpoints.
- Send team DashScope creation requests once; ambiguous disconnects and redirects require confirmation instead of transparent POST replay. Ordinary chat SDK behavior is unchanged.
- Require the already locked DashScope SDK 1.27.7 or newer so team requests use the supported request-scoped transport instead of serializing a session as API input.
- Apply that SDK minimum to both `pyproject.toml` and the documented `requirements.txt` installation/upgrade path.
- Preserve Dify workflow/chatflow node inputs, process data, outputs, execution/iteration identities, agent logs and supplied reasoning. Repeated snapshots are idempotent; unsequenced reasoning deltas retain arrival order. Observations remain distinct from local tool checkpoints.
- Previous action **Continue the same attempt** → **Query original remote run** for compatible remote executors; built-in checkpoint recovery keeps its existing action. The entry remains **Independent Runs → View Run → select task**.
- Include the conditional recovery-action mapping in both central WebUI navigation references.
- Before launch: **Workflow Assets → Start Run → Check readiness**. Role requirements remain under **Role templates → Edit role → Model**, with the effective executor shown in the preview. No navigation entry moves.
- Update English/Chinese instructions and actual WebUI screenshots. Complete asset import/export remains outside T18.
