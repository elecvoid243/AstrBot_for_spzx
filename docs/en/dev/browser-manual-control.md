# Browser manual control protocol (H01–H05)

The existing `browser-use/status`, `snapshot`, `stream`, `stop`, and `close`
extensions retain their methods and ownership rules. `manual_control_version: 1`
announces support. Missing capability means read-only compatibility.

## Verified video and coordinate provenance (V02)

The updated private runtime enables `coordinate_contract_version: 2` for JPEG
and video. A new discrete target includes the opaque `coordinate_token` and
server-issued `frame_id` from decoded pixels. Omitting fields cannot downgrade
an enabled contract. Native frame/document/owner geometry and window, nested and
open/closed shadow scroll state are checked again before input. Unknown frames,
active View Transitions and bounded discovery failures freeze new input and can
recover. This does not lock arbitrary DOM semantics or promise atomic interaction
with arbitrary animation. Held drags keep their validated original anchor and
ordered moves; mouse/key release and reset use original input sessions even if
new pixels cannot be mapped. Each new native route/session lookup is bounded.

`video_stream_version: 1` and `frame_provenance_version: 1` advertise optional
video. `POST /api/v1/plugins/browser-media/session` takes the same owned browser
identity and Dashboard login as control and returns a single-use 15-second
ticket. The first same-origin `/api/v1/plugins/browser-media/ws` message is
`{ticket,type:"offer",sdp}`. Only the host selects the owner and viewer; public
extension requests cannot forge media context. Account credential version, chat,
plugin and actual browser admission are rechecked during the session. The socket
returns an SDP answer, capture epoch events, immutable sent-frame records and
heartbeats. It accepts `{operation:"heartbeat"}` every five seconds. Credentials
and private CDP/loopback addresses never reach media payloads or URLs.

Each peer uses `iceServers: []`. The pinned aiortc 1.14.0 adapter hooks only that
sender/transport, observes its negotiated VP8 payload type and actual final sent
RTP packet, and restores its hooks on close. The source is shared bounded CDP JPEG
capture/decode with independent off-thread VP8 encoders for up to four viewers.
Missing verified dependencies retain JPEG support; unknown adapters fail media
closed. No browser extension, public relay, audio or hardware encoder is required.

The receiver also requires native `VideoFrame` support to retain one checked picture without copying every frame to JPEG. The client matches `requestVideoFrameCallback().rtpTimestamp` and actual decoded
dimensions to the bounded media-session/epoch map. Records bind browser lifetime,
document, CSS viewport, scroll/full coordinate token, control epoch and processed
input watermark. Late/missing/ambiguous metadata cannot be replaced with the latest
SSE state. The watermark is a truthful lower bound for visible feedback, not a
requirement to recapture after every character: ordered text and shortcuts can
reuse an issued frame while its full coordinates remain current. Video targets also carry `media_session_id`, `media_epoch`,
`rtp_timestamp` and `capture_sequence`. RTP wrap is ordered by a separate bounded
media sequence. A stream subscription with `frames=0` receives state/actions only,
so video does not run a competing JPEG screencast. Snapshot tools remain available.
During a brief metadata gap, focused keyboard input keeps its original checked
picture and waits in the bounded ordered queue for a mapped frame. Coordinate
changes or blur discard that queue; terminal key release never waits for new pixels.
Pinch-scaled visual viewports freeze new input until scale returns to one.

Media faults preserve manual ownership. Explicit retry/JPEG fallback obtains
fresh verified pixels; navigation/capture epochs keep the peer and control socket.
Sidebar/chat/account changes, browser close, plugin disable or viewer disconnect
retire media resources; the last viewer stops capture and encoding. Normal stop
preserves the browser. Revoked access clears retained pixels. Final offline-package
installation and four-viewer resource stress are separate acceptance work.

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
4. Send ordered typed commands: `{operation: click, control_epoch, input_id,
   x, y, target}`. `target` contains `tab_id`, `document_id`, `viewport_width`,
   `viewport_height`, `scroll_x`, `scroll_y` from the displayed image. Coordinates
   map the actual image rectangle to remote CSS viewport pixels. The runtime
   resolves its own current target and compares every field before dispatch.
   There is no user-selected CDP target, command or script proxy. During human
   control, the existing consistent-snapshot capture path supplies bounded JPEGs
   over SSE at a target of no more than ten captures per second, including static pages.
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

## Recovery and lifecycle (H05)

Other windows show read-only occupancy and cannot steal the server-owned control
lease. Refresh, offline, hidden view, sidebar close, chat switch, page exit and
account reset discard unsent input and close its socket immediately. Account and
chat access are rechecked on every command, including heartbeats. If revoked access
prevents authorized disconnect cleanup, the independent 30-second private lease
still expires. Reconnect first reads server status and fresh pixels; takeover and
handback remain explicit. A cancelled task cannot restart.

Lost input confirmation means an unknown outcome. The UI stops, reports that the
action may have happened, and never replays it. Old socket replies cannot enable
input. During explicit handback, SSE may precede the socket acknowledgement; input
stays disabled while waiting for that confirmation.

Every revocation and terminal path releases keys and any possibly held mouse on
their original CDP sessions within a shared bounded deadline. Mouse cleanup releases
outside the document so it cannot complete an uncertain click on a submit target.
Failed releases remain tracked; acquisition retries cleanup, failed handback stays
paused, and confirmed close clears the destroyed lifetime. Host handback responses
are rechecked against cancellation, ownership and runtime/disable revisions before
renewing admission. Stop preserves Chromium, close/disable destroys it, and explicit
restart creates a new generation. Old credentials cannot revive the old lifetime.
Read-only snapshots recover silent screencast loss without restarting Chromium.
Native captures use one independent internal CDP connection/session bound to the
owned browser and target, outside the Agent's input-session pool and without device
emulation overrides. Target changes and shutdown release it; failed
captures discard it for a fresh attachment. This prevents an Agent session's stale
device metrics from resizing the page after an auxiliary debugger disconnects.

The production acceptance fixture supports `--recovery --pointer-input --text-input
--lost-key-target` with its normal arguments. It uses two independent logins and
injects network, lifecycle and acknowledgement faults through production ChatUI,
the installed plugin and private Chromium. This does not verify physical offline
installation or physical Windows IME behavior.


## Keyboard, composition and paste (H03)

The same ordered socket also accepts `key` (`phase: down|up`, `code`, `key`,
`modifiers` bitmask Alt=1/Ctrl=2/Meta=4/Shift=8, `repeat`), `text` (`text`,
`source: typing|composition|paste`) and `reset_keys`. Key and text commands carry
`input_id`, `control_epoch` and the displayed `target`, just like clicks.
`reset_keys` uses the ordered ID and epoch but releases the original CDP sessions,
so it works after a target change. The host schema and private runtime restrict
these messages; they are not a CDP proxy. Text is limited to 2048 characters and
4096 UTF-8 bytes, without NUL or invalid Unicode. Oversized text is rejected as a
whole. The client keeps at most 128 pending commands, waits for each acknowledgement
and discards queued content on blur, target change or handback. An uncertain result
closes client input; content is never automatically replayed in another view.

Clicking the image focuses a local composition textarea. Ordinary text uses
`beforeinput`/`insertText`; function keys and edit shortcuts use key down/up with
no second character event (Enter has its explicit newline semantics). Native IME
preedit and candidates stay local; `compositionend` commits once and cancellation
commits nothing. A visible preedit field appears while composing. Paste consumes
only a user-generated `paste` event's plain text; no clipboard polling or automatic
synchronization occurs. Remote Tab and Escape apply only while this textarea owns
focus, including fullscreen. The visible **Leave keyboard control** button returns
focus to local controls. **Hand back control** remains the explicit Agent handback.
Blur, invalid targets, disconnect, release, expiry and stop release held keys on
their original sessions. Input timelines contain categories only. Passwords, typed
text and paste payloads are not action summaries; the Agent can still read visible
page content after handback.

Run `scripts/browser_manual_control_acceptance.py --text-input` with its normal
plugin/runtime/dashboard/evidence arguments for authenticated production-host
acceptance. Its native Chromium CDP composition exercises real browser composition
events, but does not emulate a physical Windows IME candidate window or OS hotkeys.
Those platform interactions still require a human Windows IME check.

`keyboard_input_version: 1` announces keyboard/paste support separately from click control. Older plugins retain clicks and display a keyboard upgrade hint; older read-only clients ignore the new field.


## Pointer and feedback (H04)

`pointer_input_version: 1` independently announces wheel and double-click support.
`click` optionally carries `click_count: 1|2`; a native local double-click sends
one press/release with count 1 followed by count 2, without an extra duplicated
single click. `wheel` carries CSS `x/y`, `delta_x/delta_y`, the ordered ID, epoch
and displayed target. Deltas are finite CSS pixels bounded to ±2048; the client
normalizes DOM line and page modes before dispatch. Only adjacent wheel samples
at identical target and position can merge; clicks, key transitions and text are
never merged or placed in the replaceable image slot. There are at most 128
queued commands. Stale wheel samples are discarded rather than rebound; uncertain
results stop input and are never automatically replayed.

New targets include `position_revision`, sampled by a document scroll/resize
listener including nested scrollers. This revision is independent of image sequence
and control epoch. Legacy targets without the optional field remain compatible.
Snapshot capture compares the revision before/after taking pixels, rejects mixed
coordinate spaces, and scales images to at most 1920×1080 while retaining remote
CSS viewport dimensions. The existing 2 MiB JPEG/3 MiB event budgets remain.
Human capture waits 100 ms between captures (read-only mode keeps 250 ms); queues,
subscriber limits and deadlines remain bounded. Actual capture rate is machine-dependent.

`applied_input_id` in a frame is sampled before capture. The client distinguishes
queued/unacknowledged input from acknowledged input waiting for a later loaded
image of the same epoch containing that input ID. Identical JPEGs are reusable
only after the existing image has actually decoded. `data-frame-latency` exposes
the latest input-to-loaded-frame milliseconds for local acceptance; no payload is
logged. The authenticated production fixture supports `--pointer-input` and
records observed end-to-end latency and screenshots.


Stale wheel samples receive a consumed-ID `status: stale` acknowledgement, without dispatch or automatic replay. Human ownership remains active while the next image synchronizes. Other rejected/uncertain commands retain fail-closed behavior.

## Mouse dragging and explicit refresh

`drag_input_version: 1` advertises ordered `pointer` input with `phase: down|move|up|cancel`. A press validates the complete displayed target. Moves and release stay bound to the original tab, document and viewport while allowing drag-induced scrolling. Every queued move is retained in order; release is never dropped or gated on a new frame. Target changes, focus loss, cancellation and disconnect release the mouse on its original CDP session, without replaying uncertain input. `cancel` and `reset_keys` do not depend on stale frame coordinates.

Observation subscribes once on opening. Idle or disconnected clients never retry automatically; Refresh explicitly reauthorizes, captures and subscribes again. Lease heartbeats remain independent of observation refresh.
