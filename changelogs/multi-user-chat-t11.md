# Multi-account full-instance recovery

- Keep Bot → Settings → Maintenance → Backup Manager as the administrator entry. Ordinary users retain Chat-only access; full backup and logs remain administrator operations.
- Include account identities, owners, profile preferences/bindings, projects, branches, side chats, attachments and managed workspaces in full backups. Preserve imported-history and native file-change authority metadata.
- Restore legacy single-account archives without granting access to unregistered server residue. Preserve modern account IDs, owners, roles, disabled state and credential versions, including data-only archives.
- Every full restore rotates the existing signing key and clears file tokens. Supported requests, streams and runtime tasks finish stopping before replacement; the old runtime stays stopped until restart. Password reset and disable keep their existing semantics.
- Previous flow: confirm import → restart. Current flow at the same entry: confirm import → sign in with the restored administrator → inspect actual result or retry → restart → sign in again. A 401 never indicates success. Failed/interrupted operations retain administrator retry and restart paths. After data replacement fails or is interrupted, restarting only restarts maintenance; the administrator must sign in and complete a retry with a valid backup before Chat can resume.
- Update English and Chinese full-account usage and recovery guidance, screenshots, OpenAPI and generated client contracts.
