# Agent browser snapshots

- Add an Agent browser mode to the workspace Web preview tab. It displays the current chat's real Chromium viewport, address and title on demand.
- Entry mapping: the previous **Web preview** iframe and bookmarks are now **Web preview → Web preview**; the new observation mode is **Web preview → Agent browser**. Existing Git, files and terminal entries are unchanged.
- Requires the matching browser-use plugin with chat ownership and observation support. Another chat receives a busy response. Continuous streaming, action status and the entry without a loaded project follow in separate tickets.
