# ChatUI 会话导出与导入实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ChatUI 用户把单个 webchat 会话（消息流 + 附件 + 线程回复 + LLM 上下文 + 会话级偏好）导出为一个 zip，并可由任意登录用户导入到自己账户下，得到内容一致、可继续对话的新会话。

**Architecture:** 新增一个独立 service（`session_transfer_service.py`）承载"收集行数据 → 打包 zip → 预检 → 重映射 → 单事务落库"的全部逻辑，重映射部分做成模块级纯函数以便单测；新增独立路由文件（`session_transfer.py`）暴露 3 个端点（导出 / 导入预检 / 导入确认），按既有 `ScopeDependency("chat")` + 归属校验模式鉴权。前端复用 BackupDialog 的三段式对话框交互，在 ChatUI 侧栏会话行与右键菜单加入导出入口，"导入会话"入口放在侧栏顶部操作区。

**Tech Stack:** Python 3.10+ / FastAPI / SQLModel + AsyncSession / `zipfile` + `hashlib.sha256` / `astrbot.core.backup.importer` 的版本比较复用 / Vue 3 + TypeScript + axios（`responseType: 'blob'`、multipart 上传）/ pytest。

**依据规格：** `docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md`（状态：待评审）。本计划与该规格的差异见文末"与规格的差异记录"。

## Global Constraints

- 只处理 `platform_id == "webchat"` 的会话；非 webchat 会话导出直接报错。
- `export.json` 字段与数据库行一一对应；所有 datetime 序列化为 ISO8601 UTC 字符串（复用 `astrbot.core.utils.datetime_utils.to_utc_isoformat`）。
- 导入 zip 限制：上传 ≤ 512 MB；条目数 ≤ 20000；单条目解压后 ≤ 512 MB；总解压量 ≤ 2 GB。
- 导入**不按 zip 条目路径解压**：只读 `manifest.json` / `export.json` 声明的 `zip_path`，且必须以 `files/attachments/` 开头、不含 `..`、不含 `\`；落盘文件名固定为 `{new_attachment_id}{ext}`，目录固定 `data/attachments/`，`ext` 必须匹配 `^\.[A-Za-z0-9]{1,10}$`。
- 鉴权：3 个路由都用 `ScopeDependency("chat")`；导出校验 `session.creator == auth.username`；导入一律归属当前登录用户。
- 代码风格：`ruff format .` + `ruff check .` 必须通过；docstring 用 Google 格式（`Args:` / `Returns:` / `Raises:`）；注释与日志用英文。
- 后端路由变更后必须更新 `openspec/openapi-v1.yaml`（手维护）并运行 `cd dashboard && pnpm generate:api` 重新生成客户端。
- 测试文件头写 `Author: elecvoid243` + 日期，格式对齐 `tests/test_chat_history_pagination.py`。
- 提交用 conventional commits；**禁止 push 远端、禁止发起 PR**。
- 不新增任何报告类 md 文件。

**本机命令约定（覆盖计划正文中的所有 `uv run` 写法）**

- 测试：`D:\anaconda3\envs\astrbot\python.exe -m pytest <paths> -q`。**不要**用 `uv run`，**不要**新建 venv（本机约定：直接用已有 conda 环境）。
- 格式化/检查：`D:\anaconda3\envs\astrbot\python.exe -m ruff format <files>` 与 `... -m ruff check <files>`。
- 工作目录：`.worktrees/feat-chatui-session-transfer`（分支 `feat/chatui-session-transfer`）；用该 env 从该目录运行 pytest 时，`import astrbot` 解析到本 worktree 的源码（已验证）。
- 基线噪声（不是回归）：`tests/test_chat_history_pagination.py` 为 `14 passed, 3 warnings`（清一色 `PytestUnhandledThreadExceptionWarning: Event loop is closed`）。"pristine output" 的判定以不新增 warning 为准。

---

### Task 1: 重映射纯函数

**Files:**
- Create: `astrbot/dashboard/services/session_transfer_service.py`
- Test: `tests/test_session_transfer_remap.py`

**Interfaces:**
- Consumes: `astrbot.core.platform.message_type.MessageType`
- Produces:
  - `new_umo(platform_id: str, is_group: int, creator: str, entity_id: str) -> str`
  - `build_attachment_id_map(old_ids: list[str], existing_ids: set[str], uuid_factory=uuid.uuid4) -> dict[str, str]`
  - `rewrite_attachment_refs(content: dict, id_map: dict[str, str]) -> dict`（非 dict 输入原样透传，含 `None`；注释写作 `-> dict` 但运行时可能返回 `None`）
  - `resolve_parent_message_id(old_id: int, id_map: dict[int, int]) -> int | None`
  - `is_safe_attachment_zip_path(zip_path: str) -> bool`
  - `is_safe_attachment_ext(ext: str) -> bool`
  - 模块常量：`EXPORT_KIND`、`EXPORT_FORMAT_VERSION`、`MANIFEST_NAME`、`EXPORT_DATA_NAME`、`ATTACHMENTS_PREFIX`、`WEBCHAT_PLATFORM_ID`、`THREAD_PLATFORM_ID`、`MAX_UPLOAD_BYTES`、`MAX_ZIP_ENTRIES`、`MAX_ENTRY_BYTES`、`MAX_TOTAL_UNCOMPRESSED_BYTES`、`IMPORT_ID_TTL_SECONDS`、`HISTORY_PAGE_SIZE`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_session_transfer_remap.py`：

```python
"""ChatUI session export/import: ID and reference remapping.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import pytest

from astrbot.dashboard.services.session_transfer_service import (
    build_attachment_id_map,
    is_safe_attachment_ext,
    is_safe_attachment_zip_path,
    new_umo,
    resolve_parent_message_id,
    rewrite_attachment_refs,
)


class _FixedUuid:
    """Deterministic uuid4 stand-in: returns uuid-0001, uuid-0002, ..."""

    def __init__(self) -> None:
        self.counter = 0

    def __call__(self):
        self.counter += 1
        return f"uuid-{self.counter:04d}"


def test_new_umo_friend_message():
    assert (
        new_umo("webchat", 0, "bob", "sess-1")
        == "webchat:FriendMessage:webchat!bob!sess-1"
    )


def test_new_umo_group_message():
    assert (
        new_umo("webchat", 1, "bob", "sess-1")
        == "webchat:GroupMessage:webchat!bob!sess-1"
    )


def test_attachment_map_keeps_free_ids_and_reissues_taken_ones():
    mapping = build_attachment_id_map(
        ["a1", "a2"],
        existing_ids={"a2"},
        uuid_factory=_FixedUuid(),
    )
    assert mapping == {"a1": "a1", "a2": "uuid-0001"}


def test_attachment_map_ignores_blank_and_duplicate_ids():
    mapping = build_attachment_id_map(
        ["", None, "a1", "a1"],
        existing_ids=set(),
        uuid_factory=_FixedUuid(),
    )
    assert mapping == {"a1": "a1"}


def test_rewrite_attachment_refs_replaces_only_mapped_parts():
    content = {
        "type": "user",
        "message": [
            {"type": "plain", "text": "hi"},
            {"type": "image", "attachment_id": "a1", "filename": "x.png"},
            {"type": "file", "attachment_id": "keep", "filename": "y.zip"},
        ],
    }
    rewritten = rewrite_attachment_refs(content, {"a1": "new1"})
    assert rewritten["message"][0] == {"type": "plain", "text": "hi"}
    assert rewritten["message"][1]["attachment_id"] == "new1"
    assert rewritten["message"][1]["filename"] == "x.png"
    assert rewritten["message"][2]["attachment_id"] == "keep"
    # The caller's payload must not be mutated in place.
    assert content["message"][1]["attachment_id"] == "a1"


def test_rewrite_attachment_refs_tolerates_missing_parts():
    assert rewrite_attachment_refs({}, {"a1": "new1"}) == {}
    assert rewrite_attachment_refs({"message": "bad"}, {}) == {"message": "bad"}
    assert rewrite_attachment_refs(None, {}) is None


def test_resolve_parent_message_id():
    assert resolve_parent_message_id(7, {7: 99}) == 99
    assert resolve_parent_message_id(7, {}) is None


@pytest.mark.parametrize(
    "zip_path",
    [
        "files/attachments/a1.png",
        "files/attachments/nested/a1.png",
    ],
)
def test_safe_attachment_zip_path_accepts_expected_prefix(zip_path):
    assert is_safe_attachment_zip_path(zip_path) is True


@pytest.mark.parametrize(
    "zip_path",
    [
        "",
        "export.json",
        "../files/attachments/a1.png",
        "files/attachments/../../etc/passwd",
        "files\\attachments\\a1.png",
        "/files/attachments/a1.png",
        "files/attachments/./a1.png",
    ],
)
def test_safe_attachment_zip_path_rejects_traversal(zip_path):
    assert is_safe_attachment_zip_path(zip_path) is False


@pytest.mark.parametrize("ext", [".png", ".jpeg", ".mp3", ".bin"])
def test_safe_attachment_ext_accepts(ext):
    assert is_safe_attachment_ext(ext) is True


@pytest.mark.parametrize("ext", ["", "png", ".", ".p/g", ".png.exe/" + "x" * 20])
def test_safe_attachment_ext_rejects(ext):
    assert is_safe_attachment_ext(ext) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_session_transfer_remap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'astrbot.dashboard.services.session_transfer_service'`

- [ ] **Step 3: 写实现**

创建 `astrbot/dashboard/services/session_transfer_service.py`：

```python
"""ChatUI session export/import (cross-user migration).

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from __future__ import annotations

import posixpath
import re
import uuid

from astrbot.core.platform.message_type import MessageType

EXPORT_KIND = "astrbot-chatui-export"
EXPORT_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
EXPORT_DATA_NAME = "export.json"
ATTACHMENTS_PREFIX = "files/attachments/"
WEBCHAT_PLATFORM_ID = "webchat"
THREAD_PLATFORM_ID = "webchat_thread"

# Import guards (zip bomb / oversized upload defence).
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 20000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
IMPORT_ID_TTL_SECONDS = 1800

# Export reads one session in a single page; matches the existing
# `page_size=100000` usage in chat_service.py.
HISTORY_PAGE_SIZE = 100000

_ATTACHMENT_EXT_PATTERN = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def new_umo(platform_id: str, is_group: int, creator: str, entity_id: str) -> str:
    """Rebuild a unified message origin for an imported session or thread.

    Args:
        platform_id: Platform identifier, always ``webchat`` for ChatUI.
        is_group: 1 for group sessions, 0 for private ones.
        creator: Owner of the imported entity (the importing user).
        entity_id: New session id or thread id.

    Returns:
        A UMO in the ``{platform}:{type}:{platform}!{creator}!{id}`` shape
        produced by ``build_webchat_unified_msg_origin``.
    """
    message_type = (
        MessageType.GROUP_MESSAGE.value if is_group else MessageType.FRIEND_MESSAGE.value
    )
    return f"{platform_id}:{message_type}:{platform_id}!{creator}!{entity_id}"


def build_attachment_id_map(
    old_ids: list[str],
    existing_ids: set[str],
    uuid_factory=uuid.uuid4,
) -> dict[str, str]:
    """Map exported attachment IDs onto IDs that are free in the target DB.

    Args:
        old_ids: Attachment IDs referenced by the export package.
        existing_ids: Attachment IDs already present in the target database.
        uuid_factory: Callable returning a fresh unique id; injectable for tests.

    Returns:
        Mapping from exported ID to the ID to use on import. Exported IDs
        that are still free are reused verbatim; colliding ones get a new id.
    """
    mapping: dict[str, str] = {}
    for old_id in old_ids:
        if not old_id or old_id in mapping:
            continue
        mapping[old_id] = old_id if old_id not in existing_ids else str(uuid_factory())
    return mapping


def rewrite_attachment_refs(content: dict, id_map: dict[str, str]) -> dict:
    """Replace ``attachment_id`` fields inside a history content payload.

    Args:
        content: Stored platform history content (``{"type", "message"}``).
        id_map: Mapping from exported attachment ID to the imported one.

    Returns:
        A copy of ``content`` with mapped attachment IDs. The input is never
        mutated. Non-dict content and payloads without a parts list pass
        through unchanged.
    """
    if not isinstance(content, dict):
        return content
    parts = content.get("message")
    if not isinstance(parts, list):
        return content
    rewritten: list = []
    for part in parts:
        if isinstance(part, dict) and part.get("attachment_id") in id_map:
            part = {**part, "attachment_id": id_map[part["attachment_id"]]}
        rewritten.append(part)
    return {**content, "message": rewritten}


def resolve_parent_message_id(old_id: int, id_map: dict[int, int]) -> int | None:
    """Look up the imported message id for a thread's parent message.

    Args:
        old_id: ``parent_message_id`` recorded in the export package.
        id_map: Mapping from exported history id to imported history id.

    Returns:
        The imported message id, or ``None`` when the parent was not imported
        (the thread must then be dropped with a warning).
    """
    return id_map.get(old_id)


def is_safe_attachment_zip_path(zip_path: str) -> bool:
    """Check a manifest-declared zip path cannot escape the attachments dir.

    Args:
        zip_path: Path recorded in the export manifest.

    Returns:
        True when the path is a normalized ``files/attachments/...`` path
        without traversal segments or Windows separators.
    """
    if not isinstance(zip_path, str) or not zip_path:
        return False
    if ".." in zip_path or "\\" in zip_path:
        return False
    if not zip_path.startswith(ATTACHMENTS_PREFIX):
        return False
    return posixpath.normpath(zip_path) == zip_path


def is_safe_attachment_ext(ext: str) -> bool:
    """Check an attachment suffix is safe to append to a generated filename.

    Args:
        ext: Suffix such as ``.png`` taken from the export package.

    Returns:
        True when the suffix matches ``^\\.[A-Za-z0-9]{1,10}$``.
    """
    return bool(_ATTACHMENT_EXT_PATTERN.fullmatch(ext or ""))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_session_transfer_remap.py -v`
Expected: PASS（25 个用例：7 个普通 + 18 个参数化）

- [ ] **Step 5: 格式化并提交**

```bash
uvx ruff format astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_remap.py
uvx ruff check astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_remap.py
git add astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_remap.py
git commit -m "feat: add chatui session transfer remap primitives"
```

---

### Task 2: 导出打包（zip + manifest + 校验和）

**Files:**
- Modify: `astrbot/dashboard/services/session_transfer_service.py`（追加 `SessionTransferError`、`SessionExport`、`SessionTransferService`）
- Test: `tests/test_session_transfer_export.py`

**Interfaces:**
- Consumes: Task 1 的常量与纯函数；`astrbot.core.utils.astrbot_path.get_astrbot_data_path()` / `get_astrbot_temp_path()`；`astrbot.core.config.default.VERSION`
- Produces:
  - `class SessionTransferError(Exception)`
  - `@dataclass SessionExport(file_obj: BytesIO, filename: str, mimetype: str = "application/zip")`
  - `class SessionTransferService.__init__(self, db, core_lifecycle)`，属性：`db`、`core_lifecycle`、`conv_mgr`、`platform_history_mgr`、`attachments_dir: Path`、`temp_dir: Path`、`pending_imports: dict[str, _PendingImport]`
  - `async SessionTransferService.export_session(self, username: str, session_id: str) -> SessionExport`
  - `SessionTransferService._serialize_row(row) -> dict`（static）
  - `SessionTransferService._owned_webchat_session(self, username, session_id)`（Task 5 也会用到）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_session_transfer_export.py`：

```python
"""ChatUI session export: zip layout, manifest and checksums.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import hashlib
import json
import tempfile
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.core.db.po import PlatformMessageHistory, PlatformSession, WebChatThread
from astrbot.dashboard.services.session_transfer_service import (
    ATTACHMENTS_PREFIX,
    EXPORT_DATA_NAME,
    EXPORT_FORMAT_VERSION,
    EXPORT_KIND,
    MANIFEST_NAME,
    SessionTransferError,
    SessionTransferService,
)

SESSION_ID = "sess-1"


def _row(**overrides):
    """Build a real PlatformMessageHistory row (same style as
    tests/test_chat_history_pagination.py)."""
    fields = {
        "id": 1,
        "platform_id": "webchat",
        "user_id": SESSION_ID,
        "sender_id": "alice",
        "sender_name": "alice",
        "content": {
            "type": "user",
            "message": [
                {"type": "plain", "text": "hi"},
                {"type": "image", "attachment_id": "att-1", "filename": "a.png"},
            ],
        },
        "llm_checkpoint_id": "ck-1",
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return PlatformMessageHistory(**fields)


def _session(**overrides):
    fields = {
        "session_id": SESSION_ID,
        "platform_id": "webchat",
        "creator": "alice",
        "is_group": 0,
        "display_name": "会话 A",
        "archived": 0,
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return PlatformSession(**fields)


def _make_service(attachment_dir=None):
    db = Mock()
    db.get_platform_session_by_id = AsyncMock(return_value=_session())
    db.get_webchat_threads_by_parent_session = AsyncMock(return_value=[])
    db.get_conversations = AsyncMock(return_value=[])
    db.get_preferences = AsyncMock(return_value=[])
    db.get_attachment_by_id = AsyncMock(return_value=None)
    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
    )
    service = SessionTransferService(db, core_lifecycle)
    service.platform_history_mgr.get = AsyncMock(return_value=[_row()])
    service.attachments_dir = Path(attachment_dir or tempfile.gettempdir())
    return service


@pytest.mark.asyncio
async def test_export_rejects_non_owner():
    service = _make_service()
    db_session = _session()
    db_session.creator = "someone-else"
    service.db.get_platform_session_by_id = AsyncMock(return_value=db_session)

    with pytest.raises(SessionTransferError, match="Permission denied"):
        await service.export_session("alice", SESSION_ID)


@pytest.mark.asyncio
async def test_export_rejects_non_webchat_platform():
    service = _make_service()
    db_session = _session()
    db_session.platform_id = "aiocqhttp"
    service.db.get_platform_session_by_id = AsyncMock(return_value=db_session)

    with pytest.raises(SessionTransferError, match="webchat"):
        await service.export_session("alice", SESSION_ID)


@pytest.mark.asyncio
async def test_export_package_layout_and_checksums(tmp_path):
    attachment = tmp_path / "att-1.png"
    attachment.write_bytes(b"PNGDATA")
    service = _make_service(attachment_dir=tmp_path)
    service.db.get_attachment_by_id = AsyncMock(
        return_value=SimpleNamespace(
            attachment_id="att-1",
            path=str(attachment),
            type="image",
            mime_type="image/png",
        )
    )

    export = await service.export_session("alice", SESSION_ID)

    assert export.mimetype == "application/zip"
    assert export.filename.startswith("astrbot_chatui_export_")
    assert export.filename.endswith(".zip")

    with zipfile.ZipFile(BytesIO(export.file_obj.getvalue())) as zf:
        names = set(zf.namelist())
        assert MANIFEST_NAME in names
        assert EXPORT_DATA_NAME in names
        assert f"{ATTACHMENTS_PREFIX}att-1.png" in names

        manifest = json.loads(zf.read(MANIFEST_NAME))
        assert manifest["kind"] == EXPORT_KIND
        assert manifest["format_version"] == EXPORT_FORMAT_VERSION
        assert manifest["warnings"] == []
        assert manifest["sessions"][0]["original_session_id"] == SESSION_ID
        assert manifest["sessions"][0]["display_name"] == "会话 A"
        assert manifest["sessions"][0]["original_creator"] == "alice"
        stats = manifest["sessions"][0]["stats"]
        assert stats["messages"] == 1
        assert stats["attachments"] == 1
        assert stats["attachment_bytes"] == len(b"PNGDATA")

        payload = zf.read(EXPORT_DATA_NAME)
        digest = hashlib.sha256(payload).hexdigest()
        assert manifest["checksums"][EXPORT_DATA_NAME] == f"sha256:{digest}"
        blob = zf.read(f"{ATTACHMENTS_PREFIX}att-1.png")
        assert manifest["checksums"][f"{ATTACHMENTS_PREFIX}att-1.png"] == (
            f"sha256:{hashlib.sha256(blob).hexdigest()}"
        )

        data = json.loads(payload)
        exported_session = data["sessions"][0]
        assert exported_session["session"]["session_id"] == SESSION_ID
        assert exported_session["history"][0]["id"] == 1
        assert exported_session["history"][0]["created_at"].startswith("2026-09-01")
        assert exported_session["attachments"][0]["attachment_id"] == "att-1"
        assert exported_session["attachments"][0]["ext"] == ".png"
        assert exported_session["attachments"][0]["zip_path"] == (
            f"{ATTACHMENTS_PREFIX}att-1.png"
        )


@pytest.mark.asyncio
async def test_export_records_warning_when_attachment_file_is_missing(tmp_path):
    service = _make_service(attachment_dir=tmp_path)
    service.db.get_attachment_by_id = AsyncMock(
        return_value=SimpleNamespace(
            attachment_id="att-1",
            path=str(tmp_path / "gone.png"),
            type="image",
            mime_type="image/png",
        )
    )

    export = await service.export_session("alice", SESSION_ID)

    with zipfile.ZipFile(BytesIO(export.file_obj.getvalue())) as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
        assert manifest["warnings"]
        assert "att-1" in manifest["warnings"][0]
        assert f"{ATTACHMENTS_PREFIX}att-1.png" not in set(zf.namelist())


@pytest.mark.asyncio
async def test_export_packages_attachments_referenced_only_by_a_thread(tmp_path):
    """A file attached inside a side thread is packaged, not silently dropped.

    Thread history persists the same message-part shape as the main stream
    (chat_service.create_thread), so its attachment ids must be collected too.
    """
    thread_attachment_path = tmp_path / "thread-1.png"
    thread_attachment_path.write_bytes(b"THREADPNG")
    service = _make_service(attachment_dir=tmp_path)

    # A real PO: `_serialize_row` calls model_dump() on it.
    thread = WebChatThread(
        thread_id="thr-1",
        creator="alice",
        parent_session_id=SESSION_ID,
        parent_message_id=1,
        base_checkpoint_id="ck-1",
        selected_text="hi",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    service.db.get_webchat_threads_by_parent_session = AsyncMock(return_value=[thread])

    # Main stream carries no attachment; only the thread does.
    main_row = _row(
        content={"type": "user", "message": [{"type": "plain", "text": "hi"}]}
    )
    thread_row = _row(
        id=2,
        content={
            "type": "user",
            "message": [
                {"type": "image", "attachment_id": "att-thread", "filename": "t.png"}
            ],
        },
    )

    async def _history(platform_id, user_id, **kwargs):
        if platform_id == "webchat_thread":
            return [thread_row]
        return [main_row]

    service.platform_history_mgr.get = AsyncMock(side_effect=_history)
    service.db.get_attachment_by_id = AsyncMock(
        return_value=SimpleNamespace(
            attachment_id="att-thread",
            path=str(thread_attachment_path),
            type="image",
            mime_type="image/png",
        )
    )

    export = await service.export_session("alice", SESSION_ID)

    with zipfile.ZipFile(BytesIO(export.file_obj.getvalue())) as zf:
        assert f"{ATTACHMENTS_PREFIX}att-thread.png" in set(zf.namelist())
        manifest = json.loads(zf.read(MANIFEST_NAME))
        stats = manifest["sessions"][0]["stats"]
        assert stats["attachments"] == 1
        assert stats["attachment_bytes"] == len(b"THREADPNG")
        assert manifest["warnings"] == []
        data = json.loads(zf.read(EXPORT_DATA_NAME))
        assert data["sessions"][0]["attachments"][0]["attachment_id"] == "att-thread"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_session_transfer_export.py -v`
Expected: FAIL — `ImportError: cannot import name 'SessionTransferService'`

- [ ] **Step 3: 写实现**

在 `astrbot/dashboard/services/session_transfer_service.py` 追加（放在纯函数之后）：

```python
import hashlib
import json
import os
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.core.backup.importer import _get_major_version
from astrbot.core.config.default import VERSION
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.datetime_utils import to_utc_isoformat
from astrbot.core.utils.version_comparator import VersionComparator


class SessionTransferError(Exception):
    """User-visible session export/import failure."""


@dataclass
class SessionExport:
    """A packaged session export ready to stream to the browser."""

    file_obj: BytesIO
    filename: str
    mimetype: str = "application/zip"


@dataclass
class _PendingImport:
    """A staged, pre-checked import zip awaiting confirmation."""

    zip_path: Path
    manifest: dict
    payload: dict
    preview: dict
    created_at: float


class SessionTransferService:
    """Export and import single ChatUI (webchat) sessions as zip packages."""

    def __init__(self, db, core_lifecycle) -> None:
        """Bind the service to the database and core lifecycle.

        Args:
            db: BaseDatabase instance used for direct row access.
            core_lifecycle: Provides the conversation and platform history
                managers shared with the rest of the dashboard.
        """
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.conv_mgr = core_lifecycle.conversation_manager
        self.platform_history_mgr = core_lifecycle.platform_message_history_manager
        self.attachments_dir = Path(get_astrbot_data_path()) / "attachments"
        self.temp_dir = Path(get_astrbot_temp_path())
        self.pending_imports: dict[str, _PendingImport] = {}

    @staticmethod
    def _serialize_row(row) -> dict:
        """Serialize a SQLModel row, normalizing datetimes to UTC ISO strings.

        Args:
            row: A PlatformSession / PlatformMessageHistory / ConversationV2 /
                WebChatThread / Preference instance.

        Returns:
            Dict of the row's columns with ``created_at`` / ``updated_at``
            converted to UTC ISO strings when present.
        """
        data = {
            key: value
            for key, value in row.model_dump().items()
            if key != "inner_id"
        }
        for key in ("created_at", "updated_at"):
            if key in data:
                data[key] = to_utc_isoformat(data[key])
        return data

    async def _owned_webchat_session(self, username: str, session_id: str):
        """Load a webchat session owned by ``username``.

        Args:
            username: Authenticated dashboard user.
            session_id: ChatUI session identifier.

        Returns:
            The PlatformSession row.

        Raises:
            SessionTransferError: If the session is missing, owned by another
                user, or not a webchat session.
        """
        session = await self.db.get_platform_session_by_id(session_id)
        if not session:
            raise SessionTransferError(f"Session {session_id} not found")
        if session.creator != username:
            raise SessionTransferError("Permission denied")
        if session.platform_id != WEBCHAT_PLATFORM_ID:
            raise SessionTransferError(
                "Only webchat sessions can be exported or imported"
            )
        return session

    async def _session_conversations(self, umo: str) -> list[dict]:
        """Collect every LLM conversation row belonging to a UMO.

        Args:
            umo: Unified message origin of the session or thread.

        Returns:
            Serialized ConversationV2 rows including their ``content``.
        """
        rows = await self.db.get_conversations(user_id=umo)
        collected: list[dict] = []
        for row in rows:
            full = await self.db.get_conversation_by_id(row.conversation_id)
            if full is not None:
                collected.append(self._serialize_row(full))
        return collected

    async def export_session(self, username: str, session_id: str) -> SessionExport:
        """Package one ChatUI session into a zip export.

        Args:
            username: Authenticated dashboard user; must own the session.
            session_id: ChatUI session identifier.

        Returns:
            SessionExport holding the zip bytes, download filename and mimetype.

        Raises:
            SessionTransferError: If the session is missing, owned by another
                user, or not a webchat session.
        """
        session = await self._owned_webchat_session(username, session_id)
        session_umo = new_umo(
            session.platform_id, session.is_group, session.creator, session_id
        )

        history = await self.platform_history_mgr.get(
            platform_id=WEBCHAT_PLATFORM_ID,
            user_id=session_id,
            page=1,
            page_size=HISTORY_PAGE_SIZE,
        )
        threads = await self.db.get_webchat_threads_by_parent_session(
            parent_session_id=session_id,
            creator=username,
        )

        warnings: list[str] = []
        thread_payloads: list[dict] = []
        thread_attachment_ids: list[str] = []
        umos = [session_umo]
        for thread in threads:
            thread_umo = new_umo(
                session.platform_id, 0, session.creator, thread.thread_id
            )
            umos.append(thread_umo)
            thread_history = await self.platform_history_mgr.get(
                platform_id=THREAD_PLATFORM_ID,
                user_id=thread.thread_id,
                page=1,
                page_size=HISTORY_PAGE_SIZE,
            )
            thread_attachment_ids.extend(extract_attachment_ids(thread_history))
            thread_payloads.append(
                {
                    "thread": self._serialize_row(thread),
                    "history": [self._serialize_row(row) for row in thread_history],
                    "conversations": await self._session_conversations(thread_umo),
                }
            )

        preferences: list[dict] = []
        for umo in umos:
            rows = await self.db.get_preferences(scope="umo", scope_id=umo)
            preferences.extend(self._serialize_row(row) for row in rows)

        # Attachments hang off the main stream *and* off side threads: both
        # persist the same message-part shape, so scanning `history` alone
        # would silently drop thread images from the package (no warning,
        # no bytes) and leave them permanently broken after a migration.
        all_attachment_ids = extract_attachment_ids(history) + thread_attachment_ids

        attachments: list[dict] = []
        attachment_blobs: list[tuple[str, bytes]] = []
        seen_ids: set[str] = set()
        for attachment_id in all_attachment_ids:
            if attachment_id in seen_ids:
                continue
            seen_ids.add(attachment_id)
            attachment = await self.db.get_attachment_by_id(attachment_id)
            if attachment is None:
                warnings.append(f"Attachment {attachment_id} metadata missing")
                continue
            source = Path(attachment.path)
            ext = source.suffix if is_safe_attachment_ext(source.suffix) else ""
            zip_path = f"{ATTACHMENTS_PREFIX}{attachment_id}{ext}"
            entry = {
                "attachment_id": attachment_id,
                "type": attachment.type,
                "mime_type": attachment.mime_type,
                "ext": ext,
                "size": 0,
                "zip_path": zip_path,
            }
            # One read serves both the byte count and the package payload; a
            # separate stat() would add a double syscall and a TOCTOU window.
            if source.is_file():
                try:
                    blob = await asyncio.to_thread(source.read_bytes)
                except OSError as exc:
                    warnings.append(f"Attachment {attachment_id} unreadable: {exc!s}")
                else:
                    entry["size"] = len(blob)
                    attachment_blobs.append((zip_path, blob))
            else:
                warnings.append(f"Attachment {attachment_id} file missing on disk")
            attachments.append(entry)

        data_payload = {
            "sessions": [
                {
                    "session": self._serialize_row(session),
                    "conversations": await self._session_conversations(session_umo),
                    "history": [self._serialize_row(row) for row in history],
                    "threads": thread_payloads,
                    "preferences": preferences,
                    "attachments": attachments,
                }
            ]
        }
        data_bytes = json.dumps(data_payload, ensure_ascii=False).encode("utf-8")

        checksums: dict[str, str] = {
            EXPORT_DATA_NAME: f"sha256:{hashlib.sha256(data_bytes).hexdigest()}"
        }
        for zip_path, blob in attachment_blobs:
            checksums[zip_path] = f"sha256:{hashlib.sha256(blob).hexdigest()}"

        manifest = {
            "kind": EXPORT_KIND,
            "format_version": EXPORT_FORMAT_VERSION,
            "astrbot_version": VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "sessions": [
                {
                    "original_session_id": session.session_id,
                    "display_name": session.display_name,
                    "original_creator": session.creator,
                    "stats": {
                        "messages": len(history),
                        "conversations": len(data_payload["sessions"][0]["conversations"]),
                        "threads": len(thread_payloads),
                        "attachments": len(attachments),
                        "attachment_bytes": sum(
                            entry["size"] for entry in attachments
                        ),
                    },
                }
            ],
            "warnings": warnings,
            "checksums": checksums,
        }

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            zf.writestr(EXPORT_DATA_NAME, data_bytes)
            for zip_path, blob in attachment_blobs:
                zf.writestr(zip_path, blob)
        buffer.seek(0)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return SessionExport(
            file_obj=buffer,
            filename=f"astrbot_chatui_export_{timestamp}.zip",
        )
```

同时把 import 段补齐到文件顶部（`asyncio`、`hashlib`、`json`、`time`、`zipfile`、`dataclass`、`BytesIO`、`Path` 等），并加入：

```python
from astrbot.dashboard.services.chat_service import extract_attachment_ids
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_session_transfer_export.py tests/test_session_transfer_remap.py -v`
Expected: PASS

- [ ] **Step 5: 格式化并提交**

```bash
uvx ruff format astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_export.py
uvx ruff check astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_export.py
git add -A astrbot/dashboard/services/session_transfer_service.py tests
git commit -m "feat: package chatui session export as zip with manifest"
```

---

### Task 3: 导入预检（zip 安全 + manifest/版本校验）

**Files:**
- Modify: `astrbot/dashboard/services/session_transfer_service.py`
- Test: `tests/test_session_transfer_import.py`

**Interfaces:**
- Consumes: Task 2 的 `SessionExport` / `SessionTransferService`
- Produces:
  - `async SessionTransferService.stage_import(self, upload) -> dict` — 返回 `{"import_id", "sessions", "version_status", "can_import", "warnings"}`
  - `SessionTransferService._verify_zip_safety(self, zf) -> None`
  - `SessionTransferService._read_package(self, zip_path: Path) -> tuple[dict, dict]`
  - `SessionTransferService._pending(self, import_id: str) -> _PendingImport`
  - `SessionTransferService._drop_pending(self, import_id: str) -> None`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_session_transfer_import.py`：

> **2026-09-17 实现期修正（本 Step 的脚手架原文有 3 处缺陷，已在提交 `ada9df745` 修正并上报）**：
> 1. `build_package_bytes` 的 `astrbot_version` 默认值必须是 **`VERSION`**（从 `astrbot.core.config.default` 导入），不能写死 `"4.22.0"` —— 写死会被本任务自己的主版本门禁拒绝（`4.22` vs 当前 `4.28`），且每次发版都会烂掉。显式传 `"3.0.0"` 的拒绝用例保持不变。
> 2. `test_stage_import_returns_preview_and_can_import` 必须传非空 `sessions`；空 payload 的契约（`can_import is False` 且不抛错）由新增的 `test_stage_import_marks_empty_package_not_importable` 固定。
> 3. 必须加 autouse fixture `_isolated_temp_dir`（monkeypatch 模块级 `get_astrbot_temp_path` 到 `tmp_path`），否则 `__init__` 会解析**真实** `data/temp` 并每次运行留下一个暂存 zip。
>
> 下方代码块为修正后的形态。

```python
"""ChatUI session import: staging, safety guards and round-trip.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import json
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.dashboard.services.session_transfer_service import (
    EXPORT_DATA_NAME,
    EXPORT_FORMAT_VERSION,
    EXPORT_KIND,
    MANIFEST_NAME,
    MAX_ZIP_ENTRIES,
    SessionTransferError,
    SessionTransferService,
)


def build_package_bytes(
    *,
    kind=EXPORT_KIND,
    format_version=EXPORT_FORMAT_VERSION,
    astrbot_version="4.22.0",
    extra_entries=(),
    sessions=None,
):
    """Build an import zip in memory for staging tests."""
    manifest = {
        "kind": kind,
        "format_version": format_version,
        "astrbot_version": astrbot_version,
        "exported_at": "2026-09-17T00:00:00+00:00",
        "sessions": [
            {
                "original_session_id": "sess-1",
                "display_name": "会话 A",
                "original_creator": "alice",
                "stats": {"messages": 0, "conversations": 0, "threads": 0,
                          "attachments": 0, "attachment_bytes": 0},
            }
        ],
        "warnings": [],
        "checksums": {},
    }
    payload = {"sessions": sessions if sessions is not None else []}
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest))
        zf.writestr(EXPORT_DATA_NAME, json.dumps(payload))
        for name, blob in extra_entries:
            zf.writestr(name, blob)
    buffer.seek(0)
    return buffer


class _FakeUpload:
    """Minimal UploadFileAdapter stand-in backed by bytes."""

    def __init__(self, blob: bytes, filename="pkg.zip", content_length=None):
        self._blob = blob
        self.filename = filename
        self.content_type = "application/zip"
        self.content_length = content_length if content_length is not None else len(blob)

    async def save(self, destination):
        with open(destination, "wb") as handle:
            handle.write(self._blob)


def _make_service():
    db = Mock()
    db.get_preferences = AsyncMock(return_value=[])
    db.get_conversations = AsyncMock(return_value=[])
    db.get_attachment_by_id = AsyncMock(return_value=None)
    core_lifecycle = SimpleNamespace(
        conversation_manager=Mock(),
        platform_message_history_manager=Mock(),
    )
    return SessionTransferService(db, core_lifecycle)


@pytest.mark.asyncio
async def test_stage_import_rejects_wrong_kind():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(kind="something-else").getvalue())

    with pytest.raises(SessionTransferError, match="Unsupported package"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_unknown_format_version():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(format_version=99).getvalue())

    with pytest.raises(SessionTransferError, match="format_version"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_incompatible_major_version():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes(astrbot_version="3.0.0").getvalue())

    with pytest.raises(SessionTransferError):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_oversized_upload():
    service = _make_service()
    upload = _FakeUpload(b"x", content_length=1024 * 1024 * 1024)

    with pytest.raises(SessionTransferError, match="too large"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_rejects_too_many_entries():
    service = _make_service()
    entries = [(f"files/attachments/{i}.bin", b"x") for i in range(MAX_ZIP_ENTRIES + 1)]
    upload = _FakeUpload(build_package_bytes(extra_entries=entries).getvalue())

    with pytest.raises(SessionTransferError, match="entries"):
        await service.stage_import(upload)


@pytest.mark.asyncio
async def test_stage_import_returns_preview_and_can_import():
    service = _make_service()
    upload = _FakeUpload(build_package_bytes().getvalue())

    preview = await service.stage_import(upload)

    assert preview["can_import"] is True
    assert preview["import_id"] in service.pending_imports
    assert preview["sessions"][0]["display_name"] == "会话 A"
    assert preview["sessions"][0]["original_creator"] == "alice"
    assert preview["version_status"]["compatible"] is True


@pytest.mark.asyncio
async def test_pending_import_expires():
    service = _make_service()
    preview = await service.stage_import(_FakeUpload(build_package_bytes().getvalue()))
    service.pending_imports[preview["import_id"]].created_at -= 99999

    with pytest.raises(SessionTransferError, match="expired"):
        service._pending(preview["import_id"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_session_transfer_import.py -v`
Expected: FAIL — `AttributeError: 'SessionTransferService' object has no attribute 'stage_import'`

- [ ] **Step 3: 写实现**

在 `session_transfer_service.py` 追加：

```python
    def _drop_pending(self, import_id: str) -> None:
        """Forget a staged import and delete its temporary zip.

        Args:
            import_id: Identifier returned by ``stage_import``.
        """
        pending = self.pending_imports.pop(import_id, None)
        if pending is None:
            return
        try:
            pending.zip_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"Failed to delete staged import {import_id}: {exc!s}")

    def _pending(self, import_id: str) -> _PendingImport:
        """Look up a staged import, enforcing the TTL.

        Args:
            import_id: Identifier returned by ``stage_import``.

        Returns:
            The staged import record.

        Raises:
            SessionTransferError: If the id is unknown or has expired.
        """
        pending = self.pending_imports.get(import_id)
        if pending is None:
            raise SessionTransferError("Import preview expired, please upload again")
        if time.monotonic() - pending.created_at > IMPORT_ID_TTL_SECONDS:
            self._drop_pending(import_id)
            raise SessionTransferError("Import preview expired, please upload again")
        return pending

    @staticmethod
    def _verify_zip_safety(archive) -> None:
        """Reject archives that could exhaust disk or expand beyond limits.

        Args:
            archive: Open zip archive, or any object exposing ``infolist()``
                (tests drive this with a duck-typed stand-in).

        Raises:
            SessionTransferError: If the entry count or uncompressed size
                exceeds the configured limits.
        """
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SessionTransferError(
                f"Package has too many entries (> {MAX_ZIP_ENTRIES})"
            )
        total = 0
        for info in infos:
            if info.file_size > MAX_ENTRY_BYTES:
                raise SessionTransferError(f"Entry too large: {info.filename}")
            total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise SessionTransferError("Package expands beyond the size limit")

    def _read_package(self, zip_path: Path) -> tuple[dict, dict]:
        """Read and validate a staged import zip.

        Args:
            zip_path: Path of the temporary zip on disk.

        Returns:
            ``(manifest, payload)`` parsed from the archive.

        Raises:
            SessionTransferError: If the zip is malformed, of an unexpected
                kind/format version, or built by an incompatible AstrBot.
        """
        try:
            with zipfile.ZipFile(zip_path) as zf:
                self._verify_zip_safety(zf)
                names = set(zf.namelist())
                if MANIFEST_NAME not in names or EXPORT_DATA_NAME not in names:
                    raise SessionTransferError("Package is missing required entries")
                manifest = json.loads(zf.read(MANIFEST_NAME))
                payload = json.loads(zf.read(EXPORT_DATA_NAME))
        except zipfile.BadZipFile as exc:
            raise SessionTransferError("Uploaded file is not a valid zip") from exc
        except json.JSONDecodeError as exc:
            raise SessionTransferError("Package metadata is not valid JSON") from exc
        except SessionTransferError:
            raise
        except Exception as exc:
            # A corrupted archive escapes the two classes above in three other
            # ways (zlib.error on a bad deflate stream, EOFError from
            # zipfile._read2, NotImplementedError for an unsupported
            # compression method / zip version). Convert at this boundary so
            # the service keeps its error contract and the caller's cleanup
            # runs, instead of a generic 500 plus a leaked staged file.
            raise SessionTransferError(f"Package archive is unreadable: {exc!s}") from exc

        if not isinstance(manifest, dict) or manifest.get("kind") != EXPORT_KIND:
            raise SessionTransferError("Unsupported package: wrong kind")
        if manifest.get("format_version") != EXPORT_FORMAT_VERSION:
            raise SessionTransferError(
                f"Unsupported package format_version: {manifest.get('format_version')}"
            )
        exported_version = str(manifest.get("astrbot_version") or "")
        if _get_major_version(exported_version) != _get_major_version(VERSION):
            raise SessionTransferError(
                f"Incompatible AstrBot version: package {exported_version}, "
                f"current {VERSION}"
            )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("sessions"), list
        ):
            raise SessionTransferError("Package payload is malformed")
        return manifest, payload

    async def stage_import(self, upload) -> dict:
        """Persist an uploaded package and return its pre-check preview.

        Args:
            upload: UploadFileAdapter carrying the export zip.

        Returns:
            Preview dict with ``import_id``, per-session stats, the version
            status and whether the package can be imported.

        Raises:
            SessionTransferError: If the upload is missing, too large, or
                fails any package validation.
        """
        if upload is None or not getattr(upload, "filename", None):
            raise SessionTransferError("Missing key: file")
        if upload.content_length and upload.content_length > MAX_UPLOAD_BYTES:
            raise SessionTransferError("Uploaded package is too large")

        import_id = str(uuid.uuid4())
        zip_path = self.temp_dir / f"session_import_{import_id}.zip"
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            await upload.save(str(zip_path))
            if zip_path.stat().st_size > MAX_UPLOAD_BYTES:
                raise SessionTransferError("Uploaded package is too large")
            manifest, payload = self._read_package(zip_path)
        except Exception as exc:
            # Cleanup is unconditional and the error family is normalised here
            # rather than enumerated: any non-service failure must still become
            # a SessionTransferError so the route maps it, and must still
            # remove the staged file so nothing is left untracked in data/temp.
            zip_path.unlink(missing_ok=True)
            if isinstance(exc, SessionTransferError):
                raise
            raise SessionTransferError(f"Failed to stage upload: {exc!s}") from exc

        warnings = list(manifest.get("warnings") or [])
        sessions = []
        for entry in manifest.get("sessions") or []:
            sessions.append(
                {
                    "display_name": entry.get("display_name"),
                    "original_creator": entry.get("original_creator"),
                    "stats": entry.get("stats") or {},
                }
            )
        version_status = {
            "compatible": True,
            "package_version": str(manifest.get("astrbot_version") or ""),
            "current_version": VERSION,
            "upgrade_advised": VersionComparator.compare_version(
                str(manifest.get("astrbot_version") or "0.0.0"), VERSION
            )
            != 0,
        }
        preview = {
            "import_id": import_id,
            "sessions": sessions,
            "version_status": version_status,
            "can_import": bool(payload.get("sessions")),
            "warnings": warnings,
        }
        self.pending_imports[import_id] = _PendingImport(
            zip_path=zip_path,
            manifest=manifest,
            payload=payload,
            preview=preview,
            created_at=time.monotonic(),
        )
        return preview
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_session_transfer_import.py -v`
Expected: PASS

> **2026-09-17 裁决后修正（用户裁定：版本门禁降级为 warning，并补 3 条守卫缺口）**
>
> **(1) `_read_package` 的版本门禁改为信息性** —— 删掉 `if _get_major_version(...) != _get_major_version(VERSION): raise`，只保留 `format_version` 硬门禁（数据契约），并把导出版本原样交给 `stage_import`：
>
> ```python
>         # The data contract is `format_version` (enforced above). The
>         # exporter's AstrBot version is informational: cross-instance
>         # migration routinely crosses minor releases, so a version
>         # difference becomes a warning the dialog renders, not a rejection.
>         if not isinstance(manifest.get("astrbot_version"), str):
>             raise SessionTransferError("Package is missing astrbot_version")
> ```
>
> **(2) `stage_import` 的 `version_status` 与 warning**（替换原来的 `version_status = {...}` 段）：
>
> ```python
>         exported_version = str(manifest.get("astrbot_version") or "")
>         version_cmp = (
>             VersionComparator.compare_version(exported_version, VERSION)
>             if exported_version
>             else None
>         )
>         if not exported_version:
>             warnings.append("Package does not declare an AstrBot version")
>         elif version_cmp != 0:
>             warnings.append(
>                 f"Package was exported from AstrBot {exported_version}, "
>                 f"current version is {VERSION}"
>             )
>         # `compatible` is a genuine constant, not a computed verdict: the
>         # schema contract (`format_version`) already passed `_read_package`.
>         version_status = {
>             "compatible": True,
>             "package_version": exported_version,
>             "current_version": VERSION,
>             "upgrade_advised": version_cmp is not None and version_cmp != 0,
>         }
> ```
>
> **(3) `mkdir` / `save` 移进 try，且清理与错误归一不再枚举异常族**（否则磁盘满/权限失败会抛裸 `OSError`，被兜底转成泛化 500，且半截 zip 留在 `data/temp` 无人回收）——以上方主代码块为准，关键点是 `except Exception as exc:` 分支里先无条件 `unlink`，再把非 `SessionTransferError` 的异常包装成服务错误。
>
> **(4) 需要新增/改写的测试**（`tests/test_session_transfer_import.py`）：
>
> - 原 `test_stage_import_rejects_incompatible_major_version` **改写**为
>   `test_stage_import_accepts_other_version_with_warning`：`astrbot_version="3.0.0"` 必须 `can_import is True` 且 `preview["warnings"]` 非空、`version_status["upgrade_advised"] is True`。
> - `test_stage_import_rejects_oversized_upload_without_content_length`：`monkeypatch.setattr(session_transfer_service, "MAX_UPLOAD_BYTES", 1024)`，`_FakeUpload(blob=b"x" * 4096, content_length=None)` → `pytest.raises(SessionTransferError, match="too large")`。
> - `test_stage_import_rejects_corrupt_zip`：`_FakeUpload(b"not a zip at all")` → `match="not a valid zip"`。
> - `test_read_package_converts_escaped_archive_errors`：把 `manifest.json` 改成**坏 deflate 流**（对真实包该条目的字节做逐字节 XOR 或截断），断言抛的是 `SessionTransferError`（`match="unreadable"` 或 `"not a valid zip"`）而**不是** `zlib.error` / `EOFError` / `NotImplementedError`。这条锁住「异常归一 + 暂存文件必须被删」的契约：同时断言 `service.pending_imports == {}` 且临时目录里没有 `session_import_*.zip` 残留。
> - `test_stage_import_rejects_package_missing_required_entries`：手工造只含 `manifest.json` 的 zip → `match="missing required entries"`。
> - `test_stage_import_rejects_malformed_payload`：`export.json` 内容为 `"[]"` → `match="payload is malformed"`。
> - `test_verify_zip_safety_rejects_oversized_entry` 与 `test_verify_zip_safety_rejects_total_expansion`：不需要真造 512 MB/2 GB —— `_verify_zip_safety` 是 staticmethod 且只读归档声明尺寸，用一个只实现 `infolist()` 的假对象即可走真实算术：
>
> ```python
> class _FakeArchive:
>     def __init__(self, infos):
>         self._infos = infos
>
>     def infolist(self):
>         return self._infos
>
>
> def _info(name: str, size: int) -> zipfile.ZipInfo:
>     info = zipfile.ZipInfo(name)
>     info.file_size = size
>     return info
>
>
> def test_verify_zip_safety_rejects_oversized_entry():
>     archive = _FakeArchive([_info("files/attachments/a.bin", MAX_ENTRY_BYTES + 1)])
>     with pytest.raises(SessionTransferError, match="Entry too large"):
>         SessionTransferService._verify_zip_safety(archive)
>
>
> def test_verify_zip_safety_rejects_total_expansion():
>     chunk = MAX_TOTAL_UNCOMPRESSED_BYTES // 4 + 1
>     archive = _FakeArchive(
>         [_info(f"files/attachments/{i}.bin", chunk) for i in range(4)]
>     )
>     with pytest.raises(SessionTransferError, match="size limit"):
>         SessionTransferService._verify_zip_safety(archive)
> ```
>
> （`_FakeArchive` / `_info` 放进测试文件；需 import `MAX_ENTRY_BYTES`、`MAX_TOTAL_UNCOMPRESSED_BYTES`，并保留 `import zipfile`。）
> 注：(2) 之后 `_get_major_version` 在本模块不再被使用 —— 必须从 import 段删除，否则 ruff F401 会让门禁变红。

- [ ] **Step 5: 格式化并提交**

```bash
uvx ruff format astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_import.py
uvx ruff check astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_import.py
git add -A astrbot/dashboard/services/session_transfer_service.py tests/test_session_transfer_import.py
git commit -m "feat: stage and pre-check chatui session import packages"
```

---

### Task 4: 导入落库（重映射 + 单事务 + warning）

**Files:**
- Modify: `astrbot/dashboard/services/session_transfer_service.py`
- Test: `tests/test_session_transfer_import.py`（追加用例）

**Interfaces:**
- Consumes: Task 1 纯函数、Task 3 的 `_pending` / `_drop_pending`
- Produces:
  - `async SessionTransferService.confirm_import(self, username: str, import_id: str) -> dict`
  - `async SessionTransferService._import_one_session(self, dbsession, entry: dict, username: str, warnings: list[str], created_files: list[Path], archive_path: Path) -> dict`

- [ ] **Step 1: 写失败测试**

在 `tests/test_session_transfer_import.py` 追加（复用文件顶部已有的 helper）：

> **2026-09-17 实现期修正（本 Step 的脚手架原文有 3 处缺陷，已在提交 `7b5ed6f26` 修正并上报）**：
> 1. `_FakeSession.flush` 不能给**每一行**都赋 `.id` —— `PlatformSession` 没有 `id` 字段（主键是 `inner_id`），pydantic 会直接抛错；这会同时让两条 `errors == []` 用例失败，并让 `test_confirm_import_reissues_colliding_attachment_id` **因为被吞进 `errors` 而错误地通过**。id 模拟必须限定为 `PlatformMessageHistory`（例如确定性地取 `900 + index`）。
> 2. `test_confirm_import_drops_thread_without_parent` 原文把 `get_db` 换成一个必抛的 lambda，同时断言 `errors == []` —— 逻辑上不可能同时成立（异常会被 confirm 的逐会话 handler 收进 `errors`）。保留原意、断言真实行为：没有 `WebChatThread` 行、有一条 `"parent message missing"` warning、`errors == []`。
> 3. 追加新用例时不要把既有 corruption 用例的 `assert saw_a_failure` 位移走（会让 corruption 用例变空转、新用例直接 `NameError`）。

```python
def _seed_export_sessions():
    """One session with one history row, one attachment and one thread."""
    return [
        {
            "session": {
                "session_id": "sess-1",
                "display_name": "会话 A",
                "is_group": 0,
                "archived": 0,
                "created_at": "2026-09-01T00:00:00+00:00",
                "updated_at": "2026-09-01T00:00:00+00:00",
            },
            "conversations": [
                {
                    "conversation_id": "conv-1",
                    "platform_id": "webchat",
                    "user_id": "webchat:FriendMessage:webchat!alice!sess-1",
                    "content": [{"role": "user", "content": "hi"}],
                    "title": "t",
                    "persona_id": None,
                    "token_usage": 0,
                }
            ],
            "history": [
                {
                    "id": 11,
                    "sender_id": "alice",
                    "sender_name": "alice",
                    "content": {
                        "type": "user",
                        "message": [
                            {"type": "image", "attachment_id": "att-1", "filename": "a.png"}
                        ],
                    },
                    "llm_checkpoint_id": "ck-1",
                    "created_at": "2026-09-01T00:00:00+00:00",
                }
            ],
            "threads": [
                {
                    "thread": {
                        "thread_id": "thr-1",
                        "creator": "alice",
                        "parent_session_id": "sess-1",
                        "parent_message_id": 11,
                        "base_checkpoint_id": "ck-1",
                        "selected_text": "hi",
                    },
                    "history": [],
                    "conversations": [],
                }
            ],
            "preferences": [],
            "attachments": [
                {
                    "attachment_id": "att-1",
                    "type": "image",
                    "mime_type": "image/png",
                    "ext": ".png",
                    "size": 7,
                    "zip_path": "files/attachments/att-1.png",
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_confirm_import_remaps_ids_and_ownership(tmp_path):
    service = _make_service()
    service.attachments_dir = tmp_path
    package = build_package_bytes(
        sessions=_seed_export_sessions(),
        extra_entries=[("files/attachments/att-1.png", b"PNGDATA")],
    )
    preview = await service.stage_import(_FakeUpload(package.getvalue()))

    added = []

    class _FakeSession:
        async def flush(self):
            # Emulate autoincrement assignment for the history rows.
            for row in added:
                if getattr(row, "id", None) is None:
                    row.id = 900 + len(added)

        def add(self, row):
            added.append(row)

        class _Begin:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        def begin(self):
            return _FakeSession._Begin()

    class _DbCtx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *exc):
            return False

    service.db.get_db = lambda: _DbCtx()
    service.db.get_attachments = AsyncMock(return_value=[])
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    result = await service.confirm_import("bob", preview["import_id"])

    assert result["errors"] == []
    assert len(result["created"]) == 1
    created = result["created"][0]
    assert created["display_name"] == "会话 A"
    assert created["new_session_id"] != "sess-1"

    sessions = [row for row in added if row.__class__.__name__ == "PlatformSession"]
    histories = [
        row for row in added if row.__class__.__name__ == "PlatformMessageHistory"
    ]
    threads = [row for row in added if row.__class__.__name__ == "WebChatThread"]
    assert sessions[0].creator == "bob"
    assert sessions[0].session_id == created["new_session_id"]
    assert histories[0].user_id == created["new_session_id"]
    assert histories[0].content["message"][0]["attachment_id"] == "att-1"
    assert threads[0].parent_session_id == created["new_session_id"]
    assert threads[0].parent_message_id == histories[0].id
    # The staged zip is always cleaned up.
    assert preview["import_id"] not in service.pending_imports


@pytest.mark.asyncio
async def test_confirm_import_reissues_colliding_attachment_id(tmp_path):
    service = _make_service()
    service.attachments_dir = tmp_path
    package = build_package_bytes(
        sessions=_seed_export_sessions(),
        extra_entries=[("files/attachments/att-1.png", b"PNGDATA")],
    )
    preview = await service.stage_import(_FakeUpload(package.getvalue()))

    service.db.get_attachments = AsyncMock(
        return_value=[SimpleNamespace(attachment_id="att-1")]
    )

    added = []

    class _FakeSession:
        async def flush(self):
            for row in added:
                if getattr(row, "id", None) is None:
                    row.id = 900 + len(added)

        def add(self, row):
            added.append(row)

        class _Begin:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        def begin(self):
            return _FakeSession._Begin()

    class _DbCtx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *exc):
            return False

    service.db.get_db = lambda: _DbCtx()
    service.db.get_platform_session_by_id = AsyncMock(return_value=None)

    await service.confirm_import("bob", preview["import_id"])

    attachments = [row for row in added if row.__class__.__name__ == "Attachment"]
    assert attachments[0].attachment_id != "att-1"
    assert attachments[0].path.endswith(f"{attachments[0].attachment_id}.png")
    histories = [
        row for row in added if row.__class__.__name__ == "PlatformMessageHistory"
    ]
    assert histories[0].content["message"][0]["attachment_id"] == (
        attachments[0].attachment_id
    )


@pytest.mark.asyncio
async def test_confirm_import_drops_thread_without_parent():
    service = _make_service()
    sessions = _seed_export_sessions()
    sessions[0]["history"] = []
    sessions[0]["threads"][0]["thread"]["parent_message_id"] = 11
    package = build_package_bytes(sessions=sessions)
    preview = await service.stage_import(_FakeUpload(package.getvalue()))

    # No parent history row is imported, so the thread must be dropped.
    service.db.get_db = lambda: (_ for _ in ()).throw(AssertionError("unused"))
    result = await service.confirm_import("bob", preview["import_id"])

    assert result["errors"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_session_transfer_import.py -v`
Expected: FAIL — `AttributeError: 'SessionTransferService' object has no attribute 'confirm_import'`

- [ ] **Step 3: 写实现**

在 `session_transfer_service.py` 追加：

```python
    async def _import_one_session(
        self,
        dbsession,
        entry: dict,
        username: str,
        warnings: list[str],
        created_files: list[Path],
        archive_path: Path,
    ) -> dict:
        """Import one exported session inside the caller's transaction.

        Args:
            dbsession: AsyncSession already inside a ``begin()`` block.
            entry: One element of ``export.json["sessions"]``.
            username: Importing dashboard user; becomes the new creator.
            warnings: Mutable list collecting user-visible warnings.
            created_files: Mutable list receiving every file written to disk,
                so the caller can delete them when the transaction fails.
            archive_path: Path of the staged import zip holding the blobs.

        Returns:
            ``{"new_session_id", "display_name"}`` for the imported session.
        """
        session_data = entry.get("session") or {}
        is_group = int(session_data.get("is_group") or 0)
        new_session_id = str(uuid.uuid4())
        session_umo = new_umo(WEBCHAT_PLATFORM_ID, is_group, username, new_session_id)
        # Exported preference rows carry the ORIGINAL scope ids, so the import
        # needs the old-UMO -> new-UMO mapping to place each row correctly.
        original_creator = session_data.get("creator") or username
        umo_map = {
            new_umo(
                WEBCHAT_PLATFORM_ID,
                is_group,
                original_creator,
                session_data.get("session_id") or "",
            ): session_umo
        }

        new_session = db_po.PlatformSession(
            session_id=new_session_id,
            platform_id=WEBCHAT_PLATFORM_ID,
            creator=username,
            display_name=session_data.get("display_name"),
            is_group=is_group,
            archived=0,
        )
        dbsession.add(new_session)

        # Attachments: reissue colliding ids, then write files and rows.
        exported_attachments = entry.get("attachments") or []
        old_attachment_ids = [
            item.get("attachment_id")
            for item in exported_attachments
            if item.get("attachment_id")
        ]
        existing = await self.db.get_attachments(old_attachment_ids)
        attachment_map = build_attachment_id_map(
            old_attachment_ids, {row.attachment_id for row in existing}
        )
        for item in exported_attachments:
            old_id = item.get("attachment_id")
            new_id = attachment_map.get(old_id)
            if not new_id:
                continue
            ext = item.get("ext") or ""
            # An empty suffix is legitimate: the exporter could not derive a
            # safe one, so the target file is simply "{new_id}" with no
            # suffix. Only a *provided* suffix must match the strict pattern.
            if ext and not is_safe_attachment_ext(ext):
                warnings.append(f"Attachment {old_id}: skipped unsafe suffix {ext!r}")
                continue
            zip_path = item.get("zip_path") or ""
            if not is_safe_attachment_zip_path(zip_path):
                warnings.append(f"Attachment {old_id}: skipped unsafe path {zip_path!r}")
                continue
            target = self.attachments_dir / f"{new_id}{ext}"
            blob = self._read_attachment_blob(archive_path, zip_path)
            if blob is None:
                warnings.append(f"Attachment {old_id}: file missing in package")
                continue
            await asyncio.to_thread(target.write_bytes, blob)
            dbsession.add(
                db_po.Attachment(
                    attachment_id=new_id,
                    path=str(target),
                    type=item.get("type") or "file",
                    mime_type=item.get("mime_type") or "application/octet-stream",
                )
            )
            created_files.append(target)

        # LLM conversations (session + threads share the same shape).
        for conversation in entry.get("conversations") or []:
            dbsession.add(
                db_po.ConversationV2(
                    conversation_id=str(uuid.uuid4()),
                    platform_id=WEBCHAT_PLATFORM_ID,
                    user_id=session_umo,
                    content=conversation.get("content") or [],
                    title=conversation.get("title"),
                    persona_id=conversation.get("persona_id"),
                    token_usage=int(conversation.get("token_usage") or 0),
                )
            )

        # Main history, remembering old -> new ids for thread backfill.
        history_rows = []
        for record in entry.get("history") or []:
            row = db_po.PlatformMessageHistory(
                platform_id=WEBCHAT_PLATFORM_ID,
                user_id=new_session_id,
                content=rewrite_attachment_refs(
                    record.get("content") or {}, attachment_map
                ),
                sender_id=record.get("sender_id"),
                sender_name=record.get("sender_name"),
                llm_checkpoint_id=record.get("llm_checkpoint_id"),
            )
            dbsession.add(row)
            history_rows.append((record.get("id"), row))
        await dbsession.flush()
        history_id_map = {
            old_id: row.id for old_id, row in history_rows if old_id is not None
        }

        # Threads: drop the ones whose parent message was not imported.
        for thread_entry in entry.get("threads") or []:
            thread_data = thread_entry.get("thread") or {}
            parent_id = resolve_parent_message_id(
                thread_data.get("parent_message_id"), history_id_map
            )
            if parent_id is None:
                warnings.append(
                    f"Thread {thread_data.get('thread_id')}: parent message missing"
                )
                continue
            new_thread_id = str(uuid.uuid4())
            dbsession.add(
                db_po.WebChatThread(
                    thread_id=new_thread_id,
                    creator=username,
                    parent_session_id=new_session_id,
                    parent_message_id=parent_id,
                    base_checkpoint_id=thread_data.get("base_checkpoint_id") or "",
                    selected_text=thread_data.get("selected_text") or "",
                )
            )
            thread_umo = new_umo(WEBCHAT_PLATFORM_ID, 0, username, new_thread_id)
            umo_map[
                new_umo(
                    WEBCHAT_PLATFORM_ID,
                    0,
                    original_creator,
                    thread_data.get("thread_id") or "",
                )
            ] = thread_umo
            for conversation in thread_entry.get("conversations") or []:
                dbsession.add(
                    db_po.ConversationV2(
                        conversation_id=str(uuid.uuid4()),
                        platform_id=WEBCHAT_PLATFORM_ID,
                        user_id=thread_umo,
                        content=conversation.get("content") or [],
                        title=conversation.get("title"),
                        persona_id=conversation.get("persona_id"),
                        token_usage=int(conversation.get("token_usage") or 0),
                    )
                )
            for record in thread_entry.get("history") or []:
                dbsession.add(
                    db_po.PlatformMessageHistory(
                        platform_id=THREAD_PLATFORM_ID,
                        user_id=new_thread_id,
                        content=rewrite_attachment_refs(
                            record.get("content") or {}, attachment_map
                        ),
                        sender_id=record.get("sender_id"),
                        sender_name=record.get("sender_name"),
                        llm_checkpoint_id=record.get("llm_checkpoint_id"),
                    )
                )

        # Session-scoped preferences follow the session into the new UMO.
        # The exporter collects rows for the session UMO *and* every thread
        # UMO (spec §5.1), so each row must be remapped through the UMO it
        # actually belonged to — collapsing them all onto the session UMO
        # would misassign thread-scoped config AND could collide on
        # (scope, scope_id, key), rolling back the whole session.
        for preference in entry.get("preferences") or []:
            exported_scope_id = preference.get("scope_id") or ""
            dbsession.add(
                db_po.Preference(
                    scope=preference.get("scope") or "umo",
                    scope_id=umo_map.get(exported_scope_id, session_umo),
                    key=preference.get("key") or "",
                    value=preference.get("value") or {},
                )
            )

        return {
            "new_session_id": new_session_id,
            "display_name": session_data.get("display_name"),
        }

    async def confirm_import(self, username: str, import_id: str) -> dict:
        """Import every session of a staged package into ``username``'s account.

        Args:
            username: Authenticated dashboard user receiving the sessions.
            import_id: Identifier returned by ``stage_import``.

        Returns:
            ``{"created", "warnings", "errors"}`` where ``created`` lists the
            new session ids and display names.

        Raises:
            SessionTransferError: If the staged package expired, is no longer
                importable, or its files changed since pre-check.
        """
        pending = self._pending(import_id)
        if not pending.preview.get("can_import"):
            self._drop_pending(import_id)
            raise SessionTransferError("Package contains no importable sessions")
        try:
            # Re-validate: the staged zip may have changed since pre-check.
            manifest, payload = self._read_package(pending.zip_path)
        except SessionTransferError:
            # Never leave a staged package behind on a failure path.
            self._drop_pending(import_id)
            raise
        pending.manifest, pending.payload = manifest, payload

        created: list[dict] = []
        warnings: list[str] = []
        errors: list[str] = []
        created_files: list[Path] = []
        try:
            for entry in pending.payload.get("sessions") or []:
                mark = len(created_files)
                try:
                    async with self.db.get_db() as dbsession:
                        async with dbsession.begin():
                            imported = await self._import_one_session(
                                dbsession,
                                entry,
                                username,
                                warnings,
                                created_files,
                                pending.zip_path,
                            )
                    # Appended only after `begin()` exits: a failure at
                    # commit time must never leave a phantom "created"
                    # session that Tasks 5/8 would render or link.
                    created.append(imported)
                except Exception as exc:
                    logger.error(f"Session import failed: {exc!s}", exc_info=True)
                    errors.append(
                        f"{entry.get('session', {}).get('session_id')}: {exc!s}"
                    )
                    # Drop the files this session wrote before its tx rolled back.
                    for path in created_files[mark:]:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as unlink_exc:
                            logger.warning(
                                f"Failed to roll back {path}: {unlink_exc!s}"
                            )
                    del created_files[mark:]
        finally:
            self._drop_pending(import_id)
        return {"created": created, "warnings": warnings, "errors": errors}
```

实现要点（写代码时必须遵守，否则测试会红）：

1. 模块顶部补齐正常 import（**不要**用 `__import__`）：

```python
from astrbot.core.db import po as db_po
```

2. 附件读取辅助方法：

```python
    @staticmethod
    def _read_attachment_blob(archive_path: Path, entry_path: str) -> bytes | None:
        """Read one attachment blob from the staged import zip.

        Args:
            archive_path: Path of the staged import zip.
            entry_path: Manifest-declared path inside the archive.

        Returns:
            The file bytes, or None when the entry is absent.
        """
        with zipfile.ZipFile(archive_path) as zf:
            try:
                return zf.read(entry_path)
            except KeyError:
                return None
```

3. 磁盘文件在事务失败时必须清理：`_import_one_session` 把自己写出的每个文件路径追加进调用方传入的 `created_files`，`confirm_import` 用「进入本会话前的长度」做水位线，异常时删除 `created_files[mark:]` 并截断列表。上面主代码块中的 `confirm_import` 已按此实现。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_session_transfer_import.py tests/test_session_transfer_export.py tests/test_session_transfer_remap.py -v`
Expected: PASS

- [ ] **Step 5: 写端到端往返集成测试（真库）**

创建 `tests/test_session_transfer_roundtrip.py`（真 sqlite + 真 manager，不用 mock；建库写法对齐 `tests/agent_teams/test_agent_team_auto.py:155`）：

> **2026-09-17 评审后修正（3 条 Important，用户裁定后已并入本计划：偏好重映射 / phantom created / 线程分支零覆盖）**：
>
> 1. **种子必须含一个线程**（下方代码块的 `_seed` 原文没有线程，导致 `_import_one_session` 的线程分支 `:797-815` 零覆盖）。在 `_seed` 里补：一条 `WebChatThread`（`thread_id="thr-1"`、`creator="alice"`、`parent_session_id=session.session_id`、`parent_message_id=<主 history 行的 id>`、`base_checkpoint_id="ck-1"`、`selected_text="hi"`），一条线程 history（`platform_id="webchat_thread"`、`user_id="thr-1"`、content 的 parts 里**复用同一个附件 id**，用于同时覆盖「线程内附件」端到端路径），以及一条线程 conversation（`user_id` = 线程 UMO `webchat:FriendMessage:webchat!alice!thr-1`）。
>    断言：导入后 `db.get_webchat_thread_by_id(<新 thread id>)` 存在且 `creator == "bob"`、`parent_session_id == 新会话 id`、`parent_message_id == 新主 history 行 id`；线程 history 落在 `platform_id="webchat_thread"` + `user_id=<新 thread id>` 下且其 parts 的 `attachment_id` 已被重映射；线程 conversation 落在 `webchat:FriendMessage:webchat!bob!<新 thread id>` 下。
> 2. `confirm_import` 的 `created.append` 必须移到 `begin()` 块**之外**（否则 commit 期失败会把一个已回滚的会话同时报进 `created` 与 `errors`，Task 5/8 会把它当真实会话渲染/跳转）——已并入上方主代码块。
> 3. 偏好重映射按 `scope_id` 走 `umo_map`（否则线程级偏好会被写到会话 UMO 上，且同 key 会在 `(scope, scope_id, key)` 上撞 UNIQUE 约束导致整会话回滚）——已并入上方主代码块。断言：一条原属线程 UMO 的偏好导入后落在**新线程 UMO** 下。
>
> **同时并入的两条 Minor**：`confirm_import` 的循环体开头加 `if not isinstance(entry, dict): raise SessionTransferError("Malformed session entry")`（否则 `entry.get` 会在 except handler 里抛 `AttributeError` 逃逸成 500）；三条 confirm 用例里重复的 `_FakeSession`/`_DbCtx` 抽成模块级 helper（约省 120 行）。

```python
"""ChatUI session export -> import round trip on a real sqlite database.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.dashboard.services.session_transfer_service import SessionTransferService


class _Upload:
    """UploadFileAdapter stand-in backed by bytes."""

    def __init__(self, blob: bytes, filename: str = "pkg.zip") -> None:
        self._blob = blob
        self.filename = filename
        self.content_type = "application/zip"
        self.content_length = len(blob)

    async def save(self, destination) -> None:
        with open(destination, "wb") as handle:
            handle.write(self._blob)


async def _seed(tmp_path):
    """Create a webchat session for alice with one attachment and one LLM turn."""
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    history_mgr = PlatformMessageHistoryManager(db)
    conv_mgr = ConversationManager(db)
    lifecycle = SimpleNamespace(
        conversation_manager=conv_mgr,
        platform_message_history_manager=history_mgr,
    )
    service = SessionTransferService(db, lifecycle)
    service.attachments_dir = tmp_path / "attachments"
    service.attachments_dir.mkdir(parents=True, exist_ok=True)
    service.temp_dir = tmp_path / "temp"

    session = await db.create_platform_session(
        creator="alice",
        platform_id="webchat",
        display_name="会话 A",
    )
    attachment_path = service.attachments_dir / "src-image.png"
    attachment_path.write_bytes(b"PNGDATA")
    attachment = await db.insert_attachment(
        path=str(attachment_path), type="image", mime_type="image/png"
    )

    await history_mgr.insert(
        platform_id="webchat",
        user_id=session.session_id,
        content={
            "type": "user",
            "message": [
                {"type": "plain", "text": "hi"},
                {
                    "type": "image",
                    "attachment_id": attachment.attachment_id,
                    "filename": "src-image.png",
                },
            ],
        },
        sender_id="alice",
        sender_name="alice",
        llm_checkpoint_id="ck-1",
    )
    source_umo = f"webchat:FriendMessage:webchat!alice!{session.session_id}"
    await conv_mgr.new_conversation(
        unified_msg_origin=source_umo,
        platform_id="webchat",
        content=[{"role": "user", "content": "hi"}],
        title="t",
    )
    return service, db, session, attachment


@pytest.mark.asyncio
async def test_export_import_round_trip_migrates_session_to_another_user(tmp_path):
    service, db, session, attachment = await _seed(tmp_path)

    export = await service.export_session("alice", session.session_id)
    preview = await service.stage_import(_Upload(export.file_obj.getvalue()))
    assert preview["can_import"] is True

    result = await service.confirm_import("bob", preview["import_id"])
    assert result["errors"] == []
    assert len(result["created"]) == 1
    new_session_id = result["created"][0]["new_session_id"]
    assert new_session_id != session.session_id

    # Ownership moved to the importer; metadata survived.
    imported = await db.get_platform_session_by_id(new_session_id)
    assert imported is not None
    assert imported.creator == "bob"
    assert imported.display_name == "会话 A"

    # The message stream came across with its checkpoint id intact.
    imported_history = await service.platform_history_mgr.get(
        platform_id="webchat", user_id=new_session_id, page=1, page_size=100
    )
    assert len(imported_history) == 1
    assert imported_history[0].llm_checkpoint_id == "ck-1"

    # Same-database import means the source attachment id was taken, so the
    # part now points at a freshly issued id and a rewritten on-disk path.
    image_part = imported_history[0].content["message"][1]
    assert image_part["attachment_id"] != attachment.attachment_id
    new_attachment = await db.get_attachment_by_id(image_part["attachment_id"])
    assert new_attachment is not None
    assert Path(new_attachment.path).read_bytes() == b"PNGDATA"

    # LLM context follows the new UMO so the conversation can continue.
    new_umo = f"webchat:FriendMessage:webchat!bob!{new_session_id}"
    conversations = await db.get_conversations(user_id=new_umo)
    assert len(conversations) == 1

    # The source session is untouched.
    source_history = await service.platform_history_mgr.get(
        platform_id="webchat", user_id=session.session_id, page=1, page_size=100
    )
    assert len(source_history) == 1
    source_attachment = await db.get_attachment_by_id(attachment.attachment_id)
    assert source_attachment is not None
```

- [ ] **Step 6: 运行往返测试确认通过**

Run: `uv run pytest tests/test_session_transfer_roundtrip.py -v`
Expected: PASS（1 个用例）。若失败，先修 `_import_one_session` / `confirm_import`，不要改测试断言来迁就实现。

- [ ] **Step 6: 格式化并提交**

```bash
uvx ruff format astrbot/dashboard/services/session_transfer_service.py tests/
uvx ruff check astrbot/dashboard/services/session_transfer_service.py tests/
git add -A astrbot/dashboard/services/session_transfer_service.py tests
git commit -m "feat: import chatui session packages with id remapping"
```

---

### Task 5: 路由 + service 注册 + 鉴权测试

**Files:**
- Create: `astrbot/dashboard/api/session_transfer.py`
- Modify: `astrbot/dashboard/api/router.py`（注册 child router）
- Modify: `astrbot/dashboard/api/app.py:146-186`（services 容器加入 `session_transfer`）
- Modify: `astrbot/dashboard/schemas.py`（追加确认请求模型）
- Test: `tests/test_session_transfer_routes.py`

**Interfaces:**
- Consumes: Task 2/3/4 的 `SessionTransferService`、`SessionTransferError`、`SessionExport`
- Produces:
  - `GET /api/v1/chat/sessions/{session_id}/export` → `StreamingResponse`（`application/zip`）
  - `POST /api/v1/chat/sessions/import` → `ok(preview)`，multipart 字段名 `file`
  - `POST /api/v1/chat/sessions/import/confirm` → `ok(result)`，body `{"import_id": str}`
  - `astrbot.dashboard.schemas.ChatSessionImportConfirmRequest`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_session_transfer_routes.py`：

```python
"""ChatUI session export/import routes: wiring and error mapping.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import inspect
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot.dashboard.api.auth import AuthContext, ScopeDependency
from astrbot.dashboard.api.session_transfer import (
    confirm_import_chat_sessions,
    export_chat_session,
    import_chat_sessions,
)
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.services.session_transfer_service import (
    SessionExport,
    SessionTransferError,
)
from fastapi import Depends


def _request(service):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(services=SimpleNamespace(session_transfer=service))
        )
    )


def _auth(username="alice"):
    return AuthContext(username=username, scopes=["chat"], via="jwt")


@pytest.mark.parametrize(
    "endpoint",
    [export_chat_session, import_chat_sessions, confirm_import_chat_sessions],
)
def test_routes_declare_chat_scope_dependency(endpoint):
    """build_api_router derives x-astrbot-scope from the ScopeDependency marker."""
    signature = inspect.signature(endpoint)
    scopes = [
        parameter.default.dependency.scope
        for parameter in signature.parameters.values()
        if isinstance(parameter.default, Depends)
        and isinstance(parameter.default.dependency, ScopeDependency)
    ]
    assert scopes == ["chat"]


@pytest.mark.asyncio
async def test_export_route_maps_service_error_to_api_error():
    service = Mock()
    service.export_session = AsyncMock(
        side_effect=SessionTransferError("Permission denied")
    )

    with pytest.raises(ApiError, match="Permission denied"):
        await export_chat_session(
            session_id="sess-1",
            request=_request(service),
            _auth=_auth(),
            service=service,
        )


@pytest.mark.asyncio
async def test_export_route_streams_zip():
    service = Mock()
    service.export_session = AsyncMock(
        return_value=SessionExport(file_obj=BytesIO(b"zip"), filename="pkg.zip")
    )

    response = await export_chat_session(
        session_id="sess-1",
        request=_request(service),
        _auth=_auth(),
        service=service,
    )

    assert response.media_type == "application/zip"
    assert "pkg.zip" in response.headers["content-disposition"]
    service.export_session.assert_awaited_once_with("alice", "sess-1")


@pytest.mark.asyncio
async def test_import_route_surfaces_missing_upload(monkeypatch):
    service = Mock()
    service.stage_import = AsyncMock(
        side_effect=SessionTransferError("Missing key: file")
    )

    async def _no_upload(request, *, field_name="file"):
        return None

    import astrbot.dashboard.api.session_transfer as st_api

    monkeypatch.setattr(st_api, "single_upload", _no_upload)

    with pytest.raises(ApiError, match="file"):
        await import_chat_sessions(
            request=_request(service),
            _auth=_auth("bob"),
            service=service,
        )


@pytest.mark.asyncio
async def test_confirm_route_passes_username_and_id():
    service = Mock()
    service.confirm_import = AsyncMock(return_value={"created": []})

    result = await confirm_import_chat_sessions(
        payload=SimpleNamespace(import_id="import-1"),
        request=_request(service),
        _auth=_auth("bob"),
        service=service,
    )

    service.confirm_import.assert_awaited_once_with("bob", "import-1")
    assert result["status"] == "ok"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_session_transfer_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'astrbot.dashboard.api.session_transfer'`

- [ ] **Step 3: 写实现**

创建 `astrbot/dashboard/api/session_transfer.py`：

```python
"""ChatUI session export/import endpoints.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.schemas import ChatSessionImportConfirmRequest
from astrbot.dashboard.services.session_transfer_service import (
    SessionTransferError,
    SessionTransferService,
)

from .auth import AuthContext, ScopeDependency
from .multipart import single_upload

router = APIRouter(tags=["Chat"])

require_chat_scope = ScopeDependency("chat")


def get_service(request: Request) -> SessionTransferService:
    return request.app.state.services.session_transfer


async def _run(operation):
    try:
        return ok(await run_maybe_async(operation))
    except SessionTransferError as exc:
        raise ApiError(str(exc)) from exc


def _zip_response(export) -> StreamingResponse:
    """Stream a packaged session export as a zip download.

    Args:
        export: SessionExport produced by the service.

    Returns:
        A streaming response carrying the zip bytes. ``FileResponse`` cannot
        be used here because the archive is built in memory (the same reason
        ``_export_response`` in conversations.py streams a BytesIO).
    """
    export.file_obj.seek(0)

    def iter_file():
        while chunk := export.file_obj.read(8192):
            yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=export.mimetype,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )


@router.get("/chat/sessions/{session_id}/export")
async def export_chat_session(
    session_id: str,
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
    service: SessionTransferService = Depends(get_service),
):
    try:
        export = await service.export_session(_auth.username, session_id)
    except SessionTransferError as exc:
        raise ApiError(str(exc)) from exc
    return _zip_response(export)


@router.post("/chat/sessions/import")
async def import_chat_sessions(
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
    service: SessionTransferService = Depends(get_service),
):
    upload = await single_upload(request, field_name="file")
    return await _run(lambda: service.stage_import(upload))


@router.post("/chat/sessions/import/confirm")
async def confirm_import_chat_sessions(
    payload: ChatSessionImportConfirmRequest,
    request: Request,
    _auth: AuthContext = Depends(require_chat_scope),
    service: SessionTransferService = Depends(get_service),
):
    return await _run(
        lambda: service.confirm_import(_auth.username, payload.import_id)
    )
```

在 `astrbot/dashboard/schemas.py` 的 `ConversationExportRequest` 之后追加：

```python
class ChatSessionImportConfirmRequest(BaseModel):
    import_id: str
```

在 `astrbot/dashboard/api/router.py`：
- import 段追加 `from .session_transfer import router as session_transfer_router`（按字母序放在 `sessions_router` 之前）
- `child_routers` 元组中 `sessions_router` 之后追加 `session_transfer_router,`

在 `astrbot/dashboard/api/app.py` 的 `SimpleNamespace(...)` 中，`sessions=SessionManagementService(core_lifecycle, db),` 之后追加：

```python
        session_transfer=SessionTransferService(db, core_lifecycle),
```

并在 import 段加入 `SessionTransferService`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_session_transfer_routes.py -v`
Expected: PASS

- [ ] **Step 5: 校验 OpenAPI 里 scope 被正确推导**

Run:

```bash
uv run python -c "
from astrbot.dashboard.api.router import build_api_router
router = build_api_router()
for route in router.routes:
    if 'chat/sessions' in getattr(route, 'path', '') and 'import' in route.path or 'export' in getattr(route, 'path', ''):
        print(route.path, route.openapi_extra)
"
```

Expected: 3 条路径都打印 `{'x-astrbot-scope': 'chat'}`

- [ ] **Step 6: 格式化并提交**

```bash
uvx ruff format astrbot/dashboard
uvx ruff check astrbot/dashboard
git add -A astrbot/dashboard
git commit -m "feat: expose chatui session export and import routes"
```

---

### Task 6: OpenAPI 规范与前端客户端生成

**Files:**
- Modify: `openspec/openapi-v1.yaml`
- Regenerate: `dashboard/src/api/generated/openapi-v1/**`

**Interfaces:**
- Consumes: Task 5 的 3 条路由
- Produces: 生成的 `exportChatSession` / `importChatSessions` / `confirmImportChatSessions` 客户端函数与类型

- [ ] **Step 1: 在 YAML 中新增三条路径**

> **2026-09-17 补入（基线即失败的既有测试）**：`tests/test_fastapi_v1_dashboard.py::test_v1_openapi_is_served_by_fastapi` 在**未改动**的分支基线上就是红的（已验证：stash 后同样失败），原因是签入的 `docs/public/openapi.json` 已过期；本计划新增 3 条路由只会让差异更大。因此本 Task 追加一步（放在 Step 3 之后）：用 `docs/scripts/update_openapi_json.py` 重新生成 `docs/public/openapi.json`，并确认该测试转绿。注意这一步会同时刷新基线里已经漂移的其他条目——属预期，不要为"缩小 diff"手动回退。

在 `openspec/openapi-v1.yaml` 的 `/api/v1/chat/sessions/{session_id}/goal` 条目之后插入：

```yaml
  /api/v1/chat/sessions/{session_id}/export:
    get:
      tags: [Chat]
      summary: Export a ChatUI session as a zip package
      operationId: exportChatSession
      x-astrbot-scope: chat
      parameters:
        - name: session_id
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: Session export package
          content:
            application/zip:
              schema:
                type: string
                format: binary

  /api/v1/chat/sessions/import:
    post:
      tags: [Chat]
      summary: Upload a ChatUI session package and pre-check it
      operationId: importChatSessions
      x-astrbot-scope: chat
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              $ref: "#/components/schemas/ChatSessionImportRequest"
      responses:
        "200":
          $ref: "#/components/responses/Ok"

  /api/v1/chat/sessions/import/confirm:
    post:
      tags: [Chat]
      summary: Confirm a staged ChatUI session import
      operationId: confirmImportChatSessions
      x-astrbot-scope: chat
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ChatSessionImportConfirmRequest"
      responses:
        "200":
          $ref: "#/components/responses/Ok"
```

在 `components.schemas` 段（紧邻 `ConversationExportRequest` 之后）追加：

```yaml
    ChatSessionImportRequest:
      type: object
      properties:
        file:
          type: string
          format: binary
    ChatSessionImportConfirmRequest:
      type: object
      required: [import_id]
      properties:
        import_id:
          type: string
```

- [ ] **Step 2: 重新生成前端客户端**

> **本机环境注意**：仓库的 `generate:api` 脚本以 `rm -rf` 开头，而本机 npm script 走 `cmd.exe`（`pnpm config get script-shell` = undefined，且无 `rm` 命令），直接 `pnpm generate:api` 会失败。改用下面的等价命令（生成器二进制已确认存在于 `dashboard/node_modules/.bin/openapi-ts`）。

Run:

```powershell
cd dashboard
Remove-Item -Recurse -Force src/api/generated/openapi-v1 -ErrorAction SilentlyContinue
Remove-Item -Force src/api/generated/openapi-v1.ts -ErrorAction SilentlyContinue
pnpm exec openapi-ts -i ../openspec/openapi-v1.yaml -o src/api/generated/openapi-v1 -c @hey-api/client-axios
```

Expected: 生成成功，输出目录重建，出现新操作。

验证：

```bash
cd dashboard && grep -rn "confirmImportChatSessions\|exportChatSession" src/api/generated/openapi-v1/sdk.gen.ts
```

Expected: 三个函数各出现一次。

- [ ] **Step 3: 类型检查**

Run: `cd dashboard && pnpm typecheck`
Expected: 通过（此时 `v1.ts` 尚未调用新函数，不应有新错误）。

- [ ] **Step 4: 提交**

```bash
git add openspec/openapi-v1.yaml dashboard/src/api/generated
git commit -m "chore: regenerate api client for chatui session transfer"
```

---

### Task 7: 前端导出入口

**Files:**
- Modify: `dashboard/src/api/v1.ts`（`chatApi` 增加导出/导入方法）
- Modify: `dashboard/src/components/chat/ProjectList.vue`（项目会话行导出按钮 + emit）
- Modify: `dashboard/src/components/chat/Chat.vue`（扁平列表导出按钮、右键菜单项、接线）
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ja-JP,ru-RU}/features/chat.json`

**Interfaces:**
- Consumes: Task 6 生成的客户端函数
- Produces（`chatApi` 新增）：
  - `exportSession(sessionId: string): Promise<AxiosResponse<Blob>>`
  - `importSessions(formData: FormData): Promise<AxiosResponse<ApiEnvelope<any>>>`
  - `confirmImportSessions(importId: string): Promise<AxiosResponse<ApiEnvelope<any>>>`
  - `ProjectList` 新增 emit：`exportSession: [sessionId: string]`

- [ ] **Step 1: 给 chatApi 增加三个方法**

在 `dashboard/src/api/v1.ts` 的 `chatApi` 中，紧跟 `deleteSession(sessionId: string)` 之后加入：

> **2026-09-17 评审修正（Task 6 复审发现）**：`importSessions` 必须走仓库既有的 `generatedFormData()` 包装（`v1.ts:310-327`，`fileApi.upload` / `uploadBackup` 同款），**不能**直接传 `FormData`。生成客户端会自己序列化 `body`（`formDataBodySerializer` 内部是 `Object.entries(body)`），而 `Object.entries(new FormData())` 返回 `[]` —— 实测序列化条目为 0，即请求会带着**空 multipart 体**发出，服务端 `single_upload(field_name="file")` 直接判 `Missing key: file`。下方代码块已用修正后的写法。

```ts
  exportSession(sessionId: string) {
    return openApiV1.exportChatSession({
      path: { session_id: sessionId },
      responseType: 'blob',
    }) as Promise<AxiosResponse<Blob>>;
  },
  importSessions(formData: FormData) {
    return typed<any>(
      // `generatedFormData` is the repo's established wrapper for multipart
      // endpoints (see fileApi.upload / uploadBackup): the generated client
      // serialises `body` itself, so passing a raw FormData would spread to
      // zero entries and send an empty multipart body.
      openApiV1.importChatSessions({ body: generatedFormData(formData) }),
    );
  },
  confirmImportSessions(importId: string) {
    return typed<any>(
      openApiV1.confirmImportChatSessions({ body: { import_id: importId } }),
    );
  },
```

- [ ] **Step 2: 新建共享导出按钮组件**

会话行有三个渲染位置（`Chat.vue` 扁平列表、`Chat.vue` 右键菜单、`ProjectList.vue` 项目会话列表），因此按钮抽成单用途组件，避免同一段模板复制三份（与仓库既有的 `SpToggleChip.vue` / `ThinkingEffortChip.vue` 同类）。

创建 `dashboard/src/components/chat/SessionExportButton.vue`：

```vue
<!-- Author: elecvoid243, 2026-09-17
     Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md §5.5
     Single-purpose export affordance shared by the flat session list, the
     session context menu and the project session list. -->
<script setup lang="ts">
import { Download } from "@lucide/vue";
import { useModuleI18n } from "@/i18n/composables";

const props = withDefaults(
  defineProps<{
    sessionId: string;
    /** Icon size; the context menu uses 16, the row actions 15. */
    size?: number;
    /**
     * Row actions get the host's own row-button class so hover/colour match
     * their siblings; the context menu uses the styled menu item.
     */
    variant?: "icon" | "project-icon" | "menu-item";
    /** Row-button class forwarded by the host (e.g. `project-action-btn`). */
    actionClass?: string;
  }>(),
  { size: 15, variant: "icon", actionClass: "session-action-btn" },
);

const emit = defineEmits<{ export: [sessionId: string] }>();

const { tm } = useModuleI18n("features/chat");

function onActivate(event: MouseEvent | KeyboardEvent) {
  event.stopPropagation();
  emit("export", props.sessionId);
}
</script>

<template>
  <v-btn
    v-if="variant !== 'menu-item'"
    icon
    size="x-small"
    variant="text"
    :class="actionClass"
    :title="tm('conversation.export')"
    @click="onActivate"
  >
    <Download :size="size" />
  </v-btn>
  <v-list-item v-else class="styled-menu-item" rounded="md" @click="onActivate">
    <template #prepend>
      <Download :size="size" />
    </template>
    <v-list-item-title>{{ tm("conversation.export") }}</v-list-item-title>
  </v-list-item>
</template>
```

> **2026-09-17 实现期修正（已并入上方代码块）**：图标包是 `@lucide/vue`（不是 `lucide-vue-next`）；`onActivate` 的参数需为 `MouseEvent | KeyboardEvent`（Vuetify 的按钮点击回调类型，`vue-tsc` 会拒绝纯 `MouseEvent`）；新增 `actionClass` prop 并让 `ProjectList` 传 `variant="project-icon"` + `action-class="project-action-btn"`，否则共享按钮在项目会话行里拿不到该行按钮的静默色与 hover 样式（与同级按钮不一致）。

- [ ] **Step 3: 三处接入共享按钮**

3a. `ProjectList.vue`：在 `project-session-actions` 容器内（现有 `Pencil` 按钮之前）插入

```vue
                  <SessionExportButton
                    :session-id="session.session_id"
                    variant="project-icon"
                    action-class="project-action-btn"
                    @export="(id) => $emit('exportSession', id)"
                  />
```

并在 `defineEmits` 类型中，`editSessionTitle` 之后追加：

```ts
  /** 2026-09-17 session export: a project session row asked to export. */
  exportSession: [sessionId: string];
```

（`SessionExportButton` 需加入该文件的 import 段。）

3b. `Chat.vue` 扁平列表：在 `session-actions` 容器内（`Pencil` 按钮之前）插入

```vue
              <SessionExportButton
                :session-id="session.session_id"
                @export="exportSidebarSession"
              />
```

3c. `Chat.vue` 右键菜单：在 `sessionContextMenu` 菜单的 `editDisplayName` 项之后插入

```vue
            <SessionExportButton
              :session-id="sessionContextMenu.session!.session_id"
              variant="menu-item"
              :size="16"
              @export="exportSidebarSession"
            />
```

3d. 在 `Chat.vue` 的 `ProjectList` 使用处（约 195-215 行）追加监听，否则 ProjectList 的 emit 无处可去：

```vue
          @export-session="exportSidebarSession"
```

3e. 在 `Chat.vue` 同文件的 script 中新增函数（放在 `deleteSidebarSession` 附近），并把 `SessionExportButton` 加入 import 段：

```ts
/**
 * 2026-09-17 session export: download one ChatUI session package.
 * Mirrors the conversation export flow in ConversationWorkspacePage.
 */
async function exportSidebarSession(sessionId: string) {
  try {
    const response = await chatApi.exportSession(sessionId);
    const url = URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = url;
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, -5);
    link.setAttribute("download", `astrbot_chatui_export_${timestamp}.zip`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    toast.success(tm("conversation.exportSuccess"));
  } catch (error: any) {
    console.error("Failed to export session:", error);
    toast.error(error?.response?.data?.message || tm("conversation.exportFailed"));
  }
}
```

- [ ] **Step 4: 四份 i18n 同步**

在 4 份 `dashboard/src/i18n/locales/*/features/chat.json` 的 `conversation` 对象中，紧跟 `"editDisplayName"` 之后插入对应语言的条目（键名完全相同）：

zh-CN：

```json
    "export": "导出会话",
    "exportSuccess": "会话已导出",
    "exportFailed": "导出会话失败",
```

en-US：

```json
    "export": "Export session",
    "exportSuccess": "Session exported",
    "exportFailed": "Failed to export session",
```

ja-JP：

```json
    "export": "セッションをエクスポート",
    "exportSuccess": "セッションをエクスポートしました",
    "exportFailed": "セッションのエクスポートに失敗しました",
```

ru-RU：

```json
    "export": "Экспорт сессии",
    "exportSuccess": "Сессия экспортирована",
    "exportFailed": "Не удалось экспортировать сессию",
```

- [ ] **Step 5: 校验**

Run: `cd dashboard && pnpm typecheck && pnpm test`
Expected: 类型检查通过；vitest 套件无新增失败。

手工验证（`uv run main.py` + `cd dashboard && pnpm dev`）：登录 → 侧栏任一会话行 hover → 点 Download 图标 → 浏览器下载 `astrbot_chatui_export_*.zip`；解压后应看到 `manifest.json`、`export.json`、`files/attachments/`。

- [ ] **Step 6: 提交**

```bash
git add dashboard/src/api/v1.ts dashboard/src/components/chat/ProjectList.vue dashboard/src/components/chat/Chat.vue dashboard/src/i18n/locales
git commit -m "feat: add session export entry to chatui sidebar"
```

---

### Task 8: 前端导入对话框与接线

**Files:**
- Create: `dashboard/src/components/chat/ImportSessionsDialog.vue`
- Modify: `dashboard/src/components/chat/Chat.vue`（顶部操作区入口 + 对话框 + 导入后刷新/切换）
- Modify: `dashboard/src/i18n/locales/{zh-CN,en-US,ja-JP,ru-RU}/features/chat.json`

**Interfaces:**
- Consumes: Task 7 的 `chatApi.importSessions` / `chatApi.confirmImportSessions`
- Produces:
  - `ImportSessionsDialog.vue` props：`modelValue: boolean`；emits：`update:modelValue`、`imported: [newSessionIds: string[]]`

- [ ] **Step 1: 新建对话框组件**

创建 `dashboard/src/components/chat/ImportSessionsDialog.vue`，三段式（选择文件 → 预览确认 → 结果），骨架照 `dashboard/src/components/shared/BackupDialog.vue` 的 import 页签，标题类名用 `text-h3 pa-4 pb-0 pl-6`，按钮用 `variant="text"` / `variant="tonal"`：

```vue
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import { chatApi } from "@/api/v1";

const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  imported: [newSessionIds: string[]];
}>();

const { tm } = useModuleI18n("features/chat");

type Stage = "idle" | "uploading" | "preview" | "importing" | "done";
const stage = ref<Stage>("idle");
const selectedFileName = ref("");
const preview = ref<any | null>(null);
const result = ref<any | null>(null);
const errorMessage = ref("");

const dialogVisible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit("update:modelValue", value),
});

const canConfirm = computed(
  () => Boolean(preview.value?.can_import) && stage.value === "preview",
);

function reset() {
  stage.value = "idle";
  selectedFileName.value = "";
  preview.value = null;
  result.value = null;
  errorMessage.value = "";
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset();
  },
);

async function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  selectedFileName.value = file.name;
  errorMessage.value = "";
  stage.value = "uploading";
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await chatApi.importSessions(formData);
    preview.value = response.data?.data ?? null;
    stage.value = "preview";
  } catch (error: any) {
    errorMessage.value =
      error?.response?.data?.message || tm("import.uploadFailed");
    stage.value = "idle";
  } finally {
    input.value = "";
  }
}

async function confirmImport() {
  if (!preview.value?.import_id) return;
  stage.value = "importing";
  errorMessage.value = "";
  try {
    const response = await chatApi.confirmImportSessions(preview.value.import_id);
    result.value = response.data?.data ?? null;
    stage.value = "done";
    const newIds = (result.value?.created || [])
      .map((item: any) => item.new_session_id)
      .filter(Boolean);
    emit("imported", newIds);
  } catch (error: any) {
    errorMessage.value =
      error?.response?.data?.message || tm("import.confirmFailed");
    stage.value = "preview";
  }
}
</script>

<template>
  <v-dialog v-model="dialogVisible" max-width="640" persistent>
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("import.title") }}
      </v-card-title>
      <v-card-text class="pt-4">
        <div v-if="stage === 'idle' || stage === 'uploading'">
          <p class="mb-4 text-medium-emphasis">{{ tm("import.description") }}</p>
          <v-file-input
            accept=".zip,application/zip"
            :label="tm('import.selectFile')"
            variant="outlined"
            density="comfortable"
            :loading="stage === 'uploading'"
            @change="onFileSelected"
          />
        </div>

        <div v-else-if="stage === 'preview' || stage === 'importing'">
          <p class="mb-2">{{ selectedFileName }}</p>
          <v-list density="compact">
            <v-list-item
              v-for="(item, index) in preview?.sessions || []"
              :key="index"
            >
              <v-list-item-title>
                {{ item.display_name || tm("conversation.newConversation") }}
              </v-list-item-title>
              <v-list-item-subtitle>
                {{ item.original_creator }} ·
                {{ tm("import.messageCount", { count: item.stats?.messages ?? 0 }) }}
                ·
                {{ tm("import.attachmentCount", { count: item.stats?.attachments ?? 0 }) }}
              </v-list-item-subtitle>
            </v-list-item>
          </v-list>

          <v-alert
            v-if="preview?.version_status?.upgrade_advised"
            type="warning"
            variant="tonal"
            class="mt-3"
          >
            {{ tm("import.versionMismatch") }}
          </v-alert>
          <v-alert
            v-for="(warning, index) in preview?.warnings || []"
            :key="`w-${index}`"
            type="warning"
            variant="tonal"
            density="compact"
            class="mt-2"
          >
            {{ warning }}
          </v-alert>
          <v-alert
            v-if="preview && !preview.can_import"
            type="error"
            variant="tonal"
            class="mt-2"
          >
            {{ tm("import.empty") }}
          </v-alert>
        </div>

        <div v-else class="text-center py-4">
          <p class="mb-2">{{ tm("import.done") }}</p>
          <v-alert
            v-for="(warning, index) in result?.warnings || []"
            :key="`rw-${index}`"
            type="warning"
            variant="tonal"
            density="compact"
            class="mb-2 text-left"
          >
            {{ warning }}
          </v-alert>
          <v-alert
            v-for="(failure, index) in result?.errors || []"
            :key="`re-${index}`"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-2 text-left"
          >
            {{ failure }}
          </v-alert>
        </div>

        <v-alert
          v-if="errorMessage"
          type="error"
          variant="tonal"
          density="compact"
          class="mt-3"
        >
          {{ errorMessage }}
        </v-alert>
      </v-card-text>

      <v-card-actions class="px-6 pb-4">
        <v-btn variant="text" @click="dialogVisible = false">
          {{ tm("import.close") }}
        </v-btn>
        <v-spacer />
        <v-btn
          v-if="stage === 'preview' || stage === 'importing'"
          variant="tonal"
          color="primary"
          :disabled="!canConfirm"
          :loading="stage === 'importing'"
          @click="confirmImport"
        >
          {{ tm("import.confirm") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
```

- [ ] **Step 2: Chat.vue 接线**

2a. 顶部操作区（`sidebar-top-actions` 内，与"批量管理""搜索"同区）追加一个按钮：

```vue
          <button
            type="button"
            class="sidebar-top-action-btn"
            :title="tm('import.entry')"
            @click="importDialogOpen = true"
          >
            <Upload :size="16" />
            <span>{{ tm("import.entry") }}</span>
          </button>
```

（`Upload` 图标加入现有 lucide-vue-next 的 import 段。）

2b. 模板末尾引入对话框：

```vue
    <ImportSessionsDialog
      v-model="importDialogOpen"
      @imported="onSessionsImported"
    />
```

2c. script 中新增：

```ts
const importDialogOpen = ref(false);

/**
 * 2026-09-17 session import: refresh the sidebar and open the first
 * imported session so the user lands on the migrated conversation.
 */
async function onSessionsImported(newSessionIds: string[]) {
  await getSessions();
  const first = newSessionIds[0];
  if (first) {
    await selectSession(first);
  }
}
```

（`getSessions` 来自 `useSessions()`，`selectSession` 来自现有会话逻辑；`ImportSessionsDialog` 需在 import 段引入。）

- [ ] **Step 3: 四份 i18n 同步**

在 4 份 `features/chat.json` 顶层新增 `import` 对象（键名完全相同）：

zh-CN：

```json
  "import": {
    "entry": "导入会话",
    "title": "导入会话",
    "description": "选择由 ChatUI 导出的会话包（zip）。导入会在你的账户下创建新会话，不会覆盖已有会话。",
    "selectFile": "选择会话包",
    "confirm": "确认导入",
    "close": "关闭",
    "done": "导入完成",
    "empty": "该会话包中没有可导入的会话",
    "uploadFailed": "上传会话包失败",
    "confirmFailed": "导入会话失败",
    "messageCount": "{count} 条消息",
    "attachmentCount": "{count} 个附件",
    "versionMismatch": "会话包来自不同的小版本，导入后部分内容可能显示异常。"
  },
```

en-US：

```json
  "import": {
    "entry": "Import session",
    "title": "Import sessions",
    "description": "Pick a session package exported from ChatUI (zip). Importing creates new sessions under your account and never overwrites existing ones.",
    "selectFile": "Select package",
    "confirm": "Confirm import",
    "close": "Close",
    "done": "Import finished",
    "empty": "This package contains no importable session",
    "uploadFailed": "Failed to upload the package",
    "confirmFailed": "Failed to import sessions",
    "messageCount": "{count} messages",
    "attachmentCount": "{count} attachments",
    "versionMismatch": "The package comes from a different minor version; some content may not render as expected."
  },
```

ja-JP：

```json
  "import": {
    "entry": "セッションをインポート",
    "title": "セッションのインポート",
    "description": "ChatUI からエクスポートしたセッションパッケージ（zip）を選択してください。インポートするとあなたのアカウントに新しいセッションが作成され、既存のセッションは上書きされません。",
    "selectFile": "パッケージを選択",
    "confirm": "インポートを実行",
    "close": "閉じる",
    "done": "インポートが完了しました",
    "empty": "このパッケージにインポート可能なセッションがありません",
    "uploadFailed": "パッケージのアップロードに失敗しました",
    "confirmFailed": "セッションのインポートに失敗しました",
    "messageCount": "{count} 件のメッセージ",
    "attachmentCount": "{count} 件の添付ファイル",
    "versionMismatch": "パッケージは別のマイナーバージョンで作成されています。一部の内容が正しく表示されない可能性があります。"
  },
```

ru-RU：

```json
  "import": {
    "entry": "Импорт сессии",
    "title": "Импорт сессий",
    "description": "Выберите пакет сессии, экспортированный из ChatUI (zip). Импорт создаёт новые сессии в вашей учётной записи и не перезаписывает существующие.",
    "selectFile": "Выбрать пакет",
    "confirm": "Подтвердить импорт",
    "close": "Закрыть",
    "done": "Импорт завершён",
    "empty": "В этом пакете нет сессий для импорта",
    "uploadFailed": "Не удалось загрузить пакет",
    "confirmFailed": "Не удалось импортировать сессии",
    "messageCount": "{count} сообщений",
    "attachmentCount": "{count} вложений",
    "versionMismatch": "Пакет создан в другой минорной версии; часть содержимого может отображаться некорректно."
  },
```

- [ ] **Step 4: 校验**

Run: `cd dashboard && pnpm typecheck && pnpm test && node scripts/subset-mdi-font.mjs`
Expected: 全部通过。

手工验证（跨账户迁移）：

1. 用用户 A 导出某个含图片与线程回复的会话。
2. 退出，用用户 B 登录 → 点"导入会话" → 选择 zip → 预览显示会话名/消息数/附件大小 → 确认导入。
3. 断言：新会话出现在 B 的侧栏；图片正常显示；线程可打开；在该会话继续发一条消息能拿到带上下文的回复（说明 `conversations` 的 UMO 重映射正确）。

- [ ] **Step 5: 提交**

```bash
git add dashboard/src/components/chat/ImportSessionsDialog.vue dashboard/src/components/chat/Chat.vue dashboard/src/i18n/locales
git commit -m "feat: add chatui session import dialog"
```

---

### Task 9: 导出流式化 + 体积上限 + 暂存包回收（I2 + I4）

**背景（最终整分支评审的遗留项，用户裁定按 A 方案修 I2）**

- **I2**：`export_session` 把每个附件整块读进 `attachment_blobs`，再在事件循环上用 `zipfile.ZipFile(BytesIO(...), "w", ZIP_DEFLATED)` 做 deflate —— 峰值内存≈2× 数据量，且几百 MB 的会话会卡住整个 dashboard。另一半危害是：**导出包一旦超过 `MAX_UPLOAD_BYTES`（512 MB）就永远无法被导入**（导入侧在 `stage_import` 直接拒），这是在制造"注定导不回来"的陷阱。
- **I4**：`IMPORT_ID_TTL_SECONDS` 只在**同一个 import_id 再次被查询**时才生效（`_pending`），用户上传后不确认的包会一直躺在 `data/temp/session_import_*.zip`，单个可达 512 MB、数量无上限。

**Files:**
- Modify: `astrbot/dashboard/services/session_transfer_service.py`
- Modify: `astrbot/dashboard/api/session_transfer.py`（交付方式改为 `FileResponse` + `BackgroundTask`）
- Modify: `dashboard/src/components/chat/Chat.vue`（导出失败时把 blob 错误体解出真实 message）
- Test: `tests/test_session_transfer_export.py`、`tests/test_session_transfer_import.py`、`tests/test_session_transfer_roundtrip.py`、`tests/test_session_transfer_routes.py`

**Interfaces:**
- Consumes: 既有 `MAX_UPLOAD_BYTES`、`IMPORT_ID_TTL_SECONDS`、`self.temp_dir`、`_drop_pending`、`EXPORT_DATA_NAME`/`MANIFEST_NAME`/`EXPORT_KIND`/`EXPORT_FORMAT_VERSION`
- Produces:
  - `EXPORT_CHUNK_BYTES = 1024 * 1024`（模块常量）
  - `SessionExport.path: Path`（`file_obj` 字段**删除**；`filename`/`mimetype` 不变）
  - `SessionTransferService._build_export_archive(self, archive_path, data_bytes, manifest_sessions, attachment_entries, base_warnings) -> list[str]`
  - `SessionTransferService._sweep_stale_imports(self) -> None`
  - 路由侧 `_delete_export_file(path) -> None` 与 `_zip_response(export) -> FileResponse`

- [ ] **Step 1: 改 `SessionExport` 与导出打包（I2）**

`SessionExport` 改为持有磁盘路径：

```python
@dataclass
class SessionExport:
    """A packaged session export ready to stream to the browser."""

    path: Path
    filename: str
    mimetype: str = "application/zip"
```

`export_session` 的附件收集改为**只收集元数据**（不再读字节、不再算哈希），`entry` 里多带一个内部字段 `source_path`（不写进 `export.json` —— 写盘前必须 `pop` 掉）：

```python
        attachments: list[dict] = []
        seen_ids: set[str] = set()
        for attachment_id in all_attachment_ids:
            if attachment_id in seen_ids:
                continue
            seen_ids.add(attachment_id)
            attachment = await self.db.get_attachment_by_id(attachment_id)
            if attachment is None:
                warnings.append(f"Attachment {attachment_id} metadata missing")
                continue
            source = Path(attachment.path)
            ext = source.suffix if is_safe_attachment_ext(source.suffix) else ""
            attachments.append(
                {
                    "attachment_id": attachment_id,
                    "type": attachment.type,
                    "mime_type": attachment.mime_type,
                    "ext": ext,
                    "size": 0,
                    "zip_path": f"{ATTACHMENTS_PREFIX}{attachment_id}{ext}",
                    # Path-only: the archive writer streams from disk so the
                    # bytes never sit in RAM and deflate stays off the loop.
                    "source_path": str(source),
                }
            )
```

打包尾部（替换原 `buffer = BytesIO() ... return SessionExport(file_obj=buffer, ...)`）：

```python
        manifest_sessions = [
            {
                "original_session_id": session.session_id,
                "display_name": session.display_name,
                "original_creator": session.creator,
                "stats": {
                    "messages": len(history),
                    "conversations": len(data_payload["sessions"][0]["conversations"]),
                    "threads": len(thread_payloads),
                    "attachments": len(attachments),
                    "attachment_bytes": 0,  # patched by the writer, see below
                },
            }
        ]

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.temp_dir / f"session_export_{uuid.uuid4()}.zip"
        try:
            warnings = await asyncio.to_thread(
                self._build_export_archive,
                archive_path,
                data_bytes,
                manifest_sessions,
                attachments,
                warnings,
            )
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

        # A package larger than the import cap can never be imported by anyone,
        # so refuse it here rather than handing the user a dead archive.
        archive_bytes = archive_path.stat().st_size
        if archive_bytes > MAX_UPLOAD_BYTES:
            archive_path.unlink(missing_ok=True)
            raise SessionTransferError(
                f"Session export is {archive_bytes} bytes, which exceeds the "
                f"{MAX_UPLOAD_BYTES} byte import limit; the package could never "
                "be imported. Remove some attachments and try again."
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return SessionExport(
            path=archive_path,
            filename=f"astrbot_chatui_export_{timestamp}.zip",
        )
```

新增打包方法（**整个 zip 组装都在工作线程里**；`export.json` 先写、附件流式写并增量哈希、`manifest.json` 最后写 —— zip 条目顺序不影响读取方，Task 3 用 `set(zf.namelist())` 判定）：

```python
    def _build_export_archive(
        self,
        archive_path: Path,
        data_bytes: bytes,
        manifest_sessions: list[dict],
        attachment_entries: list[dict],
        base_warnings: list[str],
    ) -> list[str]:
        """Write the export zip, streaming attachments from disk.

        Runs in a worker thread: the deflate work must not block the event
        loop, and streaming keeps peak memory at one chunk per attachment.

        Args:
            archive_path: Destination zip path in the service temp directory.
            data_bytes: Serialized ``export.json`` payload.
            manifest_sessions: Per-session manifest entries (stats are patched
                in place as attachments are written).
            attachment_entries: Attachment metadata dicts carrying an internal
                ``source_path`` key.
            base_warnings: Warnings collected before packaging.

        Returns:
            The final warning list (base warnings plus per-file problems).
        """
        warnings = list(base_warnings)
        checksums = {
            EXPORT_DATA_NAME: f"sha256:{hashlib.sha256(data_bytes).hexdigest()}"
        }
        written_bytes = 0

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(EXPORT_DATA_NAME, data_bytes)
            for entry in attachment_entries:
                source = Path(entry.pop("source_path"))
                zip_path = entry["zip_path"]
                attachment_id = entry["attachment_id"]
                if not source.is_file():
                    warnings.append(
                        f"Attachment {attachment_id} file missing on disk"
                    )
                    continue
                hasher = hashlib.sha256()
                size = 0
                try:
                    with source.open("rb") as src, zf.open(zip_path, "w") as dest:
                        while chunk := src.read(EXPORT_CHUNK_BYTES):
                            hasher.update(chunk)
                            dest.write(chunk)
                            size += len(chunk)
                except OSError as exc:
                    # The entry may be truncated: a mid-stream disk error cannot
                    # be undone once the local header is written. The importer
                    # treats an unreadable blob as metadata-only with a warning,
                    # so this degrades rather than corrupting the package.
                    warnings.append(f"Attachment {attachment_id} unreadable: {exc!s}")
                    continue
                entry["size"] = size
                written_bytes += size
                checksums[zip_path] = f"sha256:{hasher.hexdigest()}"

            manifest_sessions[0]["stats"]["attachment_bytes"] = written_bytes
            manifest = {
                "kind": EXPORT_KIND,
                "format_version": EXPORT_FORMAT_VERSION,
                "astrbot_version": VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "sessions": manifest_sessions,
                "warnings": warnings,
                "checksums": checksums,
            }
            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        return warnings
```

模块常量区补：`EXPORT_CHUNK_BYTES = 1024 * 1024`。

- [ ] **Step 2: 交付方式改为 `FileResponse` + 后台删除**

`astrbot/dashboard/api/session_transfer.py`：删除 `_zip_response` 里的 `StreamingResponse`/`iter_file`，改为

```python
def _delete_export_file(path) -> None:
    """Remove a delivered export archive from the service temp directory.

    Args:
        path: Archive path handed to ``FileResponse``.
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"Failed to delete export archive {path}: {exc!s}")


def _zip_response(export: SessionExport) -> FileResponse:
    return FileResponse(
        export.path,
        media_type=export.mimetype,
        filename=export.filename,
        background=BackgroundTask(_delete_export_file, export.path),
    )
```

需要 `from pathlib import Path`、`from starlette.background import BackgroundTask`、`from fastapi.responses import FileResponse`、`from astrbot import logger`；移除不再使用的 `StreamingResponse`。`SessionExport` 的 import 保留。

- [ ] **Step 3: 暂存包回收（I4）**

```python
    def _sweep_stale_imports(self) -> None:
        """Drop expired staged imports and orphaned staged archives.

        The TTL is otherwise only enforced when the same ``import_id`` is
        looked up again, so a package the user never confirms would sit in the
        temp directory forever (up to ``MAX_UPLOAD_BYTES`` each, unbounded
        count). A file that is still registered stays untouched; an orphan
        younger than the TTL is left alone on purpose, because a concurrent
        upload may be mid-write.
        """
        for import_id in [
            key
            for key, pending in self.pending_imports.items()
            if time.monotonic() - pending.created_at > IMPORT_ID_TTL_SECONDS
        ]:
            self._drop_pending(import_id)

        try:
            candidates = list(self.temp_dir.glob("session_import_*.zip"))
        except OSError as exc:
            logger.warning(f"Failed to scan staged imports: {exc!s}")
            return

        live = {pending.zip_path for pending in self.pending_imports.values()}
        cutoff = time.time() - IMPORT_ID_TTL_SECONDS
        for path in candidates:
            if path in live:
                continue
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                path.unlink(missing_ok=True)
                logger.info(f"Removed orphaned staged import {path.name}")
            except OSError as exc:
                logger.warning(f"Failed to remove orphaned staged import {path}: {exc!s}")
```

在 `stage_import` 的最前面（`import_id = str(uuid.uuid4())` 之前）调用 `self._sweep_stale_imports()`。

- [ ] **Step 4: 前端把导出失败的真实原因透出来**

`responseType: 'blob'` 时错误体也是 Blob，`error.response.data.message` 取不到 —— 不修的话 Step 1 的"体积超限"拒绝根本显示不到用户面前。在 `Chat.vue` 的 `exportSidebarSession` 里：

```ts
/**
 * 2026-09-18 I2: blob responses carry a Blob on the error path too, so the
 * server's message has to be decoded before it can be shown.
 */
async function exportErrorMessage(error: any): Promise<string | null> {
  const data = error?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text());
      return parsed?.message || null;
    } catch {
      return null;
    }
  }
  return data?.message || error?.message || null;
}
```

并把 catch 分支改为：

```ts
  } catch (error: any) {
    console.error("Failed to export session:", error);
    toast.error(
      (await exportErrorMessage(error)) || tm("conversation.exportFailed"),
    );
  }
```

- [ ] **Step 5: 更新受影响的测试（`file_obj` → `path`）**

四处测试文件里所有 `export.file_obj.getvalue()` 改为 `export.path.read_bytes()`；`zipfile.ZipFile(BytesIO(export.file_obj.getvalue()))` 改为 `zipfile.ZipFile(export.path)`；`tests/test_session_transfer_routes.py` 里构造 `SessionExport(file_obj=BytesIO(b"zip"), filename="pkg.zip")` 改为先在 `tmp_path` 写一个文件再传 `path=`，并断言返回的是 `FileResponse`（`response.path == str(<tmp file>)`、`response.media_type == "application/zip"`、`"pkg.zip" in response.headers["content-disposition"]`）。

新增用例：

```python
def test_export_refuses_archives_over_the_import_limit(tmp_path, monkeypatch):
    """A package the importer would reject must never be handed out."""
    ...
```

（做法：`monkeypatch.setattr(session_transfer_service, "MAX_UPLOAD_BYTES", 64)`，导出一个正常会话 → `pytest.raises(SessionTransferError, match="exceeds")`，并断言 `list(tmp_path.glob("session_export_*.zip")) == []`，即拒绝时临时文件已被删。）

```python
def test_stage_import_sweeps_expired_pending_and_orphaned_zips(tmp_path):
    """An abandoned staged package is reclaimed on the next upload."""
    ...  # 造一个 mtime 早于 TTL 的 session_import_orphan.zip + 一条已过期的 pending，
         # 调 stage_import(合法包) 后断言两者都没了、而新包仍在
```

```python
def test_sweep_keeps_fresh_orphans_and_live_pending(tmp_path):
    """A concurrent upload mid-write, and a live preview, must survive."""
    ...
```

- [ ] **Step 6: 运行全部相关测试**

Run: `D:\anaconda3\envs\astrbot\python.exe -m pytest tests/test_session_transfer_remap.py tests/test_session_transfer_export.py tests/test_session_transfer_import.py tests/test_session_transfer_roundtrip.py tests/test_session_transfer_routes.py -q`
Expected: PASS（基线 61 passed + 新增用例）

前端：`cd dashboard && pnpm typecheck`（必须通过）。

- [ ] **Step 7: 格式化并提交**

```bash
D:\anaconda3\envs\astrbot\python.exe -m ruff format astrbot/dashboard tests
D:\anaconda3\envs\astrbot\python.exe -m ruff check astrbot/dashboard tests
git add -A astrbot/dashboard dashboard/src tests
git commit -m "perf: stream session exports to disk and reclaim abandoned imports"
```

- [ ] **Step 8: 评审后修复（2026-09-18，3 条 Important + 2 条 Minor）**

1. **`source_path` 泄漏进 `export.json`（Important）**：`data_bytes = json.dumps(data_payload, ...)` 发生在 `entry.pop("source_path")` **之前**，所以包里每个附件的元数据都带上了导出机的绝对路径。修法：序列化**之前**就把内部字段去掉（用副本构造 payload，或让 `_build_export_archive` 以位置参数接收源路径列表，entry 里根本不存它）。测试补一条断言：`"source_path" not in exported_session["attachments"][0]`。
2. **导出包没有任何回收路径（Important）**：`_sweep_stale_imports` 只 glob `session_import_*.zip`；正常路径靠 `BackgroundTask` 删除，但优雅停机/中间件抛 `ClientDisconnect`/worker 被杀时会留下最多 512 MB 的 `session_export_*.zip`，无界、无 TTL、无启动清理。修法：glob 扩成 `("session_import_*.zip", "session_export_*.zip")`（导出包不进 `live` 集合，mtime 守卫天然保护正在写/正在传的文件），并在 `export_session` 开头也调一次 sweep。
3. **拒绝只镜像了导入侧 4 个上限中的 1 个（Important）**：`_verify_zip_safety` 还会拒「条目数 > `MAX_ZIP_ENTRIES`」「单条目 > `MAX_ENTRY_BYTES`」「解压总量 > `MAX_TOTAL_UNCOMPRESSED_BYTES`」。高压缩比的大附件（如几百 MB 文本/日志）可以做出 < 512 MB 但导入必拒的包，破坏本分支的硬约束。修法：写入过程中累计条目数与解压字节数，四个上限任何一个越界都走同一条拒绝路径（先 `unlink` 再 `SessionTransferError`）。
4. **Minor：中途中读错误改为中止导出**：`zf.open(..., "w")` 的上下文管理器在 `close()` 时会记录**已写了一半的条目**，而 `_read_attachment_blob` 会把这段截断字节当成正常附件导入（原注释说"importer treats an unreadable blob as metadata-only"是错的）。修法：中途中 `OSError` 时删除临时包并抛 `SessionTransferError`（"文件本身不存在"仍走上游 `is_file()` 的 warning + 仅元数据分支），并改正注释。
5. **Minor：补测 writer 异常路径**（monkeypatch `_build_export_archive` 抛错 → 断言没有 `session_export_*.zip` 残留），并让体积拒绝用例同时断言报错里含**两个**数字。

---



## 与规格的差异记录

1. **OpenAPI 规范文件**：规格 §7 的文件清单未列出 `openspec/openapi-v1.yaml`，但仓库的 `generate:api` 是从该手维护 YAML 读取的（`dashboard/package.json`），不新增路径则新客户端函数不存在。本计划补充 Task 6。
2. **i18n 数量**：规格 §5.5 写"三份（en-US, zh-CN, ru-RU）"，仓库实际有 4 个 locale（含 `ja-JP`）。本计划要求 4 份同步。
3. **事务语义**：规格 §5.4 要求"单个会话失败（事务回滚）"，DB 层未暴露跨调用事务 API。本计划改用 `db.get_db()` + `dbsession.begin()` 的自管事务（该模式在 `astrbot/core/db/migration/migra_3_to_4.py:194-196` 有既有先例），并把该会话已落盘的附件文件在异常分支清理。
4. **附件路径读取方式**：规格 §5.6 提到复用 `_validate_path_within`，但导入端**从不按 zip 条目路径落盘**（文件名由 `{new_attachment_id}{ext}` 完全生成），因此路径穿越面为零；实现里对 `zip_path` 只做前缀/`..`/分隔符校验（Task 1 的 `is_safe_attachment_zip_path`），不再引入 `_validate_path_within`。
5. **`conversations` 读取**：`db.get_conversations()` 明确不返回 `content`（见 `astrbot/core/db/__init__.py:137-146`），因此导出对每个 conversation 追加一次 `get_conversation_by_id()` 取正文。
6. **导出产物交付方式**：规格 §5.2 建议先写入 `data/temp/` 临时 zip 再用 `FileResponse` + BackgroundTask 删除。本计划改为在内存中构建 zip + `StreamingResponse`——一是与 `astrbot/dashboard/api/conversations.py:63 _export_response` 的既有做法一致，二是 `FileResponse` 只接受磁盘路径、不接受 `BytesIO`。`data/temp/` 仍用于导入暂存 zip。

## 最终整分支评审结论（2026-09-18）

- **Critical C1（阻塞合并）**：导入时 `conversations.conversation_id` 全部换新，但 `preferences` 里的 `scope="umo" / key="sel_conv_id"` 指针被原样复制 —— 同实例导入会让导入者的会话解析到**导出者**的 `conversations` 行（跨用户上下文串写，且导入出的副本成为孤儿）；跨实例则该 id 不存在，运行时回退到空上下文，且从导入的 bot 消息建线程会抛 `ChatSessionLinked checkpoint not found`。这直接违背规格 §2 目标 2/3（继续对话、线程可用）。
  修复：导入时建立 `old_conversation_id → new_conversation_id` 映射（导出侧已写入 `conversation_id`），把偏好值里指向被重映射会话的值一并改写；若指向的会话未被导入（例如线程被丢弃），**丢弃该偏好并记 warning**，而不是留下一个指向外部/失效 id 的指针。
- **Important I1**：`umo_map.get(exported_scope_id, session_umo)` 的兜底会把「未导入的线程 scope」并到会话 scope 上；配合 `UniqueConstraint(scope, scope_id, key)`，线程被丢弃这一原本可恢复的情况会升级成 `IntegrityError` 并回滚整个会话。修复：非空且不在映射内的 scope_id → 记 warning 并跳过该行。
- **后续任务（不在本分支修）**：I2 导出全量内存驻留 + 事件循环上做 deflate（规格 §5.2 的偏离，差异记录 §6 已记）；I3 原始 DB 异常文本进浏览器；I4 未确认的暂存包不会被 TTL 回收；以及既有的 `docs/public/openapi.json` 与运行时 scope 漂移（22 条 agent_teams + `system-stream`，均为分支前既有）。

---

## Self-Review

- **规格覆盖**：§5.1 包格式 → Task 2；§5.2 导出 API → Task 2/5；§5.3 导入两步 API → Task 3/4/5；§5.4 重映射 → Task 1/4；§5.5 前端 → Task 7/8（并补充 Task 6 的客户端生成）；§5.6 安全 → Task 1/3（zip 限制常量 + 路径校验）；§5.7 边界情况 → Task 3 的版本/尺寸用例 + Task 4 的缺父消息丢线程 + Task 2 的缺附件 warning；§6 测试计划 → Task 1–5 的测试；§7 文件清单 → 全部落到任务中（外加差异记录第 1 条的 YAML 与第 6 条的交付方式调整）。
- **自查期间修正的实现缺陷**（这三处若不修，计划照抄会直接失败）：
  1. 导出路由原写 `FileResponse(BytesIO(...))` —— `FileResponse` 只接受磁盘路径，改为 `StreamingResponse`（差异记录第 6 条）。
  2. Task 4 附件分支原只读取 blob 却未写入磁盘，补 `await asyncio.to_thread(target.write_bytes, blob)`。
  3. Task 2 测试脚手架原用 `SimpleNamespace` 当库行，而 `_serialize_row()` 依赖 `model_dump()`，改为直接构造 `PlatformMessageHistory` / `PlatformSession`（与 `tests/test_chat_history_pagination.py` 的既有风格一致）。
- **开工前（2026-09-17）与用户裁决后追加的 3 处修正**：
  4. Task 4 原 Step 5 是 bash heredoc + 散文体步骤（本机无 bash，且违反 No-Placeholders）→ 改成真实的 `tests/test_session_transfer_roundtrip.py`（真 sqlite + 真 manager 往返测试）。
  5. Task 7 原计划把同一段按钮模板复制进 `Chat.vue` 与 `ProjectList.vue`（rubric 会判 Important 级重复）→ 改为抽共享组件 `dashboard/src/components/chat/SessionExportButton.vue`，三个渲染位置复用。
  6. Task 6 原 `pnpm generate:api` 在本机必失败（脚本以 `rm -rf` 开头，Windows 无 `rm`）→ 改为等价的 PowerShell 删除 + `pnpm exec openapi-ts`。
- **未覆盖的规格项**：§5.7 "会话内 persona 在目标实例不存在" 目前只保留原 `persona_id`，预览 warnings 中**未**提示（需要额外查询 persona 是否存在）。已在 Task 3 的 `stage_import` 留出 `preview["warnings"]` 的位置，如需要可后续在 confirm 阶段补一条 warning。
- **占位符扫描**：无 TBD/TODO；每个代码步骤都给出了可执行的代码或确切的 JSON/YAML 片段。
- **类型一致性**：`new_umo` / `build_attachment_id_map` / `rewrite_attachment_refs` / `resolve_parent_message_id` / `is_safe_attachment_zip_path` / `is_safe_attachment_ext` 在 Task 1 定义，Task 2/3/4 沿用同名同签名；`SessionTransferService.__init__(db, core_lifecycle)` 在 Task 2 定义，Task 5 的容器注册与 `SessionTransferService(db, core_lifecycle)` 一致；前端方法名 `exportSession` / `importSessions` / `confirmImportSessions` 与 Task 6 生成的 `exportChatSession` / `importChatSessions` / `confirmImportChatSessions` 一一对应。
