# Personal Chat session archives

- Keep the existing Chat session Export and Import sessions entries for both account roles. Their locations and the version-1 ZIP format are unchanged.
- Bind import previews and confirmation to the uploading account, prevent replay, and assign restored sessions, messages, attachments and side conversations new server identifiers.
- Remap quotes within their actual imported message stream. Do not reconnect an external branch origin or restore project membership, global configuration routes, filesystem grants or remote agent conversation identities; show import warnings for skipped data.
- Mark imported history as display data and remove server file authority. Preserve this provenance across another export/import or a branch.
- Clear archive previews and discard delayed upload, confirmation and download responses when switching accounts.
- Serve the authenticated archive response with an inline filename before ChatUI creates its download. This fixes empty downloads in environments that intercept attachment responses; direct ZIP downloads retain their filename and content.
