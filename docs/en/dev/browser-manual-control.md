# Browser manual control protocol (H01/H02)

The existing `browser-use/status`, `snapshot`, `stream`, `stop`, and `close`
extensions retain their methods and ownership rules. `manual_control_version: 1`
announces support. Missing capability means read-only compatibility.

1. `POST /api/v1/plugins/browser-control/session` with the ordinary Dashboard
   bearer login and `{chat_id, generation, browser_session_id}` validates the
   currently owned main ChatUI session, profile/plugin enablement, exact audited
   plugin handler and actual runtime/browser identity. Administrators use the
   same owned-chat rule as ordinary accounts. It returns `{ticket, expires_in: 15,
   websocket_path}`. API keys and caller-supplied owner/role/connection fields are
   rejected. A ticket is single-use and bound to the account credential version.
2. Open the returned same-origin WebSocket and send `{ticket}` within five seconds.
   No credential belongs in a URL or log. The host consumes the ticket, resolves
   the enabled persisted account, creates the trusted client identity and requests
   acquisition. A stopped browser or another controller is rejected. Acquisition
   first closes Agent admission and invalidates its current plan. The runtime
   cancels or drains in-flight tools and waits for dispatched page event handlers
   to settle before acknowledging human control; an unconfirmed drain fails closed.
3. Only an acknowledgement with `status: applied`, `operation: acquire` and a
   server-owned `control_epoch` enables input. `control_mode` is `agent`,
   `acquiring`, `human` or `paused`, independently of browser availability and
   `execution_state`. Epoch is distinct from observation frame `sequence`.
4. Send typed commands one at a time: `{operation: click, control_epoch, input_id,
   x, y, target}`. `target` contains `tab_id`, `document_id`, `viewport_width`,
   `viewport_height`, `scroll_x`, `scroll_y` from the displayed image. Coordinates
   map the actual image rectangle to remote CSS viewport pixels. The runtime
   resolves its own current target and compares every field before dispatch.
   There is no user-selected CDP target, command or script proxy. During human
   control, the existing consistent-snapshot capture path supplies bounded JPEGs
   over SSE at no more than four frames per second, including static pages.
   Frame and control-state slots remain separate so metadata cannot erase the
   only changed frame. Read-only screencast observation remains compatible.
5. Input IDs start at 1 and increase by one. The runtime consumes an ID before
   dispatch; retrying the last ID returns its recorded acknowledgement without
   dispatching again. Older or skipped IDs are rejected. A partial CDP failure
   yields `status: uncertain` and input must stop. Successful acknowledgements
   include `operation`, `input_id`, `control_epoch` and fresh metadata in `state`.
6. Send `{operation: heartbeat, control_epoch}` every five seconds. The host closes
   a silent socket after fifteen seconds. The private runtime revokes a silent
   controller after thirty seconds even if the host disappears. Disconnect closes
   admission and leaves `paused`; it never starts an Agent. Hidden views and
   observation failure close the client's input socket too.
7. `{operation: release, control_epoch}` drains prior input, invalidates cached
   element knowledge and refreshes the actual page before restoring Agent
   admission. Acknowledgement reports `state.control_mode: agent`; then the socket
   closes. Browser, tabs and login state survive. Model turns and retained browser
   tools explicitly reject while human control or paused admission is active.
   An existing local Agent turn waits in `PAUSED` outside ordinary tool deadlines.
   It cancels pending model reads, records truthful interrupted/unexecuted results
   for every invalidated tool call in a batch, and makes no model requests while
   waiting. Only explicit handback wakes this turn, with a fresh observation of
   tabs, page and interactive elements obtained by the private runtime. The
   observation reaches model context through the trusted host, never a client
   command. Old queued clicks are discarded. Cancellation, stop, close and disable
   remain terminal; handback cannot create a run or revive a completed one.

The host rechecks account credential version, owned chat, profile and plugin guard
on every command. The private bridge additionally checks trusted owner, socket,
runtime generation, browser lifetime, epoch, ordered input and current page target.
Its random loopback bearer credential is never delivered to the frontend. The
legacy extension URL `/plugins/extensions/browser-use/manual` cannot accept public
control bodies; only host-created request context can enter the audited handler.

H03 keyboard/composition/paste; H04 scrolling,
double-click and feedback; H05 full lifecycle and multiwindow recovery. They must
retain these identity, acknowledgement and explicit-handback contracts.
