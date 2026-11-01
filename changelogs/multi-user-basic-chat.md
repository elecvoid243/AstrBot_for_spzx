# Multi-account basic ChatUI

- Add administrator-managed ordinary accounts with unique logins and initial credentials.
- Ordinary accounts enter the existing Dashboard's Chat route and use the shared chat history and streaming logic for their own default-profile text conversations. Unadapted operations remain denied by the server and hidden in the UI.
- Enforce WebChat ownership in daily administrative conversation/session views while retaining full operational backups and logs.
- Clear account-bound drafts and mounted state, cancel old requests, and reject stale responses when signing out or switching accounts.
- Bind account-generated download tokens and the WebChat pipeline to trusted server identity; independent API keys/platform capabilities retain their existing flows.
- Previous entry → current entry: the original WebUI login remains the administrator entry with Bot/Chat; shared administrator chat usage moves to administrator-created ordinary accounts signing in on that same page directly into their own Chat. Account creation is available under Bot → Accounts → Create account.
- Keep the ordinary user's sidebar button available on narrow screens for conversation history, new chats, and logout.
- Preserve administrator edited continuations and retries by checking the saved user turn, support owned workflow-style short WebChat origins, and retain explicit API key precedence over Dashboard cookies.
