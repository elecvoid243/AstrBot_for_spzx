# Owned Chat configuration and input choices

- Ordinary accounts can choose administrator-created profiles, enabled chat models, thinking effort, and existing quick messages in the current Chat UI.
- The existing configuration chip now uses owned-session consumption endpoints instead of configuration management APIs. Only profile IDs, names and runner types, and model IDs/names/source IDs, are returned.
- Explicit WebChat conversation bindings, including `default`, override wildcard routes. Sending validates and applies the chosen profile before queuing the first message.
- Thinking-effort preset selections and values now persist per account in the existing preferences table. Shared preset definitions and provider/profile editing remain administrative actions.
- Configuration defaults and remembered models are isolated by account. Existing conversation bindings and administrator configuration management remain available.
- New API contracts: `GET /api/v1/chat/configs`, `GET /api/v1/chat/models`, and `GET/PUT /api/v1/chat/sessions/{session_id}/config`. Chat sends accept validated `config_id`, `thinking_effort`, and existing configured model selections.
- Input mapping: configuration chip stays in the composer; thinking-effort selection changes from the shared default to an account choice. Side conversations can reuse the owned-session contract when that feature is enabled for ordinary accounts.
