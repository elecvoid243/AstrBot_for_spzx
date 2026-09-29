# interactive_choice 终态持久化（Bug Z 修复 + 答案落库）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| | |
|---|---|
| **日期** | 2026-09-29 |
| **分支** | `fix/interactive-choice-resolved-durability` |
| **状态** | 已实现（合入 `all`: `3c77de8`..`7ae9005`） |
| **范围** | `dashboard/src/stores/interactiveChoice.ts`、`dashboard/src/composables/parseInteractiveChoice.ts`、`dashboard/src/components/chat/`、`astrbot/dashboard/services/chat_service.py`、`data/plugins/astrbot_plugin_ask_user_choice/` |

**Goal:** 让交互选项框的终态（已选择 / 已取消）在刷新、AstrBot 重启、清缓存、换浏览器之后依然成立——前端按会话隔离写回 localStorage，后端把答案写进历史 part，使历史记录本身成为终态的权威来源。

**Architecture:** 三层各自解决一件事。① 前端 store 的 `persist*` 改成「读—改—写」，只替换当前 UMO 的切片，修掉跨会话覆盖删除（Bug Z）。② 插件在 `interactive_choice_resolved` 事件上带上答案，`chat_service` 收到后把 `answer` / `resolved` 戳进本轮已落盘的 `interactive_choice` part 并重写该历史行。③ 前端状态机在本地记录缺失时读 part 上的戳；历史加载完成后补一次 `reconcile`，让「后端已无 pending 且历史无答案戳」的框落到终态而不是可点击的 pending。

**Tech Stack:** Vue 3 + Pinia + TypeScript（dashboard，测试用 `node --test` 与 Vitest）、Python 3.10+ / FastAPI（`astrbot/dashboard/services/chat_service.py`，测试用 pytest）、`astrbot_plugin_ask_user_choice`（pytest）。

**Spec:** 本文件 §1（2026-09-29 排查结论，代号 Bug Z）。该结论取代「用户意图只存在浏览器 localStorage」这一隐含设计——它是本计划要改掉的东西。

---

## Global Constraints

- 注释与日志语言跟随所在文件：`astrbot/` 与 `dashboard/src` 下新增注释/日志用**英文**（根 `AGENTS.md`）；`data/plugins/astrbot_plugin_ask_user_choice/` 下用**中文**（该目录自带 `AGENTS.md`，且相邻代码是中文）。
- 所有新增函数写 Google 风格 docstring（`Args:` / `Returns:` / `Raises:`）。
- 不新增依赖；不改 REST 契约（`/api/chat/interactive-choice/*` 的请求/响应形状不变），因此**不需要**跑 `pnpm generate:api`。
- 改 SSE 事件 payload 与历史 part schema 属前后端同步发布项：插件与 dashboard 必须一起升级（沿用 `2026-07-05-interactive-choice-history-roundtrip.md` 的约定）。
- KISS + inline-first：本计划只新增两个函数——`persistSlice`（4 个 persist 共用，满足复用门槛）与 `stamp_choice_resolution`（`_consume_chat_run` 已远超 50 行，满足复杂度门槛）。其余改动一律就地修改。
- Python 收尾：`uv run ruff format <paths> && uv run ruff check <paths>`。
- 提交信息用 conventional commits。

## Review Focus

按「最可能咬到人」排序，每一条都在下方某个任务里有对应测试：

1. **同轮多个框**：答案只能戳中 `request_id` 匹配的那一个 part，不能串到同轮其它框（用户真实会话里一轮有 5 个框）。
2. **自由文本答案的长度**：用户在输入框粘贴大段文本会原样进入历史 part，撑大历史行；落库前必须截断。
3. **历史里没有答案戳的旧数据**（修复上线前已作答的框）：既无本地记录也无 `answer`，必须落到终态而不是可点击的 pending；点击不得伪造「已选择」。
4. **`interactive_choice_resolved` 的 `data` 是 dict 而不是 JSON 字符串**，且可能带未知字段；解析必须容错，不得抛异常打断 run 消费循环。
5. **两个标签页同时开着不同会话**：写回只能覆盖自己那一份切片，不能把对方会话的切片删掉；localStorage 不可写（隐私模式/配额满）时只 warn，不影响 UI。

---

## 1. 背景与根因（Spec）

### 1.1 现状数据流

- 题面：插件 `_push_to_webchat_back_queue` → `type:"plain" + chain_type:"interactive_choice"` → `chat_service.BotMessageAccumulator._store_interactive_choice` 落成历史 part（`request_id` / `prompt` / `options` / `title` / `input_placeholder` / `extra_content` / `expires_at`）。刷新后框能渲染，靠的是这条历史。
- 用户意图：只存在浏览器 localStorage（`astrbot-interactive-choice-submissions` 等 4 个 key），由 `dashboard/src/stores/interactiveChoice.ts` 按 UMO 分桶维护。
- 答案：**任何地方都不落库**。`interactive_choice_registry.resolve()` 只 set 一个 asyncio Future；历史 part 里没有答案字段。

### 1.2 根因 Bug Z：切会话把别的会话的持久化记录整块删掉

`hydrate(umo)` 在切会话时故意清空内存里其它 UMO 的桶（Y1/Y2 修的跨会话串台），而 `persist*` 序列化的是**整个内存 map**。于是切会话后的第一次写入就只写回当前会话：

- `ChatMessageList.onMounted` → `mirrorInteractiveChoiceParts` 会把历史里的框重新 `addChoice`（已提交的框不在 `activeChoices` 里，去重判断放行）→ `persist()`；
- `recomputeIgnored` → `markIgnored` → `persistIgnored()`。

复现（真实 store 模块，MemoryStorage 模拟 localStorage）：

```
A 会话预置 submissions/ignored/pending
→ hydrate(B)、在 B 里 markSubmitted + addChoice + markIgnored
submissions -> {"...sessB":{...}}     ← A 的切片消失
ignored     -> {"...sessB":{...}}     ← 同上
pending     -> {"...sessB":{...}}     ← 同上
→ hydrate(A)：getSubmissionState(A, "rid-yesterday") === undefined
```

### 1.3 为什么表现为「可点击的 pending」而不是「已忽略」

- `isInteractiveChoiceIgnored` 的兜底推导是「这条记录之后还有没有 user 记录」。一轮里选择通过工具回传，不落成 user 消息；用户截图那轮（会话 `ce5423ea-…`，`2026-09-28 08:35:54`，含 `主题/语气/意象/人称/结尾` 五个框 + 正文）就是该会话最后一条记录 → 推导为 false。
- `reconcile(umo)` 的孤儿检测本来能把「后端已无 pending」的框标成已取消，但它只在 `onMounted` / UMO 切换时跑一次，而历史是异步（且分页）加载的——框通常是在 reconcile 之后才 mirror 进 store，从未对账。于是终态判定只剩 localStorage 一条路，被 Bug Z 抹掉后就是 pending。

### 1.4 目标终态

| 情形 | 期望渲染 |
|---|---|
| 本机点过，本地记录在 | 已选择 X |
| 任意设备刷新，历史 part 带 `answer` | 已选择 X |
| 服务端超时 / 用户取消，历史 part 带 `resolved` | 已取消 |
| 后端已无 pending 且历史无戳（重启前挂着的框、修复前的旧数据） | 已取消（**不可点**，不再伪造已选择） |
| 真的在等待作答 | pending |

---

## 2. 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `dashboard/src/stores/interactiveChoice.ts` | 会话态缓存 + 持久化 | 改 `persist*` 为 per-UMO 读改写（Task 1） |
| `dashboard/src/stores/interactiveChoice.test.ts` | 上述 store 的 `node --test` 用例 | 加 4 条、收紧 1 条（Task 1） |
| `data/plugins/astrbot_plugin_ask_user_choice/api_mount.py` | resolved 事件推送（共享） | 签名加 `choice_id` / `free_text`（Task 2） |
| `data/plugins/astrbot_plugin_ask_user_choice/ask_user_choice_tool.py` | 工具主体 | 提交分支带答案；删掉重复的私有推送方法（Task 2） |
| `data/plugins/astrbot_plugin_ask_user_choice/main.py` | 「用文字回答」分支 | 带 `free_text`（Task 2） |
| `data/plugins/astrbot_plugin_ask_user_choice/tests/*` | 插件 pytest 用例 | 加 2 条（Task 2） |
| `astrbot/dashboard/services/chat_service.py` | 历史落盘 | 新增 `stamp_choice_resolution` + `_consume_chat_run` 分支（Task 3） |
| `tests/unit/test_chat_service_interactive_choice.py` | 纯函数用例 | 加 4 条（Task 3） |
| `tests/unit/test_chat_service_choice_mirror.py` | `_consume_chat_run` 集成用例 | 加 1 条（Task 3） |
| `dashboard/src/composables/parseInteractiveChoice.ts` | part 类型与纯函数 | 加 `answer` / `resolved` 类型 + `submissionFromPart`（Task 4） |
| `dashboard/src/components/chat/message_list_comps/InteractiveChoiceBox.vue` | 状态机 | 终态可来自 part（Task 4） |
| `dashboard/src/components/chat/ChatMessageList.vue` | 消息流装配 | 历史加载后补一次 reconcile（Task 5） |

---

## Task 1: store 的 per-UMO 读改写（Bug Z 核心）

**Files:**
- Modify: `dashboard/src/stores/interactiveChoice.ts`（`addChoice`/`removeChoice`/`markSubmitted`/`clearSubmissionState`/`markIgnored`/`markCancelled`/`reconcile` 共 7 处调用点 + 4 个 persist 方法）
- Test: `dashboard/src/stores/interactiveChoice.test.ts`

**Interfaces:**
- Consumes: 已有 `readPerUmo<T>(key)`（返回 `Record<umo, Record<rid, T>>`，畸形负载会被丢弃并清 key）。
- Produces: `persistSlice<T>(key: string, umo: string, slice: Record<string, T> | undefined): void`；`persist(umo)`、`persistSubmissions(umo)`、`persistIgnored(umo)`、`persistCancelled(umo)` 全部改为收一个 `umo` 参数。`slice` 为 `undefined` 或空对象时删除该 UMO 的记录，其余 UMO 的切片原样保留。

- [ ] **Step 1: 写失败测试（`dashboard/src/stores/interactiveChoice.test.ts` 末尾追加）**

```ts
// ---------------------------------------------------------------------------
// Bug Z: 切会话后的写回只能覆盖当前 UMO 的切片
// ---------------------------------------------------------------------------

test("markSubmitted in another session keeps this session's submissions (Bug Z)", () => {
  const UMO_A = "webchat:FriendMessage:webchat!alice!sessA";
  const UMO_B = "webchat:FriendMessage:webchat!alice!sessB";
  localStorage.setItem(
    SUBMISSION_STORAGE_KEY,
    JSON.stringify({
      [UMO_A]: { "rid-a": { kind: "option", optionId: "B", submittedAt: 1 } },
    }),
  );
  const store = useInteractiveChoiceStore();
  store.hydrate(UMO_B);
  store.markSubmitted(UMO_B, "rid-b", "option", { optionId: "A" });

  const persisted = JSON.parse(
    localStorage.getItem(SUBMISSION_STORAGE_KEY) as string,
  );
  assert.equal(persisted[UMO_A]["rid-a"].optionId, "B");
  assert.equal(persisted[UMO_B]["rid-b"].optionId, "A");
});

test("addChoice in another session keeps this session's pending slices (Bug Z)", () => {
  const UMO_A = "webchat:FriendMessage:webchat!alice!sessA";
  const UMO_B = "webchat:FriendMessage:webchat!alice!sessB";
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      [UMO_A]: {
        "rid-a": {
          type: "interactive_choice",
          request_id: "rid-a",
          prompt: "p",
          options: [{ id: "A", label: "a" }],
        },
      },
    }),
  );
  const store = useInteractiveChoiceStore();
  store.hydrate(UMO_B);
  store.addChoice(UMO_B, {
    type: "interactive_choice",
    request_id: "rid-b",
    prompt: "q",
    options: [{ id: "B", label: "b" }],
  });

  const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
  assert.ok(persisted[UMO_A]?.["rid-a"]);
  assert.ok(persisted[UMO_B]?.["rid-b"]);
});

test("markIgnored / markCancelled keep other sessions' slices (Bug Z)", () => {
  const UMO_A = "webchat:FriendMessage:webchat!alice!sessA";
  const UMO_B = "webchat:FriendMessage:webchat!alice!sessB";
  localStorage.setItem(
    IGNORED_STORAGE_KEY,
    JSON.stringify({ [UMO_A]: { "rid-a": true } }),
  );
  localStorage.setItem(
    CANCELLED_STORAGE_KEY,
    JSON.stringify({ [UMO_A]: { "rid-a-cancel": true } }),
  );
  const store = useInteractiveChoiceStore();
  store.hydrate(UMO_B);
  store.markIgnored(UMO_B, ["rid-b"]);
  store.markCancelled(UMO_B, "rid-b-cancel");

  assert.equal(
    JSON.parse(localStorage.getItem(IGNORED_STORAGE_KEY) as string)[UMO_A][
      "rid-a"
    ],
    true,
  );
  assert.equal(
    JSON.parse(localStorage.getItem(CANCELLED_STORAGE_KEY) as string)[UMO_A][
      "rid-a-cancel"
    ],
    true,
  );
});

test("answering in session B does not revive session A's answered box (Bug Z)", () => {
  const UMO_A = "webchat:FriendMessage:webchat!alice!sessA";
  const UMO_B = "webchat:FriendMessage:webchat!alice!sessB";
  localStorage.setItem(
    SUBMISSION_STORAGE_KEY,
    JSON.stringify({
      [UMO_A]: { "rid-yesterday": { kind: "option", optionId: "B", submittedAt: 1 } },
    }),
  );
  const store = useInteractiveChoiceStore();

  // 今天：先落在 B（默认最近会话），在 B 里正常作答 —— 正是线上触发路径。
  store.hydrate(UMO_B);
  store.markSubmitted(UMO_B, "rid-today", "option", { optionId: "A" });
  store.addChoice(UMO_B, {
    type: "interactive_choice",
    request_id: "rid-today",
    prompt: "q",
    options: [{ id: "A", label: "a" }],
  });
  store.markIgnored(UMO_B, ["rid-today"]);

  // 切回 A：历史里的框必须仍能读到「已选择」。
  store.hydrate(UMO_A);
  assert.equal(
    store.getSubmissionState(UMO_A, "rid-yesterday")?.optionId,
    "B",
  );
});

test("persistSlice swallows a storage failure (private mode / quota)", () => {
  const original = localStorage.setItem;
  localStorage.setItem = () => {
    throw new Error("QuotaExceededError");
  };
  try {
    const store = useInteractiveChoiceStore();
    store.hydrate(TEST_UMO);
    // 只要求不抛：写失败降级为「本次页面会话内有效」，UI 必须继续可用。
    store.markSubmitted(TEST_UMO, "rid-quota", "option", { optionId: "A" });
    assert.equal(store.getSubmissionState(TEST_UMO, "rid-quota")?.optionId, "A");
  } finally {
    localStorage.setItem = original;
  }
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd dashboard && node --test src/stores/interactiveChoice.test.ts`
Expected: 4 条新用例失败（`persisted[UMO_A]` 为 `undefined`、最后一条 `undefined !== "B"`），第 5 条（storage failure）通过，其余 55 条通过。

- [ ] **Step 3: 实现 `persistSlice` 并把 4 个 persist 方法改成收 `umo`**

在 `persist()` 上方新增（英文 docstring，说明 Bug Z 与「空切片 ⇒ 删 key」规则）：

```ts
persistSlice<T>(
  key: string,
  umo: string,
  slice: Record<string, T> | undefined,
): void
```

实现要点：`readPerUmo<T>(key)` 读回已存 map → `slice` 非空则 `merged[umo] = slice`，否则 `delete merged[umo]` → `localStorage.setItem(key, JSON.stringify(merged))`；整段 try/catch，失败只 `console.warn`（沿用现有 best-effort 契约）。

四个方法各自变成一行委托：`persist(umo)` → `persistSlice(STORAGE_KEY, umo, this.activeChoices[umo])`；`persistSubmissions(umo)` / `persistIgnored(umo)` / `persistCancelled(umo)` 同理，分别传对应 key 与 `this.submissionStates[umo]` / `this.ignoredStates[umo]` / `this.cancelledStates[umo]`。

7 处调用点全部补上各自作用域里的 `umo`（`addChoice`、`removeChoice`、`reconcile` 传 `STORAGE_KEY` 那一支；`markSubmitted`、`clearSubmissionState`；`markIgnored`；`markCancelled`）。`persistSlice` 的 docstring 里保留原 `persist()` 的「best-effort、quota 满不抛」说明。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd dashboard && node --test src/stores/interactiveChoice.test.ts`
Expected: 59 条全绿。

- [ ] **Step 5: 收紧被 Bug Z 放宽的旧用例**

`hydrate with a new UMO wipes the previous bucket from memory`（约 691 行）当前只断言 UMO #2 在 localStorage 里，注释写着「UMO #1 可能在也可能不在，取决于顺序」。把该注释删掉，并补一条断言：切到 UMO #2 后 `addChoice` 不得抹掉 UMO #1 的切片。

```ts
  assert.ok(persisted["webchat:one!1!s"]?.["r1"]);
```

Run: `cd dashboard && node --test src/stores/interactiveChoice.test.ts`
Expected: 59 条全绿（该断言在 Step 3 之前必然失败，是这一步的回归锚点）。

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/stores/interactiveChoice.ts dashboard/src/stores/interactiveChoice.test.ts
git commit -m "fix(chatui): scope interactive-choice persistence to one UMO slice"
```

---

## Task 2: 插件在 resolved 事件上带答案

**Files:**
- Modify: `data/plugins/astrbot_plugin_ask_user_choice/api_mount.py`（`_push_resolved_event_to_back_queue`）
- Modify: `data/plugins/astrbot_plugin_ask_user_choice/ask_user_choice_tool.py`（`call` 第 8 步、超时分支、删除私有重复方法 `_push_resolved_to_back_queue`）
- Modify: `data/plugins/astrbot_plugin_ask_user_choice/main.py`（`on_message` 的「用文字回答」分支）
- Test: `data/plugins/astrbot_plugin_ask_user_choice/tests/test_api_mount.py`、`tests/test_ask_user_choice_tool.py`

**Interfaces:**
- Produces: `_push_resolved_event_to_back_queue(request_id: str, umo: str, reason: str, sse_message_id: str, choice_id: str = "", free_text: str = "") -> None`；`data` 里仅当对应值非空时带上 `choice_id` / `free_text`（沿用现有 `umo` 字段的写法）。三个 push 点统一走这一个函数，工具内不再保留同名私有实现。

- [ ] **Step 1: 写失败测试（`tests/test_ask_user_choice_tool.py`，接在成功路径用例之后）**

```python
@pytest.mark.asyncio
async def test_call_success_path_broadcasts_the_answer(monkeypatch):
    """提交分支必须把用户选择带出去，否则答案无法落库到历史。"""
    tool = AskUserChoiceTool()
    ctx = _make_context()
    captured: list[dict] = []

    async def fake_push(*args, **kwargs):
        pass

    async def fake_resolved(*args, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(tool, "_push_to_webchat_back_queue", fake_push)
    monkeypatch.setattr(
        "astrbot_plugin_ask_user_choice.ask_user_choice_tool."
        "_push_resolved_event_to_back_queue",
        fake_resolved,
    )
    monkeypatch.setattr(
        tool, "_load_tool_config",
        lambda ctx: {"timeout_seconds": 5, "max_concurrent_pending": 32},
    )

    call_task = asyncio.create_task(
        tool.call(
            ctx,
            prompt="Pick one",
            options=[{"id": "A", "label": "alpha"}, {"id": "B", "label": "beta"}],
        )
    )
    await asyncio.sleep(0.05)
    rid = next(iter(registry._pending.keys()))
    registry.resolve(rid, {"choice_id": "B", "free_text": "顺便说一句"})
    await asyncio.wait_for(call_task, timeout=2.0)

    submitted = [c for c in captured if c.get("reason") == "submitted"]
    assert submitted, "提交分支必须广播 resolved{submitted}"
    assert submitted[0]["choice_id"] == "B"
    assert submitted[0]["free_text"] == "顺便说一句"
```

同文件另有 3 处 `monkeypatch.setattr(tool, "_push_resolved_to_back_queue", ...)`（约 198 / 244 / 282 行）指向本任务要删掉的私有方法：这三处的 target 必须改成上面的模块级路径 `astrbot_plugin_ask_user_choice.ask_user_choice_tool._push_resolved_event_to_back_queue`，否则 Step 4 会因为属性不存在而报错。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd data/plugins/astrbot_plugin_ask_user_choice && PYTHONPATH="$PWD/../../..:$PWD/../.." ../../../venv/python.exe -m pytest tests/test_ask_user_choice_tool.py -q`
Expected: 新用例 FAIL（`KeyError: 'choice_id'`）；既有用例通过。

- [ ] **Step 3: 实现**

`api_mount.py`：给 `_push_resolved_event_to_back_queue` 加两个可选参数并写进 `data`（空值不写 key），docstring 补 `Args`。

`ask_user_choice_tool.py`：
1. 第 8 步 `_push_resolved_event_to_back_queue(...)` 调用处补 `choice_id=str(user_choice.get("choice_id") or "")`、`free_text=str(user_choice.get("free_text") or "")`。
2. 超时分支改调共享函数（`reason="cancelled"`，不带答案），随后删除私有方法 `_push_resolved_to_back_queue` —— 它与共享函数只差 `umo` 字段，留着会让「哪些字段会被推出去」有两份真相。

`main.py`：`on_message` 的 resolved 推送补 `choice_id="__free_text__"`、`free_text=message_text`。

- [ ] **Step 4: 跑插件测试确认通过**

Run: `cd data/plugins/astrbot_plugin_ask_user_choice && PYTHONPATH="$PWD/../../..:$PWD/../.." ../../../venv/python.exe -m pytest tests -q`
Expected: 全绿（Step 2 的 34 条 + 新增）。

- [ ] **Step 5: 格式化 + Commit**

```bash
uv run ruff format data/plugins/astrbot_plugin_ask_user_choice
uv run ruff check data/plugins/astrbot_plugin_ask_user_choice
git add data/plugins/astrbot_plugin_ask_user_choice
git commit -m "feat(ask_user_choice): carry the answer on the resolved SSE event"
```

---

## Task 3: chat_service 把答案戳进本轮历史 part

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py`（新增模块级 `stamp_choice_resolution`；`_consume_chat_run` 内 `msg_type == "interactive_choice_resolved"` 处接上）
- Test: `tests/unit/test_chat_service_interactive_choice.py`、`tests/unit/test_chat_service_choice_mirror.py`

**Interfaces:**
- Consumes: Task 2 的 resolved 事件形状（`data` 是 **dict**，含 `request_id` / `reason` / `umo`，`reason == "submitted"` 时另有 `choice_id` / `free_text`）。
- Produces: `stamp_choice_resolution(parts: list[dict], data: dict) -> bool` —— 就地修改 `parts`，返回是否真的改动了某个 part。

数据形状（写进历史 part 的两个新字段，前端 Task 4 依赖它）：

```python
# reason == "submitted"（choice_id 为空则跳过，不戳）
part["answer"] = {"choice_id": "B", "free_text": "", "answered_at": 1759xxxxxx.12}
# reason == "cancelled"
part["resolved"] = {"reason": "cancelled", "resolved_at": 1759xxxxxx.12}
```

规则：按 `request_id` 精确匹配 `type == "interactive_choice"` 的 part（同轮多个框只戳中一个）；`free_text` 落库前截断到 **2000** 字符（用户可粘贴大段文本，历史行不能被撑爆；展示用字段，LLM 早已拿到全文）；`data` 不是 dict、缺 `request_id`、`reason` 不认识时返回 `False` 且不抛。

- [ ] **Step 1: 写失败测试（`tests/unit/test_chat_service_interactive_choice.py` 末尾追加）**

```python
def _choice_part(request_id: str = "req-1") -> dict:
    return {
        "type": "interactive_choice",
        "request_id": request_id,
        "prompt": "Pick one",
        "options": [{"id": "A", "label": "alpha"}, {"id": "B", "label": "beta"}],
    }


def test_stamp_choice_resolution_records_the_answer():
    parts = [_choice_part()]
    changed = stamp_choice_resolution(
        parts,
        {"request_id": "req-1", "reason": "submitted", "choice_id": "B", "free_text": ""},
    )
    assert changed is True
    assert parts[0]["answer"]["choice_id"] == "B"
    assert isinstance(parts[0]["answer"]["answered_at"], float)


def test_stamp_choice_resolution_targets_only_the_matching_request_id():
    parts = [_choice_part("req-1"), _choice_part("req-2")]
    stamp_choice_resolution(parts, {"request_id": "req-2", "reason": "submitted", "choice_id": "A"})
    assert "answer" not in parts[0]
    assert parts[1]["answer"]["choice_id"] == "A"


def test_stamp_choice_resolution_truncates_free_text():
    parts = [_choice_part()]
    stamp_choice_resolution(
        parts,
        {"request_id": "req-1", "reason": "submitted", "choice_id": "__free_text__", "free_text": "x" * 5000},
    )
    assert len(parts[0]["answer"]["free_text"]) == 2000


def test_stamp_choice_resolution_records_cancellation():
    parts = [_choice_part()]
    assert stamp_choice_resolution(parts, {"request_id": "req-1", "reason": "cancelled"}) is True
    assert parts[0]["resolved"]["reason"] == "cancelled"


def test_stamp_choice_resolution_is_tolerant_of_malformed_data():
    parts = [_choice_part()]
    for bad in (None, "req-1", {}, {"reason": "submitted"}, {"request_id": "req-1"}, {"request_id": "unknown", "reason": "submitted", "choice_id": "A"}):
        assert stamp_choice_resolution(parts, bad) is False
    assert "answer" not in parts[0] and "resolved" not in parts[0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_chat_service_interactive_choice.py -q`
Expected: 新用例 FAIL（`ImportError: cannot import name 'stamp_choice_resolution'`）；既有用例通过。

- [ ] **Step 3: 实现纯函数**

在 `chat_service.py` 的 `extract_web_search_refs` 附近新增模块级 `stamp_choice_resolution`（英文 Google docstring：`Args` / `Returns`），用 `time.time()` 作时间戳，与 part 上既有的 `expires_at` 同单位（unix 秒）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_chat_service_interactive_choice.py -q`
Expected: 全绿。

- [ ] **Step 5: 接进 `_consume_chat_run`**

在 `if chain_type == "interactive_choice" or msg_type == "interactive_choice_resolved":` 那段（系统流镜像）之后、`_publish_chat_run(run, result)` 之前，就地加分支：

```python
# The answer/cancellation verdict is stamped onto the part the turn already
# persisted, so a reload can restore the terminal state from history alone
# (localStorage is per-browser and can be wiped).
if msg_type == "interactive_choice_resolved":
    resolved_data = result.get("data")
    if isinstance(resolved_data, dict) and stamp_choice_resolution(
        run.history_parts, resolved_data
    ):
        if run.history_record is not None:
            await self.platform_history_mgr.update(...)
```

`platform_history_mgr.update(...)` 的三个实参抄 `flush_pending_bot_message` 里那一处现成写法（`message_id=run.history_record.id`、`content=build_bot_history_content(run.history_parts, agent_stats=run.agent_stats, file_changes=run.file_changes, refs=run.refs)`、`llm_checkpoint_id=run.llm_checkpoint_id`），不要另造形状。

- [ ] **Step 6: 写集成测试（`tests/unit/test_chat_service_choice_mirror.py`）**

在 `_make_service()` 里补 `service.platform_history_mgr.update = AsyncMock()`（现为 `MagicMock`，`await` 会 TypeError）。新增用例：推 `interactive_choice` 题面事件 → 推 `interactive_choice_resolved`（`reason: "submitted"`、`choice_id: "A"`）→ 推 `end` → 断言 `service.platform_history_mgr.update` 被调用，且 `call_args.kwargs["content"]` 里 `request_id == "req-1"` 的 part 带 `answer.choice_id == "A"`。

Run: `uv run pytest tests/unit/test_chat_service_choice_mirror.py -q`
Expected: 3 条全绿（既有 2 条 + 新增；既有用例里 `reason: "picked A"` 的 payload 走 `False` 分支，行为不变）。

- [ ] **Step 7: 格式化 + Commit**

```bash
uv run ruff format astrbot/dashboard/services/chat_service.py tests/unit
uv run ruff check astrbot/dashboard/services/chat_service.py tests/unit
git add astrbot/dashboard/services/chat_service.py tests/unit
git commit -m "feat(chatui): stamp the choice answer into the turn history record"
```

---

## Task 4: 前端从历史 part 恢复终态

**Files:**
- Modify: `dashboard/src/composables/parseInteractiveChoice.ts`
- Modify: `dashboard/src/components/chat/message_list_comps/InteractiveChoiceBox.vue`
- Test: `dashboard/src/composables/parseInteractiveChoice.test.ts`、`dashboard/src/components/chat/message_list_comps/InteractiveChoiceBox.spec.ts`

**Interfaces:**
- Consumes: Task 3 落库的 `part.answer` / `part.resolved`。
- Produces: `InteractiveChoicePart` 新增可选字段 `answer?: { choice_id: string; free_text?: string; answered_at?: number }` 与 `resolved?: { reason: string; resolved_at?: number }`；新增纯函数 `submissionFromPart(part: InteractiveChoicePart): { kind: "option" | "input"; optionId?: string; freeText?: string } | null`（`choice_id === "__free_text__"` → `kind: "input"`；否则 `kind: "option"` 且 `optionId = choice_id`；两者皆空返回 `null`）。

- [ ] **Step 1: 写失败测试（`parseInteractiveChoice.test.ts`，`node --test` 纯函数）**

```ts
test("submissionFromPart reads an option answer", () => {
  const sub = submissionFromPart({
    type: "interactive_choice", request_id: "r", prompt: "p",
    options: [{ id: "A", label: "a" }],
    answer: { choice_id: "B", free_text: "", answered_at: 1 },
  });
  assert.equal(sub?.kind, "option");
  assert.equal(sub?.optionId, "B");
});

test("submissionFromPart reads a free-text answer", () => { /* choice_id === "__free_text__" → kind "input", freeText 透传 */ });
test("submissionFromPart returns null without an answer", () => { /* 无 answer / answer 为空对象 → null */ });
test("submissionFromPart ignores a malformed answer", () => { /* answer: "B" / { free_text: "" } → null，不抛 */ });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd dashboard && node --test src/composables/parseInteractiveChoice.test.ts`
Expected: 4 条 FAIL（`submissionFromPart is not a function`）。

- [ ] **Step 3: 实现类型 + 纯函数**

在 `parseInteractiveChoice.ts` 里加两个导出类型与 `submissionFromPart`（英文注释，说明字段由后端 Task 3 落库、是「历史自解释」的唯一来源）。`truncateInteractiveChoice` / `validateInteractiveChoice` 不改：前者 `{...part}` 展开、后者不拒绝未知字段，新字段天然透传。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd dashboard && node --test src/composables/parseInteractiveChoice.test.ts`
Expected: 全绿。

- [ ] **Step 5: 写失败测试（`InteractiveChoiceBox.spec.ts`，Vitest）**

```ts
it("renders 已选择 from the history answer when localStorage lost the record", () => {
  // storeMock 默认无 submission（模拟 Bug Z 之后 / 换浏览器）
  const wrapper = mountBox({ part: makePart({ answer: { choice_id: "B" } }), umo });
  expect(wrapper.find(".interactive-choice-box.is-submitted").exists()).toBe(true);
  expect(wrapper.find(".choice-cancel-button").exists()).toBe(false);
});

it("renders 已取消 from the history resolution stamp", () => {
  const wrapper = mountBox({ part: makePart({ resolved: { reason: "cancelled" } }), umo });
  expect(wrapper.find(".interactive-choice-box.is-cancelled").exists()).toBe(true);
});

it("keeps the local submission ahead of the history answer", () => {
  // storeMock.markSubmitted(umo, "req-1", "option", { optionId: "A" }) 后，
  // part.answer.choice_id = "B" —— 本地意图优先，仍显示 A。
});
```

（`makePart` 已支持字段覆盖；三例都不需要改 mock 的写入面。）

- [ ] **Step 6: 跑测试确认失败**

Run: `cd dashboard && npx vitest run src/components/chat/message_list_comps/InteractiveChoiceBox.spec.ts`
Expected: 3 条 FAIL（`.is-submitted` / `.is-cancelled` 找不到）。

- [ ] **Step 7: 实现（只动两个 computed）**

```ts
const submissionState = computed(
  () =>
    interactiveChoiceStore.getSubmissionState(props.umo, props.part.request_id) ??
    submissionFromPart(props.part),
);

const cancelledState = computed(
  () =>
    interactiveChoiceStore.isCancelled(props.umo, props.part.request_id) ||
    props.part.resolved?.reason === "cancelled",
);
```

`state` 的优先级（submission > cancelled > ignored > pending）与注释保持不动 —— 这条顺序本身就是「本地提交意图不被服务端结论覆盖」的竞态保护，`part.answer` 走 submission 槽位即可。

- [ ] **Step 8: 跑测试确认通过 + 类型检查**

Run: `cd dashboard && npx vitest run src/components/chat/message_list_comps/InteractiveChoiceBox.spec.ts && npx vue-tsc --noEmit`
Expected: 21 条全绿；类型检查无新错误。

- [ ] **Step 9: Commit**

```bash
git add dashboard/src/composables/parseInteractiveChoice.ts dashboard/src/composables/parseInteractiveChoice.test.ts \
        dashboard/src/components/chat/message_list_comps/InteractiveChoiceBox.vue \
        dashboard/src/components/chat/message_list_comps/InteractiveChoiceBox.spec.ts
git commit -m "feat(chatui): restore a choice box's terminal state from history"
```

---

## Task 5: 历史加载后补一次对账（可单独砍掉）

只影响「后端已无 pending 且历史无戳」的框：重启前挂着的框、修复上线前的旧数据。没有这一步，它们会一直停在可点击的 pending，点下去只会在本地伪造「已选择」而后端返回 404。Task 5 与其余任务无依赖，砍掉不影响 Task 1–4。

**Files:**
- Modify: `dashboard/src/components/chat/ChatMessageList.vue`（messages watcher + UMO watcher）
- Test: `dashboard/src/components/chat/ChatMessageList.pendingChoice.spec.ts`

**Interfaces:**
- Consumes: `interactiveChoiceStore.reconcile(umo)`（已存在，孤儿检测会把「本地有、后端 pending 列表没有」的 rid 标成已取消）。
- Produces: 无新导出。

- [ ] **Step 1: 写失败测试（`ChatMessageList.pendingChoice.spec.ts`）**

在 store mock 里加 `reconcile: vi.fn()`，新增用例：挂载时 `messages` 为空（历史尚未返回）→ 挂载后再把带 `interactive_choice` 的 bot 记录推进 `props.messages` → 断言 `reconcile` 被调用一次、参数为该会话 umo；再次推入同样的消息数组不产生第二次调用。

这一步只锚「何时对账」。「对账之后框落到终态」不需要在组件层重复断言：`reconcile` 的孤儿检测 → `markCancelled` 已有 store 用例覆盖，`is-cancelled` 渲染已有 `InteractiveChoiceBox` 用例覆盖，而 Task 4 之后 `reconcile` 标出的 cancelled 会被 `part.answer` 的 submission 优先级压住（不会把已作答的框误标成已取消）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd dashboard && npx vitest run src/components/chat/ChatMessageList.pendingChoice.spec.ts`
Expected: 新用例 FAIL（`reconcile` 未被调用）。

- [ ] **Step 3: 实现**

在模块作用域加一个计数器 ref（`let reconciledChoiceCount = 0`），messages watcher 里就地统计 `next` 中 `interactive_choice` part 的数量（循环内联，不抽 helper），数量变化且 `props.currentUmo` 存在时 `void interactiveChoiceStore.reconcile(props.currentUmo)` 并更新计数器；UMO watcher 里把计数器归零（新会话要重新对账）。注释说明为什么用计数而不是每次 messages 变化都发请求（SSE 每个 chunk 都会触发 watcher）。

- [ ] **Step 4: 跑测试确认通过 + 回归相邻用例**

Run: `cd dashboard && npx vitest run src/components/chat/ChatMessageList.pendingChoice.spec.ts src/components/chat/ChatMessageList.revealWork.spec.ts`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/chat/ChatMessageList.vue dashboard/src/components/chat/ChatMessageList.pendingChoice.spec.ts
git commit -m "fix(chatui): reconcile a loaded history page against the pending list"
```

---

## 3. 验收

自动化：

```bash
cd dashboard && node --test src/stores/interactiveChoice.test.ts src/composables/parseInteractiveChoice.test.ts
cd dashboard && npx vitest run src/components/chat
cd dashboard && npx vue-tsc --noEmit
uv run pytest tests/unit -q
cd data/plugins/astrbot_plugin_ask_user_choice && PYTHONPATH="$PWD/../../..:$PWD/../.." ../../../venv/python.exe -m pytest tests -q
uv run ruff format astrbot/dashboard/services/chat_service.py && uv run ruff check astrbot/dashboard/services/chat_service.py
```

手工（需要重启后端进程，插件与 dashboard 同版本）：

1. 会话 A 里触发 `ask_user_choice`，点一个选项 → 框显示「已选择 X」。
2. 切到会话 B，随便跑一轮（确保 B 的历史里有框）→ 切回 A。
3. 硬刷新页面 → A 的框仍显示「已选择 X」（Task 1 + Task 4）。
4. DevTools 清空 `astrbot-interactive-choice-submissions` 后刷新 → 仍显示「已选择 X」（Task 4 的 `part.answer`）。
5. 重启 AstrBot 进程后刷新 → 仍显示「已选择 X」（答案已进历史库）。
6. 打开一个修复前的老会话：老框不再可点，显示「已取消」（Task 5；预期行为，见 Review Focus 3）。
7. 双开两个标签页分别停在 A / B，各自作答一轮 → 刷新任一侧，另一侧会话的终态不丢（Task 1 的跨会话隔离）。

## 4. Out of scope

- **修复前已作答的旧框无法回溯恢复**：历史 part 里没有答案字段，无法从任何数据源重建「当时选了哪个」。它们只会落到「已取消」（Task 5），不会再显示「已选择」。
- Agent Teams 成员 transcript（`astrbot/dashboard/services/agent_team_ports.py` 的 choice 镜像、`MemberTranscriptDialog.vue`）沿用现状，不接答案戳。
- 跨标签页实时同步「另一侧已作答」的框（现有 `interactive_choice_resolved{submitted}` 事件被前端有意丢弃，本计划不改这一行为）。
- 跨设备/多用户共享终态（答案落库后天然支持，但不额外做同步推送）。

---

## Execution Handoff

计划落盘于 `docs/superpowers/plans/2026-09-29-interactive-choice-resolved-durability.md`。

推荐 **Native（本会话内逐任务执行）**：Task 1 → 4 是同一份契约（`answer` 字段的形状）在四个文件里的传递，任务之间接口强耦合、单任务体量都很小，独立 subagent 上下文切换的收益低于接口跑偏的风险；Task 5 可单独砍掉。
