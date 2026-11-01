# Multi-account Chat tool consumption

- Restore the existing Skills, managed-terminal, project-status and workspace consumption entry points for ordinary accounts, while keeping shared service, plugin and permission-root management administrator-only.
- Authorize terminal output/control and skill queues through trusted account and owned conversation identities. Clear terminal, skill and extension state when changing accounts.
- Bind file diff/status/undo to the original owned bot message or active run, including recorded canonical path, backup and content checksum. Imported histories never grant filesystem authority; stale cards cannot read replacement contents.
- Restore deletions only through the exact recycle metadata/object identity and original file or directory evidence. Ambiguous, unavailable and legacy records remain visible without undo; no fallback selects the newest same-path deletion.
- Require ownership-aware markers on actually loaded spcode and Browser Use consumer handlers across official and compatibility APIs. Restrict spcode filesystem consumers to the resolved owned workspace, redact global activity counts, and download Git exports through owned attachments.
- Entry mapping: Skills management list -> owned skill inventory; path-only file actions -> source message/run; newest-path recycle restore -> exact recorded object; server-open Git revision export -> account attachment download.
- Load personal project directories from the server-resolved workspace, respect the profile's existing plugin enablement, stop old operation polling on account changes, and preserve empty cached responses.
- Update the host and both plugins together. Account admission does not grant broad plugin management scopes. Existing independent API keys and non-Dashboard plugin calls retain their semantics; this does not add per-user execution sandboxes.
