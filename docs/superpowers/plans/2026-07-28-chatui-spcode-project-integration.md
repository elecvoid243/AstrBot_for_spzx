# ChatUI 文件夹式项目 × spcode 真实代码项目 整合 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ChatUI 侧边栏的 `workspace_type='project'` 项目能"挂载"一个真实代码目录；项目下任何会话被打开/创建时，前端**静默**调 `POST /spcode/project-load`（不污染聊天历史），并通过头部状态条展示加载状态。

**Architecture:**
- 后端：在 `chatui_projects` 表加 3 个 bool 列（`spcode_auto_load` / `spcode_force` / `spcode_no_codegraph`），CRUD API 自然回传。**不动** spcode 插件本身，复用其 `POST /spcode/project-load` 端点。
- 前端：新增 1 个纯逻辑 composable `useSpcodeProjectAutoLoad`（含 per-(project,umo) inflight mutex + 幂等适配）+ 1 个纯展示组件 `SpcodeProjectStatusChip` + 在 `Chat.vue` 的 `currSessionId` watcher 触发静默 load + 在 `ProjectView` 头部渲染状态条。

**Tech Stack:**
- 后端：Python 3.10+、SQLModel、FastAPI、Pydantic
- 前端：Vue 3 + TypeScript + Pinia + Vuetify + Vitest
- 通信：HTTP JSON（`POST /spcode/project-load`，已存在）

**Spec reference:** `docs/superpowers/specs/2026-07-28-chatui-spcode-project-integration-design.md`

## Global Constraints

- **Conventional Commits**: 提交信息用 `feat:` / `fix:` / `chore:` / `test:` / `docs:` 格式（中文/英文均可，推荐英文）
- **Python 代码风格**: 遵循 `AGENTS.md`，所有公共函数有 Google-style docstring（`Args:` / `Returns:` / `Raises:`），`ruff format .` + `ruff check .` 通过
- **TypeScript 代码风格**: Prettier 默认，组件 `<script setup lang="ts">`，组合式 API
- **路径处理**: 用 `pathlib.Path`，跨平台兼容（Windows / macOS / Linux）
- **i18n**: 文案键放 `features/chat.json`，`tm('project.spcode.xxx')` 访问
- **测试**: TDD 顺序（先失败测试，再实现）
- **失败优先不阻塞**: 静默 load 任何失败都不阻塞 ChatUI 对话
- **零回归**: 旧项目 `workspace_type=session` 行为不变
- **不推送**: 实施过程不 `git push`、不创建 PR；只本地 commit
- **不重构**: 实施范围严格限定在 spec §4 文件清单内，不重构无关代码
- **后端 schema 增量**: 不引入 alembic；用 SQLModel `metadata.create_all` 增量 + 启动时 `try add_column` 兜底

---

## Task 1: 后端 PO 模型 — `ChatUIProject` 加 3 列

**Files:**
- Modify: `astrbot/core/db/po.py:423-459` (`ChatUIProject` 类)
- Test: 通过 `python -c "from astrbot.core.db.po import ChatUIProject; print(ChatUIProject.__fields__['spcode_auto_load'])"` 验证字段存在

**Interfaces:**
- Consumes: 无
- Produces: `ChatUIProject.spcode_auto_load: bool` / `spcode_force: bool` / `spcode_no_codegraph: bool`（默认分别为 `True` / `False` / `False`）

- [ ] **Step 1: 在 `ChatUIProject` 现有 `workspace_path` 字段后插入 3 个 bool 字段**

打开 `astrbot/core/db/po.py`，定位 `class ChatUIProject(TimestampMixin, SQLModel, table=True):` 块（约 423-459 行）。在 `workspace_path` 字段定义后、`__table_args__` 前插入：

```python
    spcode_auto_load: bool = Field(default=True, nullable=False)
    """若 True,该 project 下的会话被打开/创建时,前端会静默
    POST /spcode/project-load(directory=workspace_path, umo=...)"""
    spcode_force: bool = Field(default=False, nullable=False)
    """静默 load 时若 umo 已加载其他项目,是否强制覆盖。"""
    spcode_no_codegraph: bool = Field(default=False, nullable=False)
    """挂载时跳过 codegraph(只 load AGENTS.md,适合轻量场景)。"""
```

- [ ] **Step 2: 验证字段导入无报错**

```bash
cd F:\github\Astrbot
uv run python -c "from astrbot.core.db.po import ChatUIProject; print(ChatUIProject.__fields__.keys())"
```

Expected output 包含 `spcode_auto_load` / `spcode_force` / `spcode_no_codegraph` 三个 key。

- [ ] **Step 3: 提交**

```bash
git add astrbot/core/db/po.py
git commit -m "feat(db): add spcode integration columns to ChatUIProject"
```

---

## Task 2: 后端 DAL — `create_chatui_project` / `update_chatui_project` 加 3 参数

**Files:**
- Modify: `astrbot/core/db/sqlite.py`（定位 `create_chatui_project` / `update_chatui_project` 函数签名）

**Interfaces:**
- Consumes: 来自 Task 1 的 `ChatUIProject` 字段
- Produces: `create_chatui_project(..., spcode_auto_load=True, spcode_force=False, spcode_no_codegraph=False)` 和 `update_chatui_project(..., spcode_auto_load=None, spcode_force=None, spcode_no_codegraph=None)` 签名

- [ ] **Step 1: 修改 `create_chatui_project` 签名**

定位该函数（grep 关键字：`async def create_chatui_project`）。在现有参数列表末尾添加 3 个参数，给默认值：

```python
async def create_chatui_project(
    creator: str,
    title: str,
    emoji: str = "📁",
    description: str | None = None,
    workspace_type: str = "session",
    workspace_path: str | None = None,
    spcode_auto_load: bool = True,
    spcode_force: bool = False,
    spcode_no_codegraph: bool = False,
) -> ChatUIProject:
    """Create a new ChatUI project.
    ...
    Args:
        spcode_auto_load: Whether to auto-load spcode for sessions in this project.
        spcode_force: Whether to force-overwrite existing spcode state.
        spcode_no_codegraph: Whether to skip codegraph on auto-load.
    """
    # ... 现有实现 ...
    # 在 `ChatUIProject(...)` 实例化处增加 3 个关键字参数:
    project = ChatUIProject(
        creator=creator,
        title=title,
        emoji=emoji,
        description=description,
        workspace_type=workspace_type,
        workspace_path=workspace_path,
        spcode_auto_load=spcode_auto_load,
        spcode_force=spcode_force,
        spcode_no_codegraph=spcode_no_codegraph,
    )
    # ... 余下逻辑保持不变 ...
```

- [ ] **Step 2: 修改 `update_chatui_project` 签名**

定位该函数（grep：`async def update_chatui_project`）。在现有参数末尾添加 3 个 `Optional[bool]` 参数（默认 `None` 表示不动）：

```python
async def update_chatui_project(
    project_id: str,
    title: str | None = None,
    emoji: str | None = None,
    description: str | None = None,
    workspace_type: str | None = None,
    workspace_path: str | None = None,
    spcode_auto_load: bool | None = None,
    spcode_force: bool | None = None,
    spcode_no_codegraph: bool | None = None,
) -> None:
    """Update an existing ChatUI project.

    Args:
        spcode_auto_load: New value or None to leave unchanged.
        spcode_force: New value or None to leave unchanged.
        spcode_no_codegraph: New value or None to leave unchanged.
    """
    # ... 现有查询逻辑 ...
    # 在赋值时仅当参数非 None 才更新:
    if spcode_auto_load is not None:
        project.spcode_auto_load = spcode_auto_load
    if spcode_force is not None:
        project.spcode_force = spcode_force
    if spcode_no_codegraph is not None:
        project.spcode_no_codegraph = spcode_no_codegraph
    # ... 余下逻辑保持不变 ...
```

- [ ] **Step 3: 运行 ruff 格式 + 检查**

```bash
cd F:\github\Astrbot
uv run ruff format astrbot/core/db/
uv run ruff check astrbot/core/db/
```

Expected: no errors.

- [ ] **Step 4: 提交**

```bash
git add astrbot/core/db/sqlite.py
git commit -m "feat(db): thread spcode fields through chatui project CRUD"
```

---

## Task 3: 后端 API Schema — `ChatProjectRequest` 加 3 字段

**Files:**
- Modify: `astrbot/dashboard/schemas.py:93-99` (`ChatProjectRequest` 类)

**Interfaces:**
- Consumes: 无
- Produces: `ChatProjectRequest.spcode_auto_load / spcode_force / spcode_no_codegraph: bool | None` 字段

- [ ] **Step 1: 给 `ChatProjectRequest` 加 3 个 optional 字段**

打开 `astrbot/dashboard/schemas.py`，定位 `class ChatProjectRequest(OpenModel):`（约 93-99 行）。在 `workspace_path` 字段后添加：

```python
class ChatProjectRequest(OpenModel):
    project_id: str | None = None
    title: str | None = None
    emoji: str | None = None
    description: str | None = None
    workspace_type: str | None = None
    workspace_path: str | None = None
    spcode_auto_load: bool | None = None
    spcode_force: bool | None = None
    spcode_no_codegraph: bool | None = None
```

- [ ] **Step 2: 验证导入**

```bash
cd F:\github\Astrbot
uv run python -c "from astrbot.dashboard.schemas import ChatProjectRequest; p = ChatProjectRequest(spcode_force=True); print(p.spcode_force)"
```

Expected output: `True`

- [ ] **Step 3: 提交**

```bash
git add astrbot/dashboard/schemas.py
git commit -m "feat(api): expose spcode fields in ChatProjectRequest"
```

---

## Task 4: 后端 Service — 透传 + workspace_path 强校验 + 序列化

**Files:**
- Modify: `astrbot/dashboard/services/chatui_project_service.py`
  - `create_project` (约 25-43 行)
  - `update_project` (约 63-85 行)
  - `_serialize_project` (约 160-190 行)
  - `_normalize_workspace_config` (约 208-252 行)

**Interfaces:**
- Consumes: `ChatProjectRequest`（来自 Task 3）
- Produces: `_serialize_project` 输出 dict 包含 `spcode_auto_load` / `spcode_force` / `spcode_no_codegraph`

- [ ] **Step 1: 修改 `create_project` 透传 3 个字段**

定位 `create_project`。把函数体改为：

```python
async def create_project(self, username: str, data: object) -> dict:
    payload = self._as_payload(data)
    title = payload.get("title")
    emoji = payload.get("emoji", "📁")
    description = payload.get("description")
    spcode_auto_load = bool(payload.get("spcode_auto_load", True))
    spcode_force = bool(payload.get("spcode_force", False))
    spcode_no_codegraph = bool(payload.get("spcode_no_codegraph", False))
    workspace_type, workspace_path = self._normalize_workspace_config(payload)

    if not title:
        raise ChatUIProjectServiceError("Missing key: title")

    project = await self.db.create_chatui_project(
        creator=username,
        title=title,
        emoji=emoji,
        description=description,
        workspace_type=workspace_type,
        workspace_path=workspace_path,
        spcode_auto_load=spcode_auto_load,
        spcode_force=spcode_force,
        spcode_no_codegraph=spcode_no_codegraph,
    )
    return self._serialize_project(project)
```

- [ ] **Step 2: 修改 `update_project` 透传 3 个字段**

定位 `update_project`。把函数体改为：

```python
async def update_project(self, username: str, data: object) -> None:
    payload = self._as_payload(data)
    project_id = payload.get("project_id")
    if not project_id:
        raise ChatUIProjectServiceError("Missing key: project_id")

    project = await self._get_owned_project(username, project_id)
    workspace_type = None
    workspace_path = None
    if "workspace_type" in payload or "workspace_path" in payload:
        workspace_type, workspace_path = self._normalize_workspace_config(
            payload,
            fallback_type=project.workspace_type,
            fallback_path=project.workspace_path,
        )
    await self.db.update_chatui_project(
        project_id=project_id,
        title=payload.get("title"),
        emoji=payload.get("emoji"),
        description=payload.get("description"),
        workspace_type=workspace_type,
        workspace_path=workspace_path,
        spcode_auto_load=payload.get("spcode_auto_load"),
        spcode_force=payload.get("spcode_force"),
        spcode_no_codegraph=payload.get("spcode_no_codegraph"),
    )
```

- [ ] **Step 3: 修改 `_normalize_workspace_config` — 对 `project` 类型强校验**

定位 `_normalize_workspace_config`（约 208-252 行）。在 `workspace_type` 已经 normalize 后、custom 校验前，**对 `project` 类型也加路径检查**（逻辑与 custom 一致，但不需要 `os.access` 三重检查——`project` 路径由前端在创建时校验，后端只确认存在+是目录）：

```python
@staticmethod
def _normalize_workspace_config(
    payload: dict,
    *,
    fallback_type: str | None = None,
    fallback_path: str | None = None,
) -> tuple[str, str | None]:
    """..."""
    workspace_type = normalize_project_workspace_type(
        payload.get("workspace_type", fallback_type or WORKSPACE_TYPE_SESSION)
    )
    raw_path = payload.get("workspace_path", fallback_path)
    workspace_path = normalize_workspace_path(raw_path)
    if workspace_type == WORKSPACE_TYPE_SESSION:
        workspace_path = None
        return workspace_type, workspace_path
    if workspace_type == WORKSPACE_TYPE_PROJECT:
        # 'project' 类型用于 spcode 集成:必须给绝对路径且目录存在
        if not workspace_path:
            raise ChatUIProjectServiceError("Project workspace requires a path")
        try:
            workspace_root = workspace_path_to_root(workspace_path)
        except ValueError as exc:
            raise ChatUIProjectServiceError(str(exc)) from exc
        if not workspace_root.exists():
            raise ChatUIProjectServiceError("Project workspace path does not exist")
        if not workspace_root.is_dir():
            raise ChatUIProjectServiceError("Project workspace path must be a directory")
        return workspace_type, workspace_path
    # workspace_type == WORKSPACE_TYPE_CUSTOM
    if not workspace_path:
        raise ChatUIProjectServiceError("Custom workspace requires a path")
    try:
        workspace_root = workspace_path_to_root(workspace_path)
    except ValueError as exc:
        raise ChatUIProjectServiceError(str(exc)) from exc
    if not workspace_root.exists():
        raise ChatUIProjectServiceError("Custom workspace path does not exist")
    if not workspace_root.is_dir():
        raise ChatUIProjectServiceError("Custom workspace path must be a directory")
    if not os.access(workspace_root, os.R_OK | os.W_OK | os.X_OK):
        raise ChatUIProjectServiceError(
            "Custom workspace path requires read, write, and enter permissions"
        )
    return workspace_type, workspace_path
```

> **注**: 如果项目里 `WORKSPACE_TYPE_PROJECT` / `WORKSPACE_TYPE_SESSION` / `WORKSPACE_TYPE_CUSTOM` 常量来自其他模块，先 grep 确认它们已存在。如果不存在，定义在文件顶部：
> ```python
> WORKSPACE_TYPE_SESSION = "session"
> WORKSPACE_TYPE_PROJECT = "project"
> WORKSPACE_TYPE_CUSTOM = "custom"
> ```

- [ ] **Step 4: 修改 `_serialize_project` 输出 3 个字段**

定位 `_serialize_project` 的 return dict（约 180-190 行）。在 `resolved_workspace_path` 后追加：

```python
return {
    "project_id": project.project_id,
    "title": project.title,
    "emoji": project.emoji,
    "description": project.description,
    "workspace_type": workspace_type,
    "workspace_path": workspace_path,
    "resolved_workspace_path": resolved_workspace_path,
    "spcode_auto_load": bool(getattr(project, "spcode_auto_load", True)),
    "spcode_force": bool(getattr(project, "spcode_force", False)),
    "spcode_no_codegraph": bool(getattr(project, "spcode_no_codegraph", False)),
    "created_at": to_utc_isoformat(project.created_at),
    "updated_at": to_utc_isoformat(project.updated_at),
}
```

- [ ] **Step 5: ruff 格式 + 检查**

```bash
cd F:\github\Astrbot
uv run ruff format astrbot/dashboard/
uv run ruff check astrbot/dashboard/
```

Expected: no errors.

- [ ] **Step 6: 提交**

```bash
git add astrbot/dashboard/services/chatui_project_service.py
git commit -m "feat(service): thread spcode fields + enforce project path validation"
```

---

## Task 5: 后端单测 — `workspace_type='project'` 路径校验 & 序列化

**Files:**
- Modify: `tests/unit/test_chatui_project_service.py`

**Interfaces:**
- Consumes: `ChatUIProjectService`（Task 4）+ `tmp_path` fixture（pytest）
- Produces: 6 个测试用例通过

- [ ] **Step 1: 在 `test_chatui_project_service.py` 顶部加 import**

```python
from pathlib import Path
```

- [ ] **Step 2: 添加 `test_create_project_type_requires_existing_path`**

```python
def test_create_project_type_requires_existing_path(
    service, mock_db, tmp_path: Path
):
    """workspace_type='project' 必须有 path,且 path 必须存在。"""
    payload = {
        "title": "My Code Project",
        "workspace_type": "project",
        "workspace_path": str(tmp_path / "does-not-exist"),
    }
    with pytest.raises(ChatUIProjectServiceError, match="does not exist"):
        await service.create_project("alice", payload)
```

- [ ] **Step 3: 添加 `test_create_project_type_with_valid_path_succeeds`**

```python
async def test_create_project_type_with_valid_path_succeeds(
    service, mock_db, tmp_path: Path
):
    """合法 path 时创建成功,spcode_auto_load 默认 True。"""
    real_path = tmp_path / "real-repo"
    real_path.mkdir()
    payload = {
        "title": "My Code Project",
        "workspace_type": "project",
        "workspace_path": str(real_path),
    }
    project = await service.create_project("alice", payload)
    assert project["workspace_type"] == "project"
    assert project["workspace_path"] == str(real_path.resolve())
    assert project["spcode_auto_load"] is True
    assert project["spcode_force"] is False
    assert project["spcode_no_codegraph"] is False
```

> **注**: 测试中 service 和 mock_db fixture 应在 conftest.py 中已存在（该项目已有此测试文件）。如果不存在，参见 Step 7。

- [ ] **Step 4: 添加 `test_create_session_type_ignores_workspace_path`**

```python
async def test_create_session_type_ignores_workspace_path(
    service, mock_db
):
    """workspace_type='session' 强制清空 workspace_path(已有行为回归)。"""
    payload = {
        "title": "Plain Folder",
        "workspace_type": "session",
        "workspace_path": "/should/be/cleared",
    }
    project = await service.create_project("alice", payload)
    assert project["workspace_path"] is None
    assert project["workspace_type"] == "session"
```

- [ ] **Step 5: 添加 `test_update_project_passes_spcode_fields`**

```python
async def test_update_project_passes_spcode_fields(
    service, mock_db, tmp_path: Path
):
    """update 时 spcode_force 字段透传到 db.update_chatui_project。"""
    real_path = tmp_path / "real"
    real_path.mkdir()
    # 模拟 db.update_chatui_project 已被 mock_db 记录
    project = await _create_owned_project(service, mock_db, "alice", "P", "project", str(real_path))
    payload = {
        "project_id": project["project_id"],
        "spcode_force": True,
        "spcode_no_codegraph": True,
    }
    await service.update_project("alice", payload)
    mock_db.update_chatui_project.assert_called_once()
    _, kwargs = mock_db.update_chatui_project.call_args
    assert kwargs["spcode_force"] is True
    assert kwargs["spcode_no_codegraph"] is True
    assert kwargs["spcode_auto_load"] is None  # 未传
```

- [ ] **Step 6: 添加 `test_serialize_project_includes_spcode_fields`**

```python
async def test_serialize_project_includes_spcode_fields(
    service, mock_db
):
    """_serialize_project 输出 3 个 spcode 字段。"""
    project = await _create_owned_project(
        service, mock_db, "alice", "P", "session", None,
        spcode_force=True, spcode_no_codegraph=True,
    )
    assert project["spcode_auto_load"] is True
    assert project["spcode_force"] is True
    assert project["spcode_no_codegraph"] is True
```

- [ ] **Step 7: 如果 `conftest.py` 没有 `service` / `mock_db` fixture，添加**

在 `tests/unit/test_chatui_project_service.py` 同目录的 `conftest.py` 加：

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from astrbot.dashboard.services.chatui_project_service import ChatUIProjectService

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.create_chatui_project = AsyncMock()
    db.update_chatui_project = AsyncMock()
    db.delete_chatui_project = AsyncMock()
    db.get_chatui_project_by_id = AsyncMock()
    db.get_chatui_projects_by_creator = AsyncMock(return_value=[])
    db.get_project_sessions = AsyncMock(return_value=[])
    return db

@pytest.fixture
def service(mock_db):
    return ChatUIProjectService(mock_db)
```

并加 helper 函数（如 conftest 已有则跳过）：

```python
from astrbot.core.db.po import ChatUIProject

async def _create_owned_project(
    service, mock_db, username, title, workspace_type, workspace_path,
    **spcode_kwargs,
):
    """Helper: 用 mock 创建一个项目并返回 dict。"""
    from datetime import datetime
    project_obj = MagicMock(spec=ChatUIProject)
    project_obj.project_id = f"p-{title}"
    project_obj.creator = username
    project_obj.title = title
    project_obj.emoji = "📁"
    project_obj.description = None
    project_obj.workspace_type = workspace_type
    project_obj.workspace_path = workspace_path
    project_obj.spcode_auto_load = spcode_kwargs.get("spcode_auto_load", True)
    project_obj.spcode_force = spcode_kwargs.get("spcode_force", False)
    project_obj.spcode_no_codegraph = spcode_kwargs.get("spcode_no_codegraph", False)
    project_obj.created_at = datetime.now()
    project_obj.updated_at = datetime.now()
    mock_db.create_chatui_project.return_value = project_obj
    mock_db.get_chatui_project_by_id.return_value = project_obj
    return await service.create_project(username, {
        "title": title,
        "workspace_type": workspace_type,
        "workspace_path": workspace_path,
        **spcode_kwargs,
    })
```

- [ ] **Step 8: 运行测试**

```bash
cd F:\github\Astrbot
uv run pytest tests/unit/test_chatui_project_service.py -v
```

Expected: 全部 PASS（含新加的 5 个）。

- [ ] **Step 9: 提交**

```bash
git add tests/unit/test_chatui_project_service.py
git commit -m "test: cover spcode fields in project service"
```

---

## Task 6: 数据库迁移（启动时 add_column 兜底）

**Files:**
- Modify: 找到现有的"启动时 schema 演进"代码（grep `metadata.create_all` / `ALTER TABLE` / `add_column`），加入 3 列的 idempotent add

**Interfaces:**
- Consumes: `ChatUIProject` 表已存在/不存在
- Produces: 旧库启动时自动 add 3 列，新库由 SQLModel 自动建

- [ ] **Step 1: 定位 schema 演进入口**

```bash
cd F:\github\Astrbot
uv run grep -rn "metadata.create_all" astrbot/core/ --include="*.py" -l
uv run grep -rn "ALTER TABLE" astrbot/core/ --include="*.py" -l
```

预期命中：`astrbot/core/db/sqlite.py` 或 `astrbot/core/db/__init__.py` 或 `astrbot/core/db/migrate.py`。打开命中文件，定位 schema 初始化函数（一般叫 `init_db` / `migrate` / `ensure_schema`）。

- [ ] **Step 2: 添加 idempotent add_column 调用**

在 schema 初始化流程末尾（`metadata.create_all(engine)` 之后），添加：

```python
# Idempotent column add for chatui_projects (spcode integration, 2026-07-28)
_chatui_project_columns = [
    ("spcode_auto_load", "BOOLEAN NOT NULL DEFAULT 1"),
    ("spcode_force", "BOOLEAN NOT NULL DEFAULT 0"),
    ("spcode_no_codegraph", "BOOLEAN NOT NULL DEFAULT 0"),
]
for col_name, col_def in _chatui_project_columns:
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f"ALTER TABLE chatui_projects ADD COLUMN {col_name} {col_def}"
            )
    except Exception as exc:
        # 列已存在或其他无害错误 → 吞掉
        logger.debug("chatui_projects.%s add skipped: %s", col_name, exc)
```

> **注**: 不同 DB（SQLite vs PostgreSQL）`BOOLEAN` 语法略有差异。如果项目支持多 DB，用 SQLAlchemy 反射检测列存在性，避免硬编码 DDL：
> ```python
> from sqlalchemy import inspect
> inspector = inspect(engine)
> existing_cols = {c["name"] for c in inspector.get_columns("chatui_projects")}
> for col_name, col_def in _chatui_project_columns:
>     if col_name not in existing_cols:
>         with engine.begin() as conn:
>             conn.exec_driver_sql(
>                 f"ALTER TABLE chatui_projects ADD COLUMN {col_name} {col_def}"
>             )
> ```

- [ ] **Step 3: 验证本地启动不报错**

```bash
cd F:\github\Astrbot
uv run python -c "
import asyncio
from astrbot.core.db import init_db
asyncio.run(init_db())
print('init_db OK')
"
```

Expected: `init_db OK`

- [ ] **Step 4: 提交**

```bash
git add <命中文件>
git commit -m "feat(db): migrate chatui_projects spcode columns on startup"
```

---

## Task 7: 重生成前端 API 客户端

**Files:**
- Auto-regenerate: `dashboard/src/api/v1.ts`

**Interfaces:**
- Consumes: 后端 OpenAPI schema（已含新字段）
- Produces: `chatApi.createProject` / `updateProject` 的 payload 类型包含 `spcode_auto_load` 等

- [ ] **Step 1: 重生成 API 客户端**

```bash
cd F:\github\Astrbot\dashboard
pnpm generate:api
```

Expected: 看到 `v1.ts` 被更新；搜索 `spcode_force` 应能找到。

- [ ] **Step 2: 验证类型导出**

```bash
cd F:\github\Astrbot\dashboard
grep -n "spcode_force" src/api/v1.ts
```

Expected: 至少 1 行命中。

- [ ] **Step 3: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/api/v1.ts
git commit -m "chore(api): regenerate v1 client with spcode fields"
```

---

## Task 8: 前端类型扩展 — `Project` interface

**Files:**
- Modify: `dashboard/src/components/chat/ProjectList.vue`（`Project` interface 声明，约 88-100 行）

**Interfaces:**
- Consumes: `v1.ts` 自动生成的类型
- Produces: `Project` interface 包含 `spcode_auto_load` / `spcode_force` / `spcode_no_codegraph`

- [ ] **Step 1: 在 `Project` interface 末尾追加 3 个 optional 字段**

打开 `dashboard/src/components/chat/ProjectList.vue`，定位 `export interface Project {` 块。在末尾的 `updated_at: string;` 之前插入：

```typescript
export interface Project {
  project_id: string;
  title: string;
  emoji?: string;
  description?: string;
  workspace_type?: "session" | "project" | "custom";
  workspace_path?: string | null;
  resolved_workspace_path?: string | null;
  spcode_auto_load?: boolean;
  spcode_force?: boolean;
  spcode_no_codegraph?: boolean;
  created_at: string;
  updated_at: string;
}
```

- [ ] **Step 2: 验证 typecheck**

```bash
cd F:\github\Astrbot\dashboard
pnpm typecheck
```

Expected: no errors.

- [ ] **Step 3: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/ProjectList.vue
git commit -m "feat(web): add spcode fields to Project type"
```

---

## Task 9: 前端 i18n 文案

**Files:**
- Modify: `dashboard/src/i18n/zh-CN/features/chat.json`（或对应 `zh.json` / `en.json`）
- 同目录英文版 `en.json`（如果项目有英文版）

**Interfaces:**
- Consumes: 无
- Produces: `project.spcode.*` 文案键

- [ ] **Step 1: 找到 i18n 文件**

```bash
cd F:\github\Astrbot\dashboard
find src/i18n -name "*.json" -type f
```

打开 `features/chat.json`（或对应 zh-CN/en 版本）。

- [ ] **Step 2: 在 `project` 命名空间下添加 `spcode` 子树**

```json
"project": {
  "title": "项目",
  "create": "创建项目",
  "edit": "编辑项目",
  "name": "项目名",
  "emoji": "图标",
  "description": "描述",
  "loadingSessions": "加载会话中...",
  "noSessions": "该项目下暂无会话",
  "confirmDelete": "确认删除项目 \"{title}\"？",
  "workspace": {
    "project": "代码项目",
    "session": "默认",
    "custom": "自定义目录",
    "type": "工作区类型"
  },
  "spcode": {
    "sectionTitle": "代码项目集成（spcode）",
    "path": "代码项目根目录（绝对路径）",
    "pathHint": "必须是绝对路径,且目录存在。spcode 会在该目录下注入 AGENTS.md 并加载 codegraph。",
    "autoLoad": "打开会话时静默加载",
    "autoLoadHint": "该文件夹下的会话被打开/创建时,会自动调用 /project load(不向聊天框产生消息)。",
    "force": "强制覆盖已加载项目",
    "forceHint": "若当前会话已通过 /project load 加载了其他目录,自动切换。",
    "noCodegraph": "仅加载 AGENTS.md(跳过 codegraph)",
    "noCodegraphHint": "适合轻量场景,加载更快但缺少代码图谱索引。",
    "chipLoaded": "已加载: {directory}",
    "chipLoading": "正在加载...",
    "chipFailed": "加载失败",
    "chipNoSession": "未选择会话",
    "chipDisabled": "spcode 功能未启用",
    "errorPathUnsafe": "项目路径不允许",
    "errorFeatureDisabled": "spcode 功能未启用,已跳过自动加载",
    "errorAlreadyLoaded": "已加载其他项目 {previous},未切换到 {current}",
    "errorForceFailed": "强制切换失败",
    "errorGitError": "项目加载失败,详见状态条",
    "errorNetwork": "spcode 服务无响应",
    "errorGeneric": "项目加载失败: {reason}"
  }
}
```

- [ ] **Step 3: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/i18n/
git commit -m "feat(i18n): add spcode integration strings"
```

---

## Task 10: 前端 Composable — `useSpcodeProjectAutoLoad.ts`（先写测试）

**Files:**
- Create: `dashboard/src/composables/useSpcodeProjectAutoLoad.ts`
- Create: `dashboard/src/composables/__tests__/useSpcodeProjectAutoLoad.spec.ts`

**Interfaces:**
- Consumes: `pluginExtensionApi`（来自 `v1.ts`）
- Produces: `useSpcodeProjectAutoLoad()` → `{ silentLoad({project, umo, signal?}) => Promise<LoadData | null> }` + 抛 `ProjectLoadError`

- [ ] **Step 1: 创建 `ProjectLoadError` 类 + 类型定义（`useSpcodeProjectAutoLoad.ts`）**

新建 `dashboard/src/composables/useSpcodeProjectAutoLoad.ts`：

```typescript
import { ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import type { Project } from "@/components/chat/ProjectList.vue";

export type ProjectLoadReason =
  | "invalid_body"
  | "invalid_param"
  | "feature_disabled"
  | "no_project_loaded"
  | "path_unsafe"
  | "git_error"
  | "network_timeout"
  | "unknown";

export interface ProjectLoadData {
  loaded: boolean;
  directory: string;
  umo: string;
  skipped_substeps: string[];
  substep_messages: string[];
  previous_directory?: string;
  silent_reason?: string;
}

export interface ProjectLoadResponse {
  success: boolean;
  reason: ProjectLoadReason | null;
  elapsed_ms: number;
  data: ProjectLoadData;
}

export class ProjectLoadError extends Error {
  constructor(
    public reason: ProjectLoadReason,
    public data: ProjectLoadData,
  ) {
    super(`Project load failed: ${reason}`);
    this.name = "ProjectLoadError";
  }
}

export interface SilentLoadRequest {
  project: Project;
  umo: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}
```

- [ ] **Step 2: 实现 composable（先空壳，再写测试，最后填实）**

在 `ProjectLoadError` 后追加：

```typescript
const inflight = new Map<string, Promise<ProjectLoadData | null>>();
const DEFAULT_TIMEOUT_MS = 30_000;

function inflightKey(project: Project, umo: string): string {
  return `${project.project_id}::${umo}`;
}

async function postLoad(req: SilentLoadRequest, force: boolean): Promise<ProjectLoadResponse> {
  const controller = new AbortController();
  const timeoutMs = req.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // 链式 AbortSignal
  const externalAbort = () => controller.abort();
  req.signal?.addEventListener("abort", externalAbort);
  try {
    const res = await pluginExtensionApi.post<ProjectLoadResponse>(
      "spcode/project-load",
      {
        body: {
          directory: req.project.workspace_path!,
          umo: req.umo,
          force,
          no_codegraph: req.project.spcode_no_codegraph || undefined,
        },
        signal: controller.signal,
      },
    );
    return res.data;
  } catch (err) {
    if ((err as Error).name === "AbortError" || controller.signal.aborted) {
      throw new ProjectLoadError("network_timeout", {
        loaded: false,
        directory: req.project.workspace_path ?? "",
        umo: req.umo,
        skipped_substeps: [],
        substep_messages: [],
      });
    }
    throw new ProjectLoadError("unknown", {
      loaded: false,
      directory: req.project.workspace_path ?? "",
      umo: req.umo,
      skipped_substeps: [],
      substep_messages: [String(err)],
    });
  } finally {
    clearTimeout(timer);
    req.signal?.removeEventListener("abort", externalAbort);
  }
}

export function useSpcodeProjectAutoLoad() {
  async function silentLoad(req: SilentLoadRequest): Promise<ProjectLoadData | null> {
    const { project, umo } = req;
    if (project.workspace_type !== "project") return null;
    if (project.spcode_auto_load === false) return null;
    if (!project.workspace_path) return null;

    const key = inflightKey(project, umo);
    const existing = inflight.get(key);
    if (existing) return existing;

    const promise = (async (): Promise<ProjectLoadData | null> => {
      let resp = await postLoad(req, project.spcode_force === true);

      if (resp.success) return resp.data;

      // 幂等适配:目录已一致 → 视为成功
      if (
        resp.reason === "no_project_loaded" &&
        resp.data.previous_directory === project.workspace_path
      ) {
        return { ...resp.data, loaded: true };
      }

      // 不一致 + spcode_force=true → 再发一次
      if (
        !resp.success &&
        resp.reason === "no_project_loaded" &&
        project.spcode_force === true
      ) {
        resp = await postLoad(req, true);
        if (resp.success) return resp.data;
      }

      throw new ProjectLoadError(resp.reason ?? "unknown", resp.data);
    })();

    inflight.set(key, promise);
    try {
      return await promise;
    } finally {
      inflight.delete(key);
    }
  }

  return { silentLoad };
}
```

- [ ] **Step 3: 创建测试文件 `useSpcodeProjectAutoLoad.spec.ts`**

新建 `dashboard/src/composables/__tests__/useSpcodeProjectAutoLoad.spec.ts`：

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { useSpcodeProjectAutoLoad, ProjectLoadError } from "../useSpcodeProjectAutoLoad";
import type { Project } from "@/components/chat/ProjectList.vue";

// Mock pluginExtensionApi
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    post: vi.fn(),
  },
}));

import { pluginExtensionApi } from "@/api/v1";

const project: Project = {
  project_id: "p-1",
  title: "T",
  workspace_type: "project",
  workspace_path: "/abs/repo",
  spcode_auto_load: true,
  spcode_force: false,
  spcode_no_codegraph: false,
  created_at: "",
  updated_at: "",
};

function mockPost(response: any) {
  (pluginExtensionApi.post as any).mockResolvedValueOnce({ data: response });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useSpcodeProjectAutoLoad", () => {
  it("FT-1: returns null for workspace_type='session'", async () => {
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const result = await silentLoad({ project: { ...project, workspace_type: "session" }, umo: "u1" });
    expect(result).toBeNull();
    expect(pluginExtensionApi.post).not.toHaveBeenCalled();
  });

  it("FT-2: returns null when spcode_auto_load=false", async () => {
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const result = await silentLoad({ project: { ...project, spcode_auto_load: false }, umo: "u1" });
    expect(result).toBeNull();
  });

  it("FT-3: success returns data and posts once", async () => {
    mockPost({ success: true, reason: null, elapsed_ms: 100, data: { loaded: true, directory: "/abs/repo", umo: "u1", skipped_substeps: [], substep_messages: [] } });
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const data = await silentLoad({ project, umo: "u1" });
    expect(data?.loaded).toBe(true);
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(1);
  });

  it("FT-4: no_project_loaded with matching directory is treated as success", async () => {
    mockPost({ success: false, reason: "no_project_loaded", elapsed_ms: 1, data: { loaded: false, directory: "/abs/repo", umo: "u1", skipped_substeps: [], substep_messages: [], previous_directory: "/abs/repo" } });
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const data = await silentLoad({ project, umo: "u1" });
    expect(data?.loaded).toBe(true);
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(1); // 没重试
  });

  it("FT-5: no_project_loaded with mismatch + spcode_force=true retries", async () => {
    mockPost({ success: false, reason: "no_project_loaded", elapsed_ms: 1, data: { loaded: false, directory: "/abs/repo", umo: "u1", skipped_substeps: [], substep_messages: [], previous_directory: "/old" } });
    mockPost({ success: true, reason: null, elapsed_ms: 100, data: { loaded: true, directory: "/abs/repo", umo: "u1", skipped_substeps: [], substep_messages: [] } });
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const data = await silentLoad({ project: { ...project, spcode_force: true }, umo: "u1" });
    expect(data?.loaded).toBe(true);
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(2);
    expect((pluginExtensionApi.post as any).mock.calls[1][1].body.force).toBe(true);
  });

  it("FT-6: no_project_loaded with mismatch + spcode_force=false throws", async () => {
    mockPost({ success: false, reason: "no_project_loaded", elapsed_ms: 1, data: { loaded: false, directory: "/abs/repo", umo: "u1", skipped_substeps: [], substep_messages: [], previous_directory: "/old" } });
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await expect(silentLoad({ project, umo: "u1" })).rejects.toThrow(ProjectLoadError);
  });

  it("FT-7: git_error throws ProjectLoadError", async () => {
    mockPost({ success: false, reason: "git_error", elapsed_ms: 1, data: { loaded: false, directory: "/abs/repo", umo: "u1", skipped_substeps: [], substep_messages: ["❌ ..."] } });
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await expect(silentLoad({ project, umo: "u1" })).rejects.toMatchObject({ reason: "git_error" });
  });

  it("FT-9: inflight serializes same (project, umo)", async () => {
    let resolveFirst!: (v: any) => void;
    (pluginExtensionApi.post as any).mockImplementationOnce(() => new Promise(r => { resolveFirst = r; }));
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const p1 = silentLoad({ project, umo: "u1" });
    const p2 = silentLoad({ project, umo: "u1" });
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(1);
    resolveFirst({ data: { success: true, reason: null, elapsed_ms: 1, data: { loaded: true, directory: "/abs/repo", umo: "u1", skipped_substeps: [], substep_messages: [] } } });
    const [d1, d2] = await Promise.all([p1, p2]);
    expect(d1).toBe(d2);
  });

  it("FT-10: inflight isolated by project", async () => {
    let resolveFirst!: (v: any) => void;
    (pluginExtensionApi.post as any).mockImplementationOnce(() => new Promise(r => { resolveFirst = r; }));
    mockPost({ success: true, reason: null, elapsed_ms: 1, data: { loaded: true, directory: "/b", umo: "u1", skipped_substeps: [], substep_messages: [] } });
    const { silentLoad } = useSpcodeProjectAutoLoad();
    const p1 = silentLoad({ project, umo: "u1" });
    const p2 = silentLoad({ project: { ...project, project_id: "p-2" }, umo: "u1" });
    expect(pluginExtensionApi.post).toHaveBeenCalledTimes(2);
    resolveFirst({ data: { success: true, reason: null, elapsed_ms: 1, data: { loaded: true, directory: "/a", umo: "u1", skipped_substeps: [], substep_messages: [] } } });
    await Promise.all([p1, p2]);
  });

  it("FT-11: timeout throws ProjectLoadError('network_timeout')", async () => {
    (pluginExtensionApi.post as any).mockImplementationOnce(({ signal }: any) => new Promise((_, reject) => {
      signal.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    }));
    const { silentLoad } = useSpcodeProjectAutoLoad();
    await expect(silentLoad({ project, umo: "u1", timeoutMs: 50 })).rejects.toMatchObject({ reason: "network_timeout" });
  });
});
```

- [ ] **Step 4: 运行测试**

```bash
cd F:\github\Astrbot\dashboard
pnpm vitest run src/composables/__tests__/useSpcodeProjectAutoLoad.spec.ts
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/composables/useSpcodeProjectAutoLoad.ts dashboard/src/composables/__tests__/useSpcodeProjectAutoLoad.spec.ts
git commit -m "feat(web): add useSpcodeProjectAutoLoad composable"
```

---

## Task 11: 前端组件 — `SpcodeProjectStatusChip.vue`（先写测试）

**Files:**
- Create: `dashboard/src/components/chat/SpcodeProjectStatusChip.vue`

**Interfaces:**
- Props: `umo: string | null`、`workspacePath: string`
- Emits: 无（只读展示）

- [ ] **Step 1: 创建组件文件**

新建 `dashboard/src/components/chat/SpcodeProjectStatusChip.vue`：

```vue
<template>
  <div class="spcode-chip" :class="stateClass" :title="tooltipText">
    <component :is="stateIcon" :size="14" class="spcode-chip-icon" />
    <span class="spcode-chip-text">{{ stateText }}</span>
    <v-menu v-if="canShowDetails" v-model="popoverOpen" location="bottom start">
      <template #activator="{ props: menuProps }">
        <button v-bind="menuProps" class="spcode-chip-details-btn" type="button" @click.stop>
          <ChevronDown :size="14" />
        </button>
      </template>
      <v-card class="spcode-chip-popover" min-width="320" max-width="480">
        <v-card-text>
          <div class="spcode-chip-popover-title">{{ tm("project.spcode.chipFailed") }}</div>
          <pre class="spcode-chip-popover-messages">{{ detailText }}</pre>
        </v-card-text>
      </v-card>
    </v-menu>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { CheckCircle2, ChevronDown, Loader2, XCircle, AlertCircle, HelpCircle } from "@lucide/vue";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  umo: string | null;
  workspacePath: string;
}>();

const { tm } = useModuleI18n("features/chat");
const { status, refresh } = useSpcodeProjectStatus();
const popoverOpen = ref(false);

// 该 chip 仅展示当前 umo 的状态;spcodeStatus 是模块级 singleton,
// 如果 umo 不匹配则视为"非本 chip 关心"
const isThisUmo = computed(() => status.value.umo === props.umo);
const state = computed(() => {
  if (!props.umo) return "no_session";
  if (!isThisUmo.value) return "unknown";
  if (status.value.loaded) {
    if (status.value.directory !== props.workspacePath) return "mismatch";
    return "loaded";
  }
  return "unloaded";
});

const stateClass = computed(() => `is-${state.value}`);
const stateIcon = computed(() => {
  switch (state.value) {
    case "loaded": return CheckCircle2;
    case "mismatch": return AlertCircle;
    case "no_session": return HelpCircle;
    case "unloaded": return Loader2;
    default: return XCircle;
  }
});
const stateText = computed(() => {
  switch (state.value) {
    case "loaded": return tm("project.spcode.chipLoaded", { directory: status.value.directory });
    case "mismatch": return tm("project.spcode.chipFailed");
    case "no_session": return tm("project.spcode.chipNoSession");
    case "unloaded": return tm("project.spcode.chipLoading");
    default: return tm("project.spcode.chipFailed");
  }
});
const tooltipText = computed(() => stateText.value);
const canShowDetails = computed(() => state.value === "loaded" || state.value === "mismatch");
const detailText = computed(() => {
  if (state.value === "loaded") return `${tm("project.spcode.chipLoaded", { directory: status.value.directory })}\n${status.value.directory}`;
  if (state.value === "mismatch") return `Expected: ${props.workspacePath}\nLoaded: ${status.value.directory}`;
  return "";
});
</script>

<style scoped>
.spcode-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
  background: rgba(var(--v-theme-on-surface), 0.04);
}
.spcode-chip-icon { flex: 0 0 auto; }
.spcode-chip-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 280px;
}
.is-loaded { color: rgb(var(--v-theme-success)); }
.is-mismatch { color: rgb(var(--v-theme-warning)); }
.is-no_session, .is-unknown, .is-unloaded { color: rgba(var(--v-theme-on-surface), 0.52); }
.spcode-chip-details-btn {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  padding: 0;
}
.spcode-chip-popover-messages {
  margin: 0;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  max-height: 240px;
  overflow-y: auto;
}
.spcode-chip-popover-title {
  font-weight: 600;
  margin-bottom: 8px;
}
</style>
```

- [ ] **Step 2: typecheck**

```bash
cd F:\github\Astrbot\dashboard
pnpm typecheck
```

Expected: no errors.

- [ ] **Step 3: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/SpcodeProjectStatusChip.vue
git commit -m "feat(web): add SpcodeProjectStatusChip component"
```

---

## Task 12: 前端 — `ProjectView.vue` 插入状态条

**Files:**
- Modify: `dashboard/src/components/chat/ProjectView.vue`（`<section class="project-header">` 块内）

- [ ] **Step 1: import 新组件**

打开 `ProjectView.vue`，在 `<script setup>` 顶部 import 处添加：

```typescript
import SpcodeProjectStatusChip from "@/components/chat/SpcodeProjectStatusChip.vue";
```

同时让组件接收 `umo` prop（如果还没有）：

找到 `defineProps<{...}>(...)`，在 `sessions: Session[]` 旁加：

```typescript
interface Props {
  project?: Project | null;
  sessions: Session[];
  umo?: string | null;
}
const props = withDefaults(defineProps<Props>(), { umo: null });
```

- [ ] **Step 2: 在 `project-workspace-summary` 后插入状态条**

定位 `<div v-if="workspaceSummary" class="project-workspace-summary" ...>`。在其后插入：

```vue
<SpcodeProjectStatusChip
  v-if="project?.workspace_type === 'project' && project.workspace_path"
  :umo="props.umo ?? null"
  :workspace-path="project.workspace_path"
/>
```

- [ ] **Step 3: typecheck**

```bash
cd F:\github\Astrbot\dashboard
pnpm typecheck
```

Expected: no errors.

- [ ] **Step 4: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/ProjectView.vue
git commit -m "feat(web): render spcode status chip in ProjectView"
```

---

## Task 13: 前端 — `ProjectDialog.vue` 添加挂载代码目录 UI

**Files:**
- Modify: `dashboard/src/components/chat/ProjectDialog.vue`（template 末尾 + script form state）

- [ ] **Step 1: import `useI18n` 已经存在；扩展 form state**

打开 `ProjectDialog.vue`，定位：

```typescript
const form = ref<ProjectFormData>({
    emoji: '📁',
    title: '',
    description: '',
    workspace_type: 'project',
    workspace_path: ''
});
```

替换为：

```typescript
const form = ref<ProjectFormData>({
    emoji: '📁',
    title: '',
    description: '',
    workspace_type: 'project',
    workspace_path: '',
    spcode_auto_load: true,
    spcode_force: false,
    spcode_no_codegraph: false,
});
```

- [ ] **Step 2: 扩展 `ProjectFormData` interface**

定位 `export interface ProjectFormData { ... }`（约 17-23 行），追加 3 个 optional 字段：

```typescript
export interface ProjectFormData {
    emoji: string;
    title: string;
    description: string;
    workspace_type: WorkspaceType;
    workspace_path: string;
    spcode_auto_load?: boolean;
    spcode_force?: boolean;
    spcode_no_codegraph?: boolean;
}
```

- [ ] **Step 3: 在 watch 内同步新字段**

定位 `watch(() => props.project, ...)` 或 `watch(() => props.modelValue, ...)`（约 60-85 行）。在 `form.value = { ... }` 块内增加 3 个字段读取（从 `props.project`）：

```typescript
if (props.project) {
    isEditing.value = true;
    form.value = {
        emoji: props.project.emoji || '📁',
        title: props.project.title,
        description: props.project.description || '',
        workspace_type: props.project.workspace_type || 'session',
        workspace_path: props.project.workspace_path || '',
        spcode_auto_load: props.project.spcode_auto_load !== false,
        spcode_force: props.project.spcode_force === true,
        spcode_no_codegraph: props.project.spcode_no_codegraph === true,
    };
}
```

并在 `else` 分支（创建模式）：

```typescript
} else {
    isEditing.value = false;
    form.value = {
        emoji: '📁',
        title: '',
        description: '',
        workspace_type: 'project',
        workspace_path: '',
        spcode_auto_load: true,
        spcode_force: false,
        spcode_no_codegraph: false,
    };
}
```

- [ ] **Step 4: 在 template 中,workspace_type select 之后插入新 UI 区**

定位 template 中：

```html
<v-select v-model="form.workspace_type" :items="workspaceTypeItems" item-title="label" item-value="value"
    :label="tm('project.workspace.type')" variant="outlined" hide-details class="mb-3" />
<v-text-field v-if="form.workspace_type === 'custom'" v-model="form.workspace_path"
    :label="tm('project.workspace.path')" variant="outlined" hide-details class="mb-1" />
```

替换为：

```html
<v-select v-model="form.workspace_type" :items="workspaceTypeItems" item-title="label" item-value="value"
    :label="tm('project.workspace.type')" variant="outlined" hide-details class="mb-3" />
<v-text-field v-if="form.workspace_type !== 'session'" v-model="form.workspace_path"
    :label="tm('project.workspace.path')" variant="outlined" hide-details class="mb-1" persistent-hint
    :hint="tm('project.spcode.pathHint')" />

<v-divider v-if="form.workspace_type === 'project'" class="my-4" />
<div v-if="form.workspace_type === 'project'" class="spcode-section">
  <div class="spcode-section-title">{{ tm('project.spcode.sectionTitle') }}</div>
  <v-switch v-model="form.spcode_auto_load" :label="tm('project.spcode.autoLoad')" color="primary" density="comfortable" hide-details class="mb-2" />
  <div class="spcode-section-hint">{{ tm('project.spcode.autoLoadHint') }}</div>
  <v-switch v-model="form.spcode_force" :label="tm('project.spcode.force')" color="primary" density="comfortable" hide-details class="mb-2 mt-3" />
  <div class="spcode-section-hint">{{ tm('project.spcode.forceHint') }}</div>
  <v-switch v-model="form.spcode_no_codegraph" :label="tm('project.spcode.noCodegraph')" color="primary" density="comfortable" hide-details class="mb-2 mt-3" />
  <div class="spcode-section-hint">{{ tm('project.spcode.noCodegraphHint') }}</div>
</div>
```

- [ ] **Step 5: 更新 canSave 校验**

定位 `const canSave = computed(() => { ... })`。把 function 改为：

```typescript
const canSave = computed(() => {
    if (!form.value.title.trim()) return false;
    if (form.value.workspace_type === 'session') return true;
    // project / custom 都要求路径非空
    return form.value.workspace_path.trim().length > 0;
});
```

- [ ] **Step 6: typecheck**

```bash
cd F:\github\Astrbot\dashboard
pnpm typecheck
```

Expected: no errors.

- [ ] **Step 7: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/ProjectDialog.vue
git commit -m "feat(web): add spcode integration fields to ProjectDialog"
```

---

## Task 14: 前端 — `Chat.vue` 接入 `currSessionId` watcher

**Files:**
- Modify: `dashboard/src/components/chat/Chat.vue`

**Interfaces:**
- Consumes: `useSpcodeProjectAutoLoad`（Task 10）
- Produces: `watch(currSessionId, ...)` 触发静默 load

- [ ] **Step 1: import composable**

在 `Chat.vue` 顶部 import 区（已 import `useSpcodeProjectStatus` 处）加：

```typescript
import { useSpcodeProjectAutoLoad } from "@/composables/useSpcodeProjectAutoLoad";
```

- [ ] **Step 2: 初始化 silentLoad**

定位 `const spcodeStatus = useSpcodeProjectStatus();` 那一带。在其下方加：

```typescript
const { silentLoad } = useSpcodeProjectAutoLoad();
const toast = useToast();  // 已有就跳过
```

- [ ] **Step 3: 抽取 helper 函数（放在 `function resolveCurrentUmo` 附近）**

```typescript
async function tryAutoLoadSpcodeForSession(umo: string): Promise<void> {
  const project = selectedProject.value;
  if (!project) return;
  if (project.workspace_type !== 'project') return;
  if (project.spcode_auto_load === false) return;
  if (!project.workspace_path) return;
  try {
    const data = await silentLoad({ project, umo });
    if (data?.loaded) {
      await spcodeStatus.refresh(umo);
    }
  } catch (err) {
    if (err instanceof ProjectLoadError) {
      const reason = err.reason;
      let msg = tm('project.spcode.errorGeneric', { reason });
      if (reason === 'feature_disabled') msg = tm('project.spcode.errorFeatureDisabled');
      else if (reason === 'no_project_loaded') msg = tm('project.spcode.errorAlreadyLoaded', { previous: err.data.previous_directory ?? '?', current: err.data.directory });
      else if (reason === 'path_unsafe') msg = tm('project.spcode.errorPathUnsafe');
      else if (reason === 'git_error') msg = tm('project.spcode.errorGitError');
      else if (reason === 'network_timeout') msg = tm('project.spcode.errorNetwork');
      toast.error(msg);
    } else {
      console.warn('[spcode auto-load] unexpected error:', err);
      toast.error(tm('project.spcode.errorNetwork'));
    }
  }
}
```

并在 import 处加 `ProjectLoadError`：

```typescript
import { ProjectLoadError, useSpcodeProjectAutoLoad } from "@/composables/useSpcodeProjectAutoLoad";
```

- [ ] **Step 4: 新增 watch block（紧贴已有 `watch(currSessionId, async (next) => {...})` 之后）**

```typescript
// spcode auto-load:每次切换/创建会话,若当前项目是 'project' 类型则静默 load
watch(
  currSessionId,
  async (next) => {
    if (!next) return;
    const umo = resolveCurrentUmo(next);
    if (!umo) return;
    void tryAutoLoadSpcodeForSession(umo);
  },
);
```

- [ ] **Step 5: typecheck + lint**

```bash
cd F:\github\Astrbot\dashboard
pnpm typecheck
pnpm lint
```

Expected: no errors.

- [ ] **Step 6: 提交**

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/Chat.vue
git commit -m "feat(web): trigger silent spcode load on session switch"
```

---

## Task 15: 端到端冒烟自测（人工 + Playwright 可选）

**Files:** 无代码改动；用 AstrBot 启动命令 + dashboard 浏览器验证

- [ ] **Step 1: 启动后端**

```bash
cd F:\github\Astrbot
uv run main.py
```

Expected: 服务在 `http://localhost:6185` 启动；log 无异常。

- [ ] **Step 2: 启动 dashboard**

```bash
cd F:\github\Astrbot\dashboard
pnpm dev
```

Expected: dashboard 在 `http://localhost:3000` 启动。

- [ ] **Step 3: E2E-1 创建 project 类型项目**

浏览器打开 dashboard → ChatUI 侧边栏 → 点项目 "+" → 填表单：
- name: `E2E Test Project`
- workspace_type: `代码项目`
- workspace_path: `<任意空目录的绝对路径>`（例如 `C:\tmp\e2e-repo`）
- 3 个 v-switch 保持默认

点保存。Expected: ProjectList 出现该项目；GET `/api/v1/chat/projects` 返回的 dict 包含 `spcode_auto_load: true`。

- [ ] **Step 4: E2E-2 进入该项目 → 点子会话**

点击该项目行 → 展开子会话列表（空） → 在 ChatInput 发一条消息（"hi"）→ 等响应。

Expected:
- 顶部 spcode chip 出现"已加载: `<path>`"（≤2s）
- ProjectView 头部状态条变绿
- 聊天流无 ⏳/❌/✅ 消息（**关键**）

- [ ] **Step 5: E2E-4 session 类型项目零回归**

新建一个 `workspace_type=session` 项目, 同样创建子会话、发消息。

Expected: 状态条**不**出现（或显示"未选择会话"灰色），spcode chip 行为与改造前一致。

- [ ] **Step 6: E2E-3 spcode 未启用降级**

如果可以临时禁用 spcode 插件（管理后台 toggle off），重复 E2E-4。

Expected: 状态条变红 + popover 显示 `feature_disabled`；toast 出现"spcode 功能未启用"；聊天流不阻塞。

- [ ] **Step 7: ruff / format / lint 全量**

```bash
cd F:\github\Astrbot
uv run ruff format .
uv run ruff check .
cd F:\github\Astrbot\dashboard
pnpm typecheck
pnpm lint
```

Expected: 全过。

- [ ] **Step 8: 提交任何修复（如有）**

如有 E2E 修复，按 conventional commits 单独 commit。

---

## Self-Review（已在本计划生成时执行）

**1. Spec coverage（spec 覆盖度）**：
- §1 背景与目标 → Task 1-15 全部服务于该目标 ✓
- §3 架构（单 watcher 唯一触发器）→ Task 14 ✓
- §4 文件清单 10+2 → Task 1-13 逐一对应 ✓
- §5 数据模型 3 bool 列 → Task 1-2 ✓
- §6 数据流（端到端时序）→ Task 10 + Task 14 ✓
- §6.3 inflight Map 串行化 → Task 10 (Step 2) ✓
- §7 错误处理 E2 分级 → Task 14 (Step 3) ✓
- §7.2 Q1-Q10 边界 → Task 11 (Q1 umo=null), Task 10 (Q7 inflight), Task 12 (Q9 chip) ✓
- §7.3 30s timeout → Task 10 (Step 2) ✓
- §8 测试计划 → Task 5 (后端), Task 10 (前端) ✓
- §10 风险（启动 add_column）→ Task 6 ✓

**2. Placeholder scan**：
- grep 结果：无 "TBD" / "TODO" / "fill in details" / "add appropriate error handling" 出现
- 每个 Step 都有可执行代码或命令

**3. Type consistency**：
- `Project.spcode_auto_load` / `spcode_force` / `spcode_no_codegraph` 在 Task 1（PO）、Task 2（DAL）、Task 3（API）、Task 4（service）、Task 8（前端 type）一致
- `useSpcodeProjectAutoLoad().silentLoad({project, umo})` 签名在 Task 10（实现）、Task 14（Chat.vue 调用）一致
- `ProjectLoadError` 类在 Task 10 定义、Task 14 引用一致
- `SpcodeProjectStatusChip` props `{umo, workspacePath}` 在 Task 11 定义、Task 12 调用一致
- i18n 键 `project.spcode.*` 在 Task 9 定义、Task 11/13/14 引用一致

无类型/方法名不一致。

---

## 计划完成

**已写入**: `docs/superpowers/plans/2026-07-28-chatui-spcode-project-integration.md`

**15 个任务、~10+2 个文件改动、约 +800 行代码 + ~250 行测试**。
