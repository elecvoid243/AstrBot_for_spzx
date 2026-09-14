# ChatUI 会话导出与导入（跨用户迁移）设计

- 日期：2026-09-13
- 状态：待评审（未实现）
- 范围：ChatUI（webchat 平台）会话的打包导出与导入，支持把用户 A 的聊天记录迁移给用户 B（同一实例或跨实例）

## 1. 背景与问题

ChatUI 的会话数据散落在多张表和磁盘文件中（见 §3.1），现有能力无法做部分迁移：

- 全量备份（`astrbot/core/backup/`）是"整库替换"语义：导入前清空所有表、附件按导出机绝对路径还原，不能选择部分数据，也不能合并进一个在用实例。
- 对话导出（`astrbot/dashboard/services/conversation_service.py:483`）只导出 LLM checkpoint（`conversations.content`）为 JSONL，不含渲染层消息流、附件文件、线程回复，且没有对应的导入功能。
- `DELETE /api/files/{attachment_id}`（`astrbot/dashboard/api/files.py:105`）是空壳，附件只随整个会话删除时清理（`astrbot/dashboard/services/chat_service.py:2071`）。

因此需要一套会话级（session 粒度）的导出/导入机制。

## 2. 目标与非目标

### 目标

1. 用户可以在 ChatUI 中把某个会话（含消息流、图片/文件附件、线程回复、LLM 上下文）导出为一个 zip 文件。
2. 任何登录用户可以把导出包导入到**自己的账户**下，得到内容一致的新会话；支持同实例跨账户和跨实例迁移。
3. 导入后消息渲染、图片显示、继续对话（LLM 上下文）、引用回复线程均正常工作。

### 非目标

- 不做批量/全用户导出（zip 格式本身支持多会话，批量入口留作后续）。
- 不做服务端直接复制（无文件的"管理员复制到用户 B"）。
- 不迁移：人格（personas）定义、ChatUI 项目归属（`chatui_projects` / `session_project_relations`）、平台统计（`platform_stats`）、输入框草稿（浏览器 localStorage，`useChatDrafts.ts`）、运行时诊断数据（如 prefix-cache 指纹，纯内存）。
- 不处理非 webchat 平台的会话。
- 不改变附件现有访问控制（`/api/files/{id}` 依旧只要求登录 + file scope）。
- 不引入附件清理机制（与本功能正交）。

## 3. 调研结论

### 3.1 一个 ChatUI 会话的数据足迹

| 数据 | 表 | 关键关联 |
| --- | --- | --- |
| 会话 | `platform_sessions`（`session_id` UUID 唯一、`platform_id`、`creator`、`display_name`、`is_group`、`archived`） | — |
| 渲染层消息流 | `platform_message_history`（`platform_id="webchat"`，`user_id=session_id`，自增 `id`，`sender_id/sender_name`，`content` 消息 parts JSON，`llm_checkpoint_id`） | 图片/文件 part 引用 `attachment_id` |
| LLM 上下文 | `conversations`（`conversation_id` UUID 唯一，`user_id` = UMO 字符串，`content` OpenAI 格式 checkpoint 列表，`title`、`persona_id`、`token_usage`） | UMO = `{platform}:{type}:{platform}!{creator}!{session_id}`（`chat_service.py:813`） |
| 线程回复 | `webchat_threads`（`thread_id` UUID 唯一、`creator`、`parent_session_id`、`parent_message_id` → history 自增 `id`、`base_checkpoint_id`、`selected_text`） | 线程还有自己的 history（`platform_id="webchat_thread"`，`user_id=thread_id`）和 conversations（user_id = 线程 UMO，`chat_service.py:825`） |
| 附件 | `attachments`（`attachment_id` UUID 唯一、`path` 本机绝对路径、`type`、`mime_type`）+ `data/attachments/` 磁盘文件 | 消息 parts JSON 内引用 `attachment_id` |
| 会话级偏好 | `preferences`（`scope="umo"`，`scope_id`=UMO，如 `sel_conv_id`，见 `astrbot/core/utils/shared_preferences.py:381`） | scope_id 内嵌 creator 与 session_id |

checkpoint id 存在于 `conversations.content` JSON 内部，整体拷贝后 `llm_checkpoint_id` / `base_checkpoint_id` 的内部引用自洽，无需改写。

### 3.2 可复用的既有模式

- zip + manifest + sha256 校验和：`astrbot/core/backup/exporter.py`
- 路径越界校验：`astrbot/core/backup/importer.py:63` `_validate_path_within`
- 版本兼容判断（主版本必须一致）：`astrbot/core/backup/importer.py:42` `_get_major_version` + `VersionComparator`
- 上传文件名消毒：`astrbot/dashboard/services/chat_service.py:57` `sanitize_upload_filename`
- 消息 parts 中附件 id 提取：`astrbot/dashboard/services/chat_service.py:973` `extract_attachment_ids`
- 鉴权与归属校验：`require_chat_scope` + `session.creator != username → Permission denied`（`chat_service.py:2478`）
- 前端三段式导入对话框：`dashboard/src/components/shared/BackupDialog.vue`（选文件 → 预检确认 → 结果）

## 4. 方案选择

- **方案 A（选定）：会话级导出包（zip）+ 导入到导入者账户。** 复用备份模块模式；A 导出文件、B 导入，天然支持同实例跨账户与跨实例；导入者无需管理员权限。
- 方案 B（否决）：服务端管理员直接复制，无中间文件。仅限同一实例、需要新的管理员 UI 与权限，无法跨实例。
- 方案 C（否决）：给全量备份加用户过滤。备份导入语义是清库替换，部分数据导入需另做合并模式，影响面过大。

## 5. 详细设计

### 5.1 导出包格式

zip 文件名：`astrbot_chatui_export_{YYYYMMDD_HHMMSS}.zip`

```
manifest.json                              # 清单 + 校验和
export.json                                # 全部行数据
files/attachments/{attachment_id}{ext}     # 附件文件实体（ext 取自原文件后缀）
```

`manifest.json`：

```json
{
  "kind": "astrbot-chatui-export",
  "format_version": 1,
  "astrbot_version": "<导出实例版本>",
  "exported_at": "<ISO8601 UTC>",
  "sessions": [
    {
      "original_session_id": "...",
      "display_name": "...",
      "original_creator": "...",
      "stats": {"messages": 0, "conversations": 0, "threads": 0, "attachments": 0, "attachment_bytes": 0}
    }
  ],
  "warnings": ["..."],
  "checksums": {"export.json": "sha256:...", "files/attachments/...": "sha256:..."}
}
```

`export.json`（`sessions` 数组，每个元素对应一个会话；字段与数据库行一一对应，datetime 序列化为 ISO8601 UTC 字符串）：

```json
{
  "sessions": [
    {
      "session": {"session_id": "...", "display_name": "...", "is_group": 0, "archived": 0, "created_at": "...", "updated_at": "..."},
      "conversations": [{"conversation_id": "...", "platform_id": "...", "user_id": "...", "content": [], "title": "...", "persona_id": "...", "token_usage": 0, "created_at": "...", "updated_at": "..."}],
      "history": [{"id": 0, "sender_id": "...", "sender_name": "...", "content": {}, "llm_checkpoint_id": "...", "created_at": "..."}],
      "threads": [
        {
          "thread": {"thread_id": "...", "creator": "...", "parent_session_id": "...", "parent_message_id": 0, "base_checkpoint_id": "...", "selected_text": "..."},
          "history": [],
          "conversations": []
        }
      ],
      "preferences": [{"scope": "umo", "scope_id": "...", "key": "...", "value": {}}],
      "attachments": [{"attachment_id": "...", "type": "image", "mime_type": "image/png", "ext": ".png", "size": 0, "zip_path": "files/attachments/....png"}]
    }
  ]
}
```

导出规则：

- 只允许 `platform_id == "webchat"` 的会话；`history` 为该会话全部 `platform_message_history` 行（不分页取全量，参照 `chat_service.py` 中 `page_size=100000` 的既有做法），按 `(created_at, id)` 排序。
- `preferences` 收集 `scope="umo"` 且 `scope_id` 属于 {会话 UMO} ∪ {各线程 UMO} 的全部行（包含 `sel_conv_id` 及文件访问模式等会话级状态）。
- 附件实体缺失（磁盘上已被删）时：`attachments` 元数据照常写入，但 zip 中无对应文件，`manifest.warnings` 记录一条；导入端据此跳过该附件行。
- `original_creator` 仅作展示；导入后归属以导入者为准。

### 5.2 导出 API

- 路由：`GET /api/v1/chat/sessions/{session_id}/export`，定义在新路由文件 `astrbot/dashboard/api/session_transfer.py` 并在 `astrbot/dashboard/api/router.py` 注册（模式同 `chat_router`），业务逻辑放独立 service（见 §7）。
- 校验：`require_chat_scope`；会话存在、`platform_id=="webchat"`、`session.creator == auth.username`，否则按现有 `ChatServiceError → ApiError` 模式报权限错误。
- 流程：收集行数据 → `extract_attachment_ids` 从 parts 提取附件 → 逐个写入 zip（缺失记 warning）→ 生成 manifest 与校验和。
- 产物先写入 `data/temp/` 下的临时 zip，用 `FileResponse` 返回（`Content-Disposition: attachment`），并在 FastAPI BackgroundTask 中删除临时文件。

### 5.3 导入 API（两步，模式同 backups 的 check/import）

1. `POST /api/v1/chat/sessions/import`（multipart 上传 zip，`require_chat_scope`）
   - 大小与格式校验后存入 `data/temp/session_import_{uuid}.zip`，解析并校验：
     - `manifest.kind == "astrbot-chatui-export"`，`format_version == 1`（不认识则拒绝）；
     - `astrbot_version` 主版本与当前一致（复用 backup 的 `_get_major_version` + `VersionComparator`，主版本不同拒绝）；
     - zip 安全限制（见 §5.6）。
   - 返回预览（不落库）：`{import_id, sessions: [{display_name, original_creator, stats, warnings}], version_status, can_import}`。`import_id` 由 service 持有 `temp_id → 临时文件路径` 的内存映射，TTL 30 分钟，过期或进程重启后 confirm 报错。
2. `POST /api/v1/chat/sessions/import/confirm`（body: `{import_id}`）
   - 先对临时 zip 重跑与第 1 步相同的 manifest/版本/zip 安全校验（防止预检后文件状态变化），再按 §5.4 落库，`creator = auth.username`，返回 `{created: [{new_session_id, display_name}], warnings, errors}`。预检判定不可导入（`can_import == false`）时 confirm 直接拒绝。
   - 无论成功失败，confirm 结束后删除临时 zip。

### 5.4 ID 与引用重映射（导入核心）

导入按会话逐个处理，单个会话失败（事务回滚）不影响其他会话。顺序与规则：

1. 预生成映射：`old_session_id → 新 uuid4`；每个线程 `old_thread_id → 新 uuid4`；每个 UMO（会话与线程）按 `build_webchat_unified_msg_origin` 的模板重建为新 UMO（message_type 依原会话 `is_group` 取 `FriendMessage`/`GroupMessage`）：`{platform}:{type}:{platform}!{导入者}!{新id}`。
2. 附件映射：对每个附件 `attachment_id`，查询目标库——已存在同 id（例如重复导入同一包）→ 生成新 `attachment_id`；不存在 → 保留原 id。得到 `old_attachment_id → new_attachment_id` 映射。
3. 改写消息 parts：遍历主 history 与线程 history 的 content JSON，把 image/file/record/video part 中的 `attachment_id` 按映射替换；其余字段（`filename`、`stored_filename`、文本）原样保留。
4. 落库顺序：
   - `attachments`：文件写入 `data/attachments/{new_attachment_id}{ext}`（`ext` 须匹配 `^\.[A-Za-z0-9]{1,10}$`，不合规则跳过该附件并记 warning），插入 `attachments` 行（`path` = 本机新绝对路径，`type`/`mime_type` 取自清单）。zip 中缺失文件的附件：跳过行并记 warning（消息 part 引用仍指向该 id，渲染时与现状一致地显示加载失败）。
   - `conversations`：`conversation_id` 换新 uuid4，`user_id` = 映射后的 UMO，`content`/`title`/`persona_id`/`token_usage` 原样；线程的 conversations 同理（user_id = 线程新 UMO）。
   - 主 `platform_message_history`：保留 `sender_id/sender_name/content/llm_checkpoint_id/created_at`，插入后建立 `old history id → 新 id` 映射。
   - `webchat_threads`：`thread_id` 换新，`creator` = 导入者，`parent_session_id` = 新会话 id，`parent_message_id` 按映射回填（映射缺失则丢弃该线程并记 warning），`base_checkpoint_id`/`selected_text` 原样；随后插入线程自己的 history（`platform_id="webchat_thread"`，`user_id` = 新 thread_id）。
   - `preferences`：`scope_id` 替换为新 UMO 后原样插入。

### 5.5 前端 UI

- **导出**：`ProjectList.vue` 会话行操作区（现有 edit/archive/delete 按钮旁，`ProjectList.vue:220-263`）新增 Download 图标按钮，emit `exportSession(session_id)` 到 `Chat.vue`；`api/v1.ts` 新增 `exportSession(session_id)`，用 axios blob（带 Authorization 头）下载并经 object URL 触发保存。
- **导入**：会话列表操作区（与"归档会话"入口同区）新增"导入会话"按钮，打开新组件 `ImportSessionsDialog.vue`，交互照搬 BackupDialog import 页签的三段式：选择文件 → 预览（会话列表、消息数、附件大小、版本警告）→ 确认导入 → 结果（错误/警告展示）。成功后 emit，`Chat.vue` 刷新会话列表并切换到第一个新会话。
- 对话框遵循项目规范：标题 `text-h3 pa-4 pb-0 pl-6`，按钮 `variant="text"/"tonal"`。
- i18n：`dashboard/src/i18n/locales/{en-US,zh-CN,ru-RU}/features/chat.json` 新增导出/导入相关 key（三份同步，俄语参照现有条目风格翻译）。
- API 变更后执行 `cd dashboard && pnpm generate:api` 重新生成客户端。

### 5.6 安全

- 导入 zip 的上传体积上限 512 MB；解析时强制：条目数 ≤ 20000、单条目解压后 ≤ 512 MB、总解压量 ≤ 2 GB（常量定义在 service 模块，防 zip bomb）。
- 导入**不按 zip 条目路径解压任何文件**：仅读取 `export.json`/manifest 声明的附件 `zip_path`，且要求其必须以 `files/attachments/` 开头、不含 `..`、与附件元数据一一对应；落盘文件名一律为 `{new_attachment_id}{ext}`，目录固定为 `data/attachments/`。因此不存在路径穿越面（附件实体读取仍复用 `_validate_path_within` 做二次防御）。
- 导出与导入均要求 `require_chat_scope`；导出校验会话归属（仅本人会话可导出），导入一律归属到当前登录用户，不可能写他人会话。
- 附件 id 重映射保证导入不会覆盖目标库中已有附件记录。

### 5.7 边界情况

- 导出瞬间正在生成的回复不在包内（DB 快照语义）。
- 重复导入同一 zip → 生成内容重复的新会话（新 id），不做去重；预览界面明示"将创建新会话"。
- 附件文件在导出端已丢失 → warning + 导入后图片加载失败（与现存"文件被删后历史图片打不开"的行为一致）。
- 导入端版本过旧（主版本不同）→ 拒绝并提示。
- 会话内 persona 在目标实例不存在 → `persona_id` 保留原值，运行时回退默认人格；在预览 warnings 中提示。
- 空会话（无消息）允许导出导入。

## 6. 测试计划

后端（必需）：

1. 重映射纯函数单测：UMO 重建、附件 id 映射（含已存在冲突）、parts 改写、`parent_message_id` 回填与缺映射丢弃。
2. 导入回路集成测试：种子库导出 → 导入到另一账户 → 断言 sessions/conversations/history/threads/attachments/preferences 行数与引用一致性，继续对话上下文完整。
3. zip 安全测试：`zip_path` 穿越/伪造条目被拒，超限条目被拒。
4. 权限测试：非 owner 导出被拒；导入后 `creator` 为导入者。

前端：不新增组件测试（对话框为薄封装，逻辑在服务端），保持现有 vitest 套件通过；`pnpm generate:api` 后类型检查通过。

## 7. 实现文件清单

后端：

- 新增 `astrbot/dashboard/services/session_transfer_service.py`：导出/导入/重映射（重映射做成模块内纯函数以便单测）。
- 新增 `astrbot/dashboard/api/session_transfer.py` 路由（`router.py` 注册，模式同 `chat_router`）。
- `astrbot/dashboard/schemas.py`：`ChatSessionImportConfirmRequest` 等请求/响应模型。
- services 容器注册新 service（`request.app.state.services`）。
- 上传文件名消毒等直接 import 复用 `chat_service.py` 既有函数，不在新 service 内复制。

前端：

- `dashboard/src/api/v1.ts` + 重新生成的 openapi client。
- `dashboard/src/components/chat/ProjectList.vue`（导出按钮 + emit）。
- 新增 `dashboard/src/components/chat/ImportSessionsDialog.vue`。
- `dashboard/src/components/chat/Chat.vue`（接线：exportSession、import 入口、导入后刷新）。
- i18n 三份 `features/chat.json`。

## 8. 后续可扩展项（本设计不实现）

- 会话列表多选批量导出 / 按用户全部导出。
- 管理员"服务端直接复制到用户 B"（方案 B）作为同实例快捷路径。
- 导出包内可选拆分（仅消息流不含 LLM 上下文）。
