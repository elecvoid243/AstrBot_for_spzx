# Agent Teams v2 human requests

- Canvas assets can configure human questions and exact-version approvals. Durable requests retain constraints, task/Attempt references, responses and single-consumption evidence.
- Human waits release active capacity and block only dependent paths. Pause accepts and saves responses for continuation; cancellation and retry retain request history.
- Approval previews and exports use the same immutable reference. Revisions do not inherit approval or trigger downstream reruns. Files governed by approval cannot be downloaded from an unapproved version.
- Previous entry → current entry: no persistent human entry in v2 workflows → Workflow Assets → Edit Asset → Human question / Version approval; Independent Runs → View Run → Human requests. Legacy Workflow Editor entries remain separate.
