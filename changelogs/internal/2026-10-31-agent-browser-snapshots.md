# Agent browser observation

- Add an Agent browser mode to the workspace Web preview tab. It displays the current chat's real Chromium viewport, address, title and action results, starting with a snapshot and continuing with live updates.
- Entry mapping: the previous **Web preview** iframe and bookmarks are now **Web preview → Web preview**; the new observation mode is **Web preview → Agent browser**. Existing Git, files and terminal entries are unchanged.
- Entry mapping without a project: **no workspace entry → Agent browser above the composer**. With a loaded project, **Workspace → Web preview → Agent browser** remains available. Unloading a project keeps the active Agent browser open.
- Recover observation automatically after a temporary disconnect, using a fresh authorized snapshot before action increments. Clear the old image and subscription on chat switches, reject repeated/older events, and resynchronize explicitly after cursor expiry or a changed runtime. Only the latest image is fetched; the same browser and login state survive observation recovery.
- Show distinct idle, busy, disabled, disconnected and reconnecting states. Requires the matching browser-use plugin with chat ownership, streaming and action support.
