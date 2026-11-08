## Remote rich message display

- Replace simplified remote rendering with shared local HTML previews, Skill requests, file review/reference cards and the full interactive choice widget, including descriptions, supplementary Markdown, details folds and persisted answer/cancellation states.
- Keep remote choice transport scoped to the authorized connection/session, including cancellation, without sharing local submission state.
- Restore native IPython results, branch markers, public source icons and audio/video playback. Inline attachment images use scoped remote reads instead of local resource URLs.
- Remove remote attachment download buttons and native-player download actions. Preserve the local attachment controls and omit remote configuration/workspace/write actions.
- Update English and Chinese WebUI instructions and screenshots. Existing remote entry points remain unchanged.
