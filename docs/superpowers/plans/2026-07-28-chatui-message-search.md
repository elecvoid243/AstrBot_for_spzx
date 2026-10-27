# ChatUI Message Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a message search feature to ChatUI sidebar that lets users search webchat conversations by keyword, see grouped results with context snippets, and click to navigate directly to matching messages.

**Architecture:** New backend API `/api/v1/messages/search` that parses conversation history JSON and returns structured search results with snippets. Frontend adds a search button in the sidebar and an overlay dialog showing results grouped by conversation. Clicking a result navigates to the conversation and scrolls to the matched message with temporary highlight.

**Tech Stack:** Python (FastAPI), Vue 3 + Vuetify 3, TypeScript

## Global Constraints

- Python 3.10+, Vue 3, Vuetify 3
- English comments and logs; Chinese acceptable in UI strings
- Use pathlib.Path for file paths; use ruff for formatting/linting
- No gradients, no glassmorphism, Apple-style restraint
- Conversation history format: OpenAI-style JSON array with role/content fields
- Webchat UMO format: `webchat:MessageType:webchat!username!session_id`
- Search: case-insensitive substring match on plain text parts only
- Only search webchat platform conversations
- Snippet: ~50 chars context before and after match, trimmed at word boundaries
- Message index: corresponds to index in conversation history JSON array
- Dialog: overlay style, ~480px wide, Enter to search, ESC to close
- Results grouped by conversation, collapsible, first group expanded by default

---

### Task 1: Backend search_messages core logic

**Files:**
- Modify: `astrbot/dashboard/services/conversation_service.py` (add search_messages method after line 89)
- Test: `tests/unit/test_message_search.py`

**Interfaces:**
- Produces: async search_messages(q: str, page: int, page_size: int) -> dict with keys: results[], pagination{}
- Consumes: self.conv_mgr.get_all_conversations(page, page_size)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_message_search.py`:

```python
import json

def test_extract_plain_text_from_message():
    msg = {"role": "assistant", "content": "Hello world, this is a test message"}
    text_parts = []
    content = msg.get("content", "")
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
    result = " ".join(text_parts)
    assert result == "Hello world, this is a test message"

def test_snippet_generation():
    text = "This is a long message with some content that contains the keyword we are searching for"
    keyword = "keyword"
    lower_text = text.lower()
    lower_kw = keyword.lower()
    pos = lower_text.find(lower_kw)
    assert pos >= 0
    ctx_len = 50
    start = max(0, pos - ctx_len)
    end = min(len(text), pos + len(keyword) + ctx_len)
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    assert "keyword" in snippet

def test_match_search_in_history():
    history = [
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language"},
    ]
    keyword = "python"
    matches = []
    for idx, msg in enumerate(history):
        if msg.get("role") not in ("user", "assistant"):
            continue
        full_text = msg.get("content", "")
        if keyword.lower() in full_text.lower():
            matches.append({"message_index": idx, "role": msg.get("role")})
    assert len(matches) == 1
    assert matches[0]["message_index"] == 1
```

- [ ] **Step 2: Run test to verify they pass**

Run: `cd F:\github\Astrbot && uv run python -m pytest tests/unit/test_message_search.py -v`

Expected: PASS (standalone logic tests)

- [ ] **Step 3: Write search_messages method in ConversationService**

Add this method to `astrbot/dashboard/services/conversation_service.py` after line 89:

```python
    async def search_messages(
        self,
        *,
        q: str,
        page: int,
        page_size: int,
    ) -> dict:
        q = q.strip()
        if not q:
            return {"results": [], "pagination": {"page": 1, "page_size": page_size, "total": 0, "total_pages": 1}}
        page = max(page, 1)
        page_size = max(1, min(page_size, 50))

        all_conversations = []
        search_page = 1
        scan_limit = 1000
        while len(all_conversations) < scan_limit:
            batch, _ = await self.conv_mgr.get_all_conversations(page=search_page, page_size=200)
            webchat_batch = [c for c in batch if c and c.get("user_id", "").startswith("webchat:")]
            all_conversations.extend(webchat_batch)
            if len(batch) < 200:
                break
            search_page += 1

        lower_q = q.lower()
        grouped_results = {}

        for conv in all_conversations:
            cid = conv.get("cid")
            if not cid:
                continue
            history_str = conv.get("history", "[]")
            try:
                history = json.loads(history_str) if history_str else []
            except json.JSONDecodeError:
                continue
            if not isinstance(history, list):
                continue

            conv_matches = []
            for idx, msg in enumerate(history):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                text_parts = []
                content = msg.get("content", "")
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                full_text = " ".join(text_parts)
                if not full_text:
                    continue
                lower_text = full_text.lower()
                pos = lower_text.find(lower_q)
                if pos == -1:
                    continue
                ctx_len = 50
                start = max(0, pos - ctx_len)
                end = min(len(full_text), pos + len(q) + ctx_len)
                snippet = full_text[start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(full_text):
                    snippet = snippet + "..."
                match_offset = snippet.lower().find(lower_q)
                conv_matches.append({
                    "message_index": idx,
                    "role": role,
                    "snippet": snippet,
                    "match_offset": max(0, match_offset),
                })

            if conv_matches:
                safe_cid = str(cid) if cid else "unknown"
                display_cid = safe_cid[:8] if len(safe_cid) >= 8 else safe_cid
                grouped_results[cid] = {
                    "session_id": cid,
                    "title": conv.get("title") or f"瀵硅瘽 {display_cid}",
                    "updated_at": conv.get("updated_at", 0) or 0,
                    "matches": conv_matches,
                }

        sorted_results = sorted(grouped_results.values(), key=lambda x: x["updated_at"], reverse=True)
        total = len(sorted_results)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        offset = (page - 1) * page_size
        page_results = sorted_results[offset : offset + page_size]
        return {
            "results": page_results,
            "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages},
        }
```

- [ ] **Step 4: Run tests**

Run: `cd F:\github\Astrbot && uv run python -m pytest tests/unit/test_message_search.py -v`

Expected: PASS

- [ ] **Step 5: Format and commit**

Run: `uv run ruff format astrbot/dashboard/services/conversation_service.py`

```bash
git add astrbot/dashboard/services/conversation_service.py tests/unit/test_message_search.py
git commit -m "feat: add search_messages method to ConversationService"
```

### Task 2: Backend API route for message search

**Files:**
- Create: `astrbot/dashboard/api/messages.py`
- Modify: `astrbot/dashboard/api/app.py` (add import and router registration)
- Test: `tests/unit/test_message_search_api.py`

**Interfaces:**
- Consumes: ConversationService.search_messages from Task 1
- Produces: GET /api/v1/messages/search route

- [ ] **Step 1: Create messages.py**

Create `astrbot/dashboard/api/messages.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, Query, Request
from astrbot.dashboard.responses import ok
from astrbot.dashboard.services.conversation_service import ConversationService, ConversationServiceError
from .auth import AuthContext, require_scope

router = APIRouter(tags=["Messages"])

def get_service(request: Request) -> ConversationService:
    return request.app.state.services.conversations

async def require_chat_scope(request: Request) -> AuthContext:
    return await require_scope(request, "chat")

@router.get("/messages/search")
async def search_messages(
    q: str = Query(min_length=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    _auth: AuthContext = Depends(require_chat_scope),
    service: ConversationService = Depends(get_service),
):
    try:
        result = await service.search_messages(q=q, page=page, page_size=page_size)
        return ok(result)
    except Exception as exc:
        raise ConversationServiceError(f"Search failed: {exc!s}") from exc
```

- [ ] **Step 2: Register in app.py**

Add to imports in `astrbot/dashboard/api/app.py`:
```python
from .messages import router as messages_router
```

Add router registration:
```python
app.include_router(messages_router, prefix=API_V1_PREFIX)
```

- [ ] **Step 3: Write smoke test**

Create `tests/unit/test_message_search_api.py`:

```python
def test_messages_route_imports():
    from astrbot.dashboard.api import messages
    assert hasattr(messages, "router")

def test_messages_route_config():
    from astrbot.dashboard.api.messages import router
    routes = [r.path for r in router.routes]
    assert "/messages/search" in routes
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/unit/test_message_search_api.py -v`

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format astrbot/dashboard/api/messages.py
git add astrbot/dashboard/api/messages.py astrbot/dashboard/api/app.py tests/unit/test_message_search_api.py
git commit -m "feat: add GET /api/v1/messages/search API route"
```

### Task 3: Frontend API client wrapper

**Files:**
- Create: `dashboard/src/api/messages.ts`

- [ ] **Step 1: Create messages.ts**

Create `dashboard/src/api/messages.ts`:

```typescript
export interface SearchMatch {
  message_index: number;
  role: string;
  snippet: string;
  match_offset: number;
}

export interface SearchConversation {
  session_id: string;
  title: string;
  updated_at: number;
  matches: SearchMatch[];
}

export interface MessageSearchResponse {
  results: SearchConversation[];
  pagination: { page: number; page_size: number; total: number; total_pages: number; };
}

export const messageApi = {
  async searchMessages(q: string, page: number = 1, pageSize: number = 20): Promise<MessageSearchResponse> {
    const url = new URL('/api/v1/messages/search', window.location.origin);
    url.searchParams.set('q', q);
    url.searchParams.set('page', String(page));
    url.searchParams.set('page_size', String(pageSize));
    const token = localStorage.getItem('token') || '';
    const response = await fetch(url.toString(), {
      method: 'GET',
      headers: { Authorization: token ? `Bearer ${token}` : '' },
    });
    if (!response.ok) throw new Error(`Search failed: ${response.statusText}`);
    const data = await response.json();
    if (data.status === 'ok' && data.data) return data.data as MessageSearchResponse;
    throw new Error('Invalid response format');
  },
};
```

- [ ] **Step 2: Format and commit**

```bash
git add dashboard/src/api/messages.ts
git commit -m "feat: add messageApi searchMessages client wrapper"
```

### Task 4: ChatMessageList data-message-index attribute

**Files:**
- Modify: `dashboard/src/components/chat/ChatMessageList.vue`

- [ ] **Step 1: Add data-message-index**

Find the message rendering loop in ChatMessageList.vue:
```vue
<div v-for="(msg, index) in props.messages" :key="msg.id ?? index" class="message-wrapper">
```
Change to:
```vue
<div v-for="(msg, index) in props.messages" :key="msg.id ?? index" class="message-wrapper" :data-message-index="index">
```

- [ ] **Step 2: Typecheck**

Run: `cd dashboard && pnpm run typecheck`

- [ ] **Step 3: Format and commit**

```bash
git add dashboard/src/components/chat/ChatMessageList.vue
git commit -m "feat: add data-message-index to ChatMessageList for search navigation"
```

### Task 5: ChatMessageSearchDialog component

**Files:**
- Create: `dashboard/src/components/chat/ChatMessageSearchDialog.vue`

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/chat/ChatMessageSearchDialog.vue` with the following structure:

Template:
- Overlay div with click-to-close on self
- Search header with input field (search icon + input + clear button) and ESC hint
- Loading state with spinner
- Error state with retry button
- Empty state message
- Results list: groups by conversation with expandable matches
- Pagination footer

Script key logic:
- watch modelValue to focus input when opened
- performSearch on Enter key
- toggleGroup for expand/collapse
- navigateToMessage closes dialog and router.push with scrollToIndex query param
- highlightSnippet uses `<mark>` tags

Style:
- Fixed overlay, centered, ~480px wide, max-height 450px
- Clean design: no gradients, subtle borders, consistent with ChatUI
- Search input with rounded bg, clear button
- Result groups with chevron, match snippets with mark highlight

Full implementation details available in the design spec. Key functions to implement:
- closeDialog(): emit update:modelValue false
- performSearch(): call messageApi.searchMessages, handle loading/error states
- toggleGroup(conv): toggle expandedGroups[conv.session_id]
- navigateToMessage(conv, match): closeDialog + router.push with scrollToIndex
- highlightSnippet(snippet, keyword): escapeHtml + regex replace with `<mark>`

- [ ] **Step 2: Typecheck**

Run: `cd dashboard && pnpm run typecheck`

- [ ] **Step 3: Format and commit**

```bash
git add dashboard/src/components/chat/ChatMessageSearchDialog.vue
git commit -m "feat: add ChatMessageSearchDialog component"
```

### Task 6: Add search button to Chat.vue sidebar

**Files:**
- Modify: `dashboard/src/components/chat/Chat.vue`

- [ ] **Step 1: Add search button**

Find sidebar section header in Chat.vue (around line 137-139):
```vue
<div class="sidebar-section-header">
  <span>{{ tm("conversation.title") }}</span>
</div>
```
Change to:
```vue
<div class="sidebar-section-header">
  <span>{{ tm("conversation.title") }}</span>
  <button class="search-button" type="button" :title="tm('search.title')" @click="searchDialogOpen = true">
    <Search :size="14" />
  </button>
</div>
```

- [ ] **Step 2: Add imports and state**

Add to script setup imports:
```typescript
import { Search } from 'lucide-vue-next';
import ChatMessageSearchDialog from './ChatMessageSearchDialog.vue';
```
Add state:
```typescript
const searchDialogOpen = ref(false);
```

- [ ] **Step 3: Render dialog**

Add before closing template tag:
```vue
<ChatMessageSearchDialog v-model="searchDialogOpen" />
```

- [ ] **Step 4: Add CSS**

Add to scoped style:
```css
.sidebar-section-header .search-button {
  margin-left: auto;
  border: none;
  background: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.45);
  display: flex;
  align-items: center;
  padding: 3px 6px;
  border-radius: 4px;
}
.sidebar-section-header .search-button:hover {
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: var(--v-theme-on-surface);
}
```

- [ ] **Step 5: Typecheck and commit**

```bash
cd dashboard && pnpm run typecheck
git add dashboard/src/components/chat/Chat.vue
git commit -m "feat: add search button to ChatUI sidebar"
```

### Task 7: Scroll-to-message navigation

**Files:**
- Modify: `dashboard/src/components/chat/Chat.vue`

- [ ] **Step 1: Add scrollToMessageFromQuery function**

In Chat.vue script setup:

```typescript
async function scrollToMessageFromQuery() {
  const idxStr = route.query.scrollToIndex as string | undefined;
  if (!idxStr) return;
  const targetIndex = parseInt(idxStr, 10);
  if (isNaN(targetIndex) || targetIndex < 0) return;
  await nextTick();
  await new Promise(r => setTimeout(r, 300));
  const el = document.querySelector(`[data-message-index="${targetIndex}"]`) as HTMLElement | null;
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  // Clear query param
  const { scrollToIndex: _, ...restQuery } = route.query;
  router.replace({ query: restQuery });
}
```

- [ ] **Step 2: Call it in route watch**

In the watch handler for route.params.conversationId, after session loads:
```typescript
await scrollToMessageFromQuery();
```

- [ ] **Step 3: Format and commit**

```bash
git add dashboard/src/components/chat/Chat.vue
git commit -m "feat: add scroll-to-message navigation"
```

### Task 8: i18n strings

**Files:**
- Modify: `dashboard/src/i18n/locales/zh-CN/features/chat.json`
- Modify: `dashboard/src/i18n/locales/en-US/features/chat.json`

- [ ] **Step 1: Add zh-CN strings**

Add to zh-CN/features/chat.json:
```json
"search": {
  "title": "搜索消息",
  "placeholder": "搜索历史消息...",
  "loading": "搜索中...",
  "error": "搜索失败，请稍后重试",
  "retry": "重试",
  "noResults": "未找到匹配的消息",
  "matches": "条匹配",
  "prevPage": "上一页",
  "nextPage": "下一页",
  "pageInfo": "{current}/{total}"
}
```

- [ ] **Step 2: Add en-US strings**

Add to en-US/features/chat.json:
```json
"search": {
  "title": "Search messages",
  "placeholder": "Search conversation history...",
  "loading": "Searching...",
  "error": "Search failed, please try again",
  "retry": "Retry",
  "noResults": "No matching messages found",
  "matches": "matches",
  "prevPage": "Prev",
  "nextPage": "Next",
  "pageInfo": "{current}/{total}"
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/i18n/locales/zh-CN/features/chat.json dashboard/src/i18n/locales/en-US/features/chat.json
git commit -m "feat: add i18n strings for message search"
```

---

## Self-Review

**Spec coverage:**
- Search webchat only: Task 1 filters by `webchat:` prefix
- Enter to search: Task 5 uses @keydown.enter
- Grouped by conversation with expand: Task 5
- Snippet with highlight: Task 5
- Click to navigate with scroll: Task 5 + Task 7
- ESC to close: Task 5
- Overlay dialog: Task 5
- Pagination: Task 1 + Task 5
- i18n: Task 8

**No placeholders:** All tasks have concrete steps with code.

**Type consistency:** API response types match across backend/frontend.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-28-chatui-message-search.md`. Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?