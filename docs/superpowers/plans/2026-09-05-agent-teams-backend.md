# Agent Teams Backend (Plan 1/3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete Agent Teams backend: team/member/workflow CRUD with auto session creation, the DAG manual-orchestration engine over TeamPorts, runs API + SSE streaming, and Tier-1 hot resume.

**Architecture:** Teams live in the dashboard service layer and drive the existing WebChat pipeline via synthetic turn injection (same seam as Agent Collab). Members are ordinary independent sessions; the only core change in this plan is a 3-line extra passthrough in the webchat adapter. Pure DAG algorithms are split into `agent_team_dag.py` for unit testability; IO lives in `DAGRunner`/`AgentTeamRunService` over an injected `TeamPorts` seam.

**Tech Stack:** Python 3.10+, FastAPI, SQLModel/aiosqlite, asyncio, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-05-agent-teams-design.md` (§5 data model, §6.1-6.3/6.5-6.6 backend, §7 API; §6.4 auto-orchestration and the frontend are Plans 2/3, NOT this plan)

## Global Constraints

- Python 3.10+ compatible; consider cross-platform (Windows/macOS/Linux) file handling.
- Use English for all code comments, logs, and identifiers. Google-style docstrings (`Args:`/`Returns:`/`Raises:`) on public functions/methods.
- Conventional commits (`feat:`, `fix:`, `test:`, `chore:`).
- Run `ruff format .` and `ruff check .` before every commit.
- Limits (module constants): members ≤ 10 per team, nodes ≤ 20 per workflow, `max_parallel` ≤ 5, `max_rounds` ≤ 20, `reply_timeout` default 600.0s, `inject_max_length` default 4000, `busy_poll_interval` 2.0s.
- Members are WebChat sessions only, created via `ChatService.new_session` (no binding of pre-existing sessions). `session_id` globally unique across teams.
- Run statuses: `running | paused | completed | stopped | failed | interrupted`; node statuses: `pending | running | done | failed | skipped`.
- Node states are persisted on EVERY transition (`update_agent_team_run`), not only at run end.
- One active run (`running`/`paused`) per team; starting another raises a service error containing "active run" (the API layer maps it to HTTP 409).
- Tests: `pytest tests/agent_teams -v`; DB tests use `SQLiteDatabase(str(tmp_path / "t.db"))` + `await db.initialize()`.

---

### Task 1: PO tables (`agent_teams`, `agent_team_workflows`, `agent_team_runs`)

**Files:**
- Modify: `astrbot/core/db/po.py` (append after the `CronJob` class)
- Test: `tests/agent_teams/test_agent_team_tables.py`

**Interfaces:**
- Produces: SQLModel classes `AgentTeam`, `AgentTeamWorkflow`, `AgentTeamRun` (fields per spec §5). Later tasks import them from `astrbot.core.db.po`.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_tables.py
"""Schema tests for the agent-team PO tables."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from astrbot.core.db.po import AgentTeam, AgentTeamRun, AgentTeamWorkflow


@pytest.mark.asyncio
async def test_tables_created_and_roundtrip(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    assert {"agent_teams", "agent_team_workflows", "agent_team_runs"} <= set(
        SQLModel.metadata.tables.keys()
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            AgentTeam(
                team_id="t1",
                owner_username="alice",
                name="team",
                coordinator_member_id="m1",
                members=[{"member_id": "m1", "name": "lead", "session_id": "c1"}],
                config={"failure_policy": "pause"},
            )
        )
        session.add(
            AgentTeamRun(
                run_id="r1",
                team_id="t1",
                workflow_id=None,
                mode="dag",
                input="do it",
                status="running",
                graph_snapshot={"nodes": [], "edges": []},
                node_states={},
                rounds=[],
            )
        )
        await session.commit()
    async with maker() as session:
        loaded = (
            await session.execute(
                select(AgentTeamRun).where(AgentTeamRun.run_id == "r1")
            )
        ).scalars().one()
        assert loaded.mode == "dag"
        assert loaded.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_tables.py -v`
Expected: FAIL — `ImportError: cannot import name 'AgentTeam'`

- [ ] **Step 3: Implement — append to `astrbot/core/db/po.py` (after `CronJob`)**

```python
class AgentTeam(TimestampMixin, SQLModel, table=True):
    """An Agent Teams team: named members bound to WebChat sessions."""

    __tablename__: str = "agent_teams"

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    team_id: str = Field(max_length=32, nullable=False, unique=True)
    owner_username: str = Field(max_length=64, nullable=False)
    name: str = Field(max_length=64, nullable=False)
    coordinator_member_id: str = Field(max_length=32, nullable=False)
    # [{member_id, name, session_id, umo, persona_id, provider_id, system_prompt}]
    members: list = Field(default_factory=list, sa_type=JSON)
    # {failure_policy, reply_timeout, max_rounds, max_parallel, inject_max_length}
    config: dict = Field(default_factory=dict, sa_type=JSON)


class AgentTeamWorkflow(TimestampMixin, SQLModel, table=True):
    """A saved manual-orchestration DAG template bound to one team."""

    __tablename__: str = "agent_team_workflows"

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    workflow_id: str = Field(max_length=32, nullable=False, unique=True)
    team_id: str = Field(max_length=32, nullable=False, index=True)
    name: str = Field(max_length=64, nullable=False)
    # {nodes: [{id, member_id, task, title?}], edges: [{from, to}]}
    graph: dict = Field(default_factory=dict, sa_type=JSON)
    # {node_id: {x, y}} editor canvas positions
    layout: dict = Field(default_factory=dict, sa_type=JSON)


class AgentTeamRun(TimestampMixin, SQLModel, table=True):
    """One execution of a team (auto or DAG mode), persisted per transition."""

    __tablename__: str = "agent_team_runs"

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    run_id: str = Field(max_length=32, nullable=False, unique=True)
    team_id: str = Field(max_length=32, nullable=False, index=True)
    workflow_id: str | None = Field(default=None, max_length=32)
    mode: str = Field(max_length=8, nullable=False)  # auto | dag
    input: str = Field(sa_type=Text, nullable=False, default="")
    status: str = Field(max_length=16, nullable=False, default="running", index=True)
    result_summary: str = Field(sa_type=Text, nullable=False, default="")
    graph_snapshot: dict = Field(default_factory=dict, sa_type=JSON)
    # {node_id: {status, member_id, task_rendered, result, error, started_at,
    #            finished_at}}; node status: pending|running|done|failed|skipped
    node_states: dict = Field(default_factory=dict, sa_type=JSON)
    # auto mode only (Plan 3): [{n, assignments, results_digest}]
    rounds: list = Field(default_factory=list, sa_type=JSON)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent_teams/test_agent_team_tables.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/core/db/po.py tests/agent_teams/
ruff check astrbot/core/db/po.py tests/agent_teams/
git add astrbot/core/db/po.py tests/agent_teams/
git commit -m "feat: add agent teams PO tables"
```

---

### Task 2: DB repository methods

**Files:**
- Modify: `astrbot/core/db/__init__.py` (abstract declarations on `BaseDatabase`)
- Modify: `astrbot/core/db/sqlite.py` (implementations on `SQLiteDatabase`)
- Test: `tests/agent_teams/test_agent_team_repo.py`

**Interfaces:**
- Consumes: PO classes from Task 1; `_run_in_tx` / `_apply_updates` (existing, `sqlite.py:1742`/`1753`).
- Produces (declared abstract on `BaseDatabase`, implemented on `SQLiteDatabase`):
  - `async create_agent_team(*, team_id: str, owner_username: str, name: str, coordinator_member_id: str, members: list, config: dict) -> AgentTeam`
  - `async get_agent_team(team_id: str) -> AgentTeam | None`
  - `async get_agent_teams_by_owner(owner_username: str) -> list[AgentTeam]`
  - `async update_agent_team(team_id: str, **updates) -> None` (skips `None` values via `_apply_updates`)
  - `async delete_agent_team(team_id: str) -> None`
  - Same five for workflows: `create_agent_team_workflow(*, workflow_id, team_id, name, graph, layout)`, `get_agent_team_workflow`, `get_agent_team_workflows_by_team`, `update_agent_team_workflow`, `delete_agent_team_workflow`
  - Runs: `create_agent_team_run(*, run_id, team_id, workflow_id, mode, input, status, graph_snapshot, node_states, rounds) -> AgentTeamRun`, `get_agent_team_run(run_id)`, `get_agent_team_runs_by_team(team_id)`, `get_active_agent_team_run(team_id) -> AgentTeamRun | None` (status in `("running", "paused")`, latest by `id`), `update_agent_team_run(run_id, **updates)`, `get_agent_team_runs_by_status(statuses: list[str]) -> list[AgentTeamRun]`

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_repo.py
"""Repo-method tests against a real on-disk SQLite database."""

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_team_crud_roundtrip(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()

    await db.create_agent_team(
        team_id="t1",
        owner_username="alice",
        name="team",
        coordinator_member_id="m1",
        members=[{"member_id": "m1", "name": "lead", "session_id": "c1"}],
        config={},
    )
    team = await db.get_agent_team("t1")
    assert team is not None and team.owner_username == "alice"
    assert len(await db.get_agent_teams_by_owner("alice")) == 1
    assert await db.get_agent_teams_by_owner("bob") == []

    await db.update_agent_team(
        "t1", name="renamed", config={"failure_policy": "auto_skip"}
    )
    team = await db.get_agent_team("t1")
    assert team.name == "renamed"
    assert team.config == {"failure_policy": "auto_skip"}
    # None values must not overwrite
    await db.update_agent_team("t1", name=None)
    assert (await db.get_agent_team("t1")).name == "renamed"

    await db.delete_agent_team("t1")
    assert await db.get_agent_team("t1") is None


@pytest.mark.asyncio
async def test_run_lifecycle_queries(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    for run_id, status in (("r1", "completed"), ("r2", "running")):
        await db.create_agent_team_run(
            run_id=run_id, team_id="t1", workflow_id=None, mode="dag",
            input="x", status=status, graph_snapshot={}, node_states={}, rounds=[],
        )
    active = await db.get_active_agent_team_run("t1")
    assert active is not None and active.run_id == "r2"

    await db.update_agent_team_run(
        "r2", status="paused", node_states={"n1": {"status": "running"}}
    )
    active = await db.get_active_agent_team_run("t1")
    assert active is not None and active.status == "paused"
    assert (
        len(await db.get_agent_team_runs_by_status(["running", "paused", "interrupted"]))
        == 2
    )
    runs = await db.get_agent_team_runs_by_team("t1")
    assert {r.run_id for r in runs} == {"r1", "r2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_repo.py -v`
Expected: FAIL — `AttributeError: 'SQLiteDatabase' object has no attribute 'create_agent_team'`

- [ ] **Step 3: Implement abstract declarations in `astrbot/core/db/__init__.py`**

Add to `BaseDatabase` (follow the existing `create_chatui_project` declaration style around line 962):

```python
    @abc.abstractmethod
    async def create_agent_team(
        self, *, team_id: str, owner_username: str, name: str,
        coordinator_member_id: str, members: list, config: dict,
    ) -> "AgentTeam": ...

    @abc.abstractmethod
    async def get_agent_team(self, team_id: str) -> "AgentTeam | None": ...

    @abc.abstractmethod
    async def get_agent_teams_by_owner(self, owner_username: str) -> list["AgentTeam"]: ...

    @abc.abstractmethod
    async def update_agent_team(self, team_id: str, **updates) -> None: ...

    @abc.abstractmethod
    async def delete_agent_team(self, team_id: str) -> None: ...

    @abc.abstractmethod
    async def create_agent_team_workflow(
        self, *, workflow_id: str, team_id: str, name: str, graph: dict, layout: dict,
    ) -> "AgentTeamWorkflow": ...

    @abc.abstractmethod
    async def get_agent_team_workflow(self, workflow_id: str) -> "AgentTeamWorkflow | None": ...

    @abc.abstractmethod
    async def get_agent_team_workflows_by_team(self, team_id: str) -> list["AgentTeamWorkflow"]: ...

    @abc.abstractmethod
    async def update_agent_team_workflow(self, workflow_id: str, **updates) -> None: ...

    @abc.abstractmethod
    async def delete_agent_team_workflow(self, workflow_id: str) -> None: ...

    @abc.abstractmethod
    async def create_agent_team_run(
        self, *, run_id: str, team_id: str, workflow_id: str | None, mode: str,
        input: str, status: str, graph_snapshot: dict, node_states: dict, rounds: list,
    ) -> "AgentTeamRun": ...

    @abc.abstractmethod
    async def get_agent_team_run(self, run_id: str) -> "AgentTeamRun | None": ...

    @abc.abstractmethod
    async def get_agent_team_runs_by_team(self, team_id: str) -> list["AgentTeamRun"]: ...

    @abc.abstractmethod
    async def get_active_agent_team_run(self, team_id: str) -> "AgentTeamRun | None": ...

    @abc.abstractmethod
    async def update_agent_team_run(self, run_id: str, **updates) -> None: ...

    @abc.abstractmethod
    async def get_agent_team_runs_by_status(self, statuses: list[str]) -> list["AgentTeamRun"]: ...
```

Import the PO classes in `__init__.py` following the file's existing po import style.

- [ ] **Step 4: Implement in `astrbot/core/db/sqlite.py`**

Shared shape: every read uses `select(...)` inside `_run_in_tx`; writes use `_apply_updates` + `updated_at` refresh. Match the query style of neighboring methods (e.g. `get_chatui_project_by_id`): if they use `session.exec(...)`, use it; if they use `session.execute(...).scalars()`, use that.

```python
    # ==== Agent Teams ====

    async def create_agent_team(self, *, team_id, owner_username, name,
                                coordinator_member_id, members, config) -> AgentTeam:
        """Create one agent team row.

        Args:
            team_id: Unique team identifier.
            owner_username: Dashboard username owning the team.
            name: Team display name.
            coordinator_member_id: Member id acting as coordinator.
            members: List of member dicts.
            config: Team-level run configuration.

        Returns:
            The persisted AgentTeam.
        """
        team = AgentTeam(
            team_id=team_id, owner_username=owner_username, name=name,
            coordinator_member_id=coordinator_member_id, members=members,
            config=config,
        )

        def _op(session):
            session.add(team)
            return team

        return await self._run_in_tx(_op)

    async def get_agent_team(self, team_id: str) -> AgentTeam | None:
        statement = select(AgentTeam).where(AgentTeam.team_id == team_id)

        def _op(session):
            return session.exec(statement).first()

        return await self._run_in_tx(_op)

    async def get_agent_teams_by_owner(self, owner_username: str) -> list[AgentTeam]:
        statement = (
            select(AgentTeam)
            .where(AgentTeam.owner_username == owner_username)
            .order_by(AgentTeam.id.desc())
        )

        def _op(session):
            return list(session.exec(statement).all())

        return await self._run_in_tx(_op)

    async def update_agent_team(self, team_id: str, **updates) -> None:
        async def _op(session):
            team = session.exec(
                select(AgentTeam).where(AgentTeam.team_id == team_id)
            ).first()
            if team is None:
                return None
            self._apply_updates(team, **updates)
            team.updated_at = datetime.now()
            session.add(team)
            return None

        await self._run_in_tx(_op)

    async def delete_agent_team(self, team_id: str) -> None:
        async def _op(session):
            team = session.exec(
                select(AgentTeam).where(AgentTeam.team_id == team_id)
            ).first()
            if team is not None:
                session.delete(team)
            return None

        await self._run_in_tx(_op)

    async def create_agent_team_workflow(self, *, workflow_id, team_id, name,
                                         graph, layout) -> AgentTeamWorkflow:
        workflow = AgentTeamWorkflow(
            workflow_id=workflow_id, team_id=team_id, name=name,
            graph=graph, layout=layout,
        )

        def _op(session):
            session.add(workflow)
            return workflow

        return await self._run_in_tx(_op)

    async def get_agent_team_workflow(self, workflow_id: str) -> AgentTeamWorkflow | None:
        statement = select(AgentTeamWorkflow).where(
            AgentTeamWorkflow.workflow_id == workflow_id
        )

        def _op(session):
            return session.exec(statement).first()

        return await self._run_in_tx(_op)

    async def get_agent_team_workflows_by_team(self, team_id: str) -> list[AgentTeamWorkflow]:
        statement = (
            select(AgentTeamWorkflow)
            .where(AgentTeamWorkflow.team_id == team_id)
            .order_by(AgentTeamWorkflow.id.desc())
        )

        def _op(session):
            return list(session.exec(statement).all())

        return await self._run_in_tx(_op)

    async def update_agent_team_workflow(self, workflow_id: str, **updates) -> None:
        async def _op(session):
            workflow = session.exec(
                select(AgentTeamWorkflow).where(
                    AgentTeamWorkflow.workflow_id == workflow_id
                )
            ).first()
            if workflow is None:
                return None
            self._apply_updates(workflow, **updates)
            workflow.updated_at = datetime.now()
            session.add(workflow)
            return None

        await self._run_in_tx(_op)

    async def delete_agent_team_workflow(self, workflow_id: str) -> None:
        async def _op(session):
            workflow = session.exec(
                select(AgentTeamWorkflow).where(
                    AgentTeamWorkflow.workflow_id == workflow_id
                )
            ).first()
            if workflow is not None:
                session.delete(workflow)
            return None

        await self._run_in_tx(_op)

    async def create_agent_team_run(self, *, run_id, team_id, workflow_id, mode, input,
                                    status, graph_snapshot, node_states, rounds) -> AgentTeamRun:
        run = AgentTeamRun(
            run_id=run_id, team_id=team_id, workflow_id=workflow_id, mode=mode,
            input=input, status=status, graph_snapshot=graph_snapshot,
            node_states=node_states, rounds=rounds,
        )

        def _op(session):
            session.add(run)
            return run

        return await self._run_in_tx(_op)

    async def get_agent_team_run(self, run_id: str) -> AgentTeamRun | None:
        statement = select(AgentTeamRun).where(AgentTeamRun.run_id == run_id)

        def _op(session):
            return session.exec(statement).first()

        return await self._run_in_tx(_op)

    async def get_agent_team_runs_by_team(self, team_id: str) -> list[AgentTeamRun]:
        statement = (
            select(AgentTeamRun)
            .where(AgentTeamRun.team_id == team_id)
            .order_by(AgentTeamRun.id.desc())
        )

        def _op(session):
            return list(session.exec(statement).all())

        return await self._run_in_tx(_op)

    async def get_active_agent_team_run(self, team_id: str) -> AgentTeamRun | None:
        statement = (
            select(AgentTeamRun)
            .where(
                AgentTeamRun.team_id == team_id,
                AgentTeamRun.status.in_(["running", "paused"]),
            )
            .order_by(AgentTeamRun.id.desc())
        )

        def _op(session):
            return session.exec(statement).first()

        return await self._run_in_tx(_op)

    async def update_agent_team_run(self, run_id: str, **updates) -> None:
        async def _op(session):
            run = session.exec(
                select(AgentTeamRun).where(AgentTeamRun.run_id == run_id)
            ).first()
            if run is None:
                return None
            self._apply_updates(run, **updates)
            run.updated_at = datetime.now()
            session.add(run)
            return None

        await self._run_in_tx(_op)

    async def get_agent_team_runs_by_status(self, statuses: list[str]) -> list[AgentTeamRun]:
        statement = (
            select(AgentTeamRun)
            .where(AgentTeamRun.status.in_(statuses))
            .order_by(AgentTeamRun.id.desc())
        )

        def _op(session):
            return list(session.exec(statement).all())

        return await self._run_in_tx(_op)
```

Import `AgentTeam`, `AgentTeamRun`, `AgentTeamWorkflow` in `sqlite.py` following the existing po import block.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/agent_teams/test_agent_team_repo.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
ruff format astrbot/core/db/ tests/agent_teams/
ruff check astrbot/core/db/ tests/agent_teams/
git add astrbot/core/db/__init__.py astrbot/core/db/sqlite.py tests/agent_teams/test_agent_team_repo.py
git commit -m "feat: add agent teams repository methods"
```

---

### Task 3: Pure DAG algorithms (`agent_team_dag.py`)

**Files:**
- Create: `astrbot/dashboard/services/agent_team_dag.py`
- Test: `tests/agent_teams/test_agent_team_dag.py`

**Interfaces:**
- Produces (used by Tasks 5, 8):
  - `class TeamDAGError(ValueError)` — error type for invalid graphs and render failures.
  - `validate_dag(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]` — returns adjacency `node_id -> [direct successor ids]`; raises `TeamDAGError` on duplicate node id, edge referencing unknown node, or cycle.
  - `topological_layers(nodes: list[dict], edges: list[dict]) -> list[list[str]]` — Kahn layering (independent nodes batched per layer).
  - `downstream_of(node_id: str, edges: list[dict]) -> list[str]` — transitive successors (cascade-skip set).
  - `render_task(template: str, run_input: str, results: dict[str, str], max_length: int) -> str` — substitutes `{{input}}` and `{{<node_id>}}`; raises `TeamDAGError` on unknown placeholder; tail-truncates substitutions to `max_length`.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_dag.py
"""Unit tests for pure DAG helpers."""

import pytest

from astrbot.dashboard.services.agent_team_dag import (
    TeamDAGError,
    downstream_of,
    render_task,
    topological_layers,
    validate_dag,
)

NODES = [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}, {"id": "n4"}]
EDGES = [
    {"from": "n1", "to": "n3"},
    {"from": "n2", "to": "n3"},
    {"from": "n3", "to": "n4"},
]


def test_layers_and_adjacency():
    adj = validate_dag(NODES, EDGES)
    assert adj["n1"] == ["n3"] and adj["n3"] == ["n4"]
    layers = topological_layers(NODES, EDGES)
    assert layers == [["n1", "n2"], ["n3"], ["n4"]]


def test_cycle_rejected():
    edges = EDGES + [{"from": "n4", "to": "n1"}]
    with pytest.raises(TeamDAGError, match="cycle"):
        validate_dag(NODES, edges)


def test_dangling_and_duplicate_rejected():
    with pytest.raises(TeamDAGError, match="unknown node"):
        validate_dag(NODES, [{"from": "nx", "to": "n1"}])
    with pytest.raises(TeamDAGError, match="duplicate"):
        validate_dag([{"id": "n1"}, {"id": "n1"}], [])


def test_downstream_transitive():
    assert sorted(downstream_of("n1", EDGES)) == ["n3", "n4"]
    assert downstream_of("n4", EDGES) == []


def test_render_task():
    out = render_task(
        "目标: {{input}}\n前驱结论: {{n1}}", "写诗", {"n1": "春天来了"}, max_length=100
    )
    assert out == "目标: 写诗\n前驱结论: 春天来了"


def test_render_unknown_placeholder():
    with pytest.raises(TeamDAGError, match="unknown placeholder"):
        render_task("{{nope}}", "x", {"n1": "y"}, max_length=10)


def test_render_truncates_tail():
    out = render_task("{{n1}}", "x", {"n1": "0123456789"}, max_length=4)
    assert out.endswith("6789") and "截断" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_dag.py -v`
Expected: FAIL — `ModuleNotFoundError: ... agent_team_dag`

- [ ] **Step 3: Implement `astrbot/dashboard/services/agent_team_dag.py`**

```python
"""Pure DAG helpers for Agent Teams manual orchestration (spec §6.3).

No IO here: everything operates on plain dicts so scheduling rules are
unit-testable without ports or database.
"""

import re

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class TeamDAGError(ValueError):
    """Raised for invalid workflow graphs or task template render failures."""


def _node_ids(nodes: list[dict]) -> list[str]:
    ids = [str(n.get("id", "")) for n in nodes]
    if any(not i for i in ids):
        raise TeamDAGError("node missing id")
    if len(set(ids)) != len(ids):
        raise TeamDAGError("duplicate node id")
    return ids


def validate_dag(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """Validate a workflow graph and return the successor adjacency.

    Args:
        nodes: [{id, ...}] node list.
        edges: [{from, to}] directed edges.

    Returns:
        Adjacency dict mapping node id -> list of direct successor ids.

    Raises:
        TeamDAGError: On duplicate ids, edges referencing unknown nodes,
            or a cycle.
    """
    ids = _node_ids(nodes)
    id_set = set(ids)
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    for edge in edges:
        src, dst = str(edge.get("from", "")), str(edge.get("to", ""))
        if src not in id_set or dst not in id_set:
            raise TeamDAGError(f"edge references unknown node: {src!r}->{dst!r}")
        adjacency[src].append(dst)

    # Kahn's algorithm; leftover nodes mean a cycle.
    indegree = {i: 0 for i in ids}
    for successors in adjacency.values():
        for dst in successors:
            indegree[dst] += 1
    queue = [i for i in ids if indegree[i] == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for dst in adjacency[node]:
            indegree[dst] -= 1
            if indegree[dst] == 0:
                queue.append(dst)
    if visited != len(ids):
        raise TeamDAGError("cycle detected in workflow graph")
    return adjacency


def topological_layers(nodes: list[dict], edges: list[dict]) -> list[list[str]]:
    """Layer nodes so every node appears after all its predecessors.

    Args:
        nodes: [{id, ...}] node list (must validate).
        edges: [{from, to}] directed edges.

    Returns:
        List of layers, each a list of mutually independent node ids.
    """
    adjacency = validate_dag(nodes, edges)
    indegree = {i: 0 for i in adjacency}
    for successors in adjacency.values():
        for dst in successors:
            indegree[dst] += 1
    layer = [i for i, degree in indegree.items() if degree == 0]
    layers: list[list[str]] = []
    while layer:
        layers.append(layer)
        next_layer: list[str] = []
        for node in layer:
            for dst in adjacency[node]:
                indegree[dst] -= 1
                if indegree[dst] == 0:
                    next_layer.append(dst)
        layer = next_layer
    return layers


def downstream_of(node_id: str, edges: list[dict]) -> list[str]:
    """Return every transitive successor of node_id (cascade-skip set)."""
    successors: dict[str, list[str]] = {}
    for edge in edges:
        successors.setdefault(str(edge.get("from", "")), []).append(
            str(edge.get("to", ""))
        )
    seen: list[str] = []
    stack = list(successors.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.append(current)
        stack.extend(successors.get(current, []))
    return seen


def render_task(
    template: str, run_input: str, results: dict[str, str], max_length: int
) -> str:
    """Render a node task template.

    Args:
        template: Template text containing {{input}} / {{<node_id>}}.
        run_input: The run-level input substituted for {{input}}.
        results: node_id -> full reply text for referenced predecessors.
        max_length: Tail-truncation limit for each substitution.

    Returns:
        The rendered task text.

    Raises:
        TeamDAGError: If a placeholder references an unknown node.
    """

    def _substitute(match: re.Match) -> str:
        key = match.group(1)
        if key == "input":
            value = run_input
        elif key in results:
            value = results[key]
        else:
            raise TeamDAGError(
                f"unknown placeholder {{{{{key}}}}}; known: input, {sorted(results)}"
            )
        if len(value) > max_length:
            value = f"…[已截断，仅保留尾部]\n{value[-max_length:]}"
        return value

    return _PLACEHOLDER_RE.sub(_substitute, template)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent_teams/test_agent_team_dag.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/dashboard/services/agent_team_dag.py tests/agent_teams/
ruff check astrbot/dashboard/services/agent_team_dag.py tests/agent_teams/
git add astrbot/dashboard/services/agent_team_dag.py tests/agent_teams/test_agent_team_dag.py
git commit -m "feat: add pure DAG helpers for agent teams"
```

---

### Task 4: `AgentTeamService` — team CRUD + member lifecycle

**Files:**
- Create: `astrbot/dashboard/services/agent_team_service.py`
- Test: `tests/agent_teams/test_agent_team_service.py`

**Interfaces:**
- Consumes: repo methods (Task 2), `ChatService.new_session(username, platform_id) -> {"session_id", "platform_id"}` (`chat_service.py:2264`), `core_lifecycle.conversation_manager.new_conversation(unified_msg_origin, platform_id, content=None, title=None, persona_id=None)` (`conversation_mgr.py:92`), `core_lifecycle.provider_manager.set_provider(provider_id, ProviderType.CHAT, umo)` (`provider/manager.py:153`).
- Produces:
  - `class AgentTeamsServiceError(Exception)`
  - `DEFAULT_TEAM_CONFIG = {"failure_policy": "pause", "reply_timeout": 600.0, "max_rounds": 20, "max_parallel": 5, "inject_max_length": 4000}`; `MAX_MEMBERS = 10`; `MAX_NAME_LEN = 32`
  - `class AgentTeamService` with ctor `__init__(self, db, core_lifecycle, chat_service, busy_checker: Callable[[str], bool] | None = None)`.
  - `async create_team(username: str, payload: dict) -> dict` — payload `{name, members: [{name, persona_id?, system_prompt?, provider_id?}], coordinator: <member name>, config?}`; creates all member sessions server-side; returns the team dict.
  - `async list_teams(username) -> dict` / `async get_team(username, team_id) -> dict` / `async update_team(username, team_id, payload) -> dict` / `async delete_team(username, team_id) -> dict`
  - `async add_member(username, team_id, payload: dict) -> dict` / `async remove_member(username, team_id, member_id) -> dict`
  - Team dicts: `{"team_id", "owner_username", "name", "coordinator_member_id", "members", "config", "created_at", "updated_at"}`.
  - `_team_to_dict(team) -> dict` module-level helper (Task 8's `_require_team` imports it).
  - Workflow CRUD is appended to this class in Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_service.py
"""AgentTeamService tests: team CRUD and member session lifecycle."""

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamsServiceError,
    AgentTeamService,
)


class FakeChatService:
    def __init__(self):
        self.counter = 0

    async def new_session(self, username: str, platform_id: str) -> dict:
        self.counter += 1
        return {"session_id": f"conv-{self.counter:04d}", "platform_id": platform_id}


class FakeConversationManager:
    def __init__(self):
        self.created = []

    async def new_conversation(self, unified_msg_origin, platform_id, content=None,
                               title=None, persona_id=None):
        self.created.append({"umo": unified_msg_origin, "persona_id": persona_id})
        return "cid-1"


class FakeProviderManager:
    def __init__(self):
        self.set = []

    async def set_provider(self, provider_id, provider_type, umo=None):
        self.set.append((provider_id, umo))


class FakeCoreLifecycle:
    def __init__(self):
        self.conversation_manager = FakeConversationManager()
        self.provider_manager = FakeProviderManager()


async def make_service(tmp_path, busy=None):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    return db, AgentTeamService(
        db=db,
        core_lifecycle=FakeCoreLifecycle(),
        chat_service=FakeChatService(),
        busy_checker=busy or (lambda sid: False),
    )


MEMBERS = [
    {"name": "主管", "persona_id": "p1"},
    {"name": "写手", "system_prompt": "你是写手", "provider_id": "prov-a"},
    {"name": "审校", "persona_id": "p2"},
]


@pytest.mark.asyncio
async def test_create_team_creates_member_sessions(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "alice", {"name": "内容团队", "members": MEMBERS, "coordinator": "主管"}
    )
    assert team["coordinator_member_id"] == team["members"][0]["member_id"]
    assert len(team["members"]) == 3
    assert team["members"][1]["provider_id"] == "prov-a"
    assert all(m["session_id"].startswith("conv-") for m in team["members"])
    assert team["config"]["failure_policy"] == "pause"
    row = await db.get_agent_team(team["team_id"])
    assert row is not None and row.name == "内容团队"


@pytest.mark.asyncio
async def test_create_team_rejects_bad_coordinator_and_duplicates(tmp_path):
    _, svc = await make_service(tmp_path)
    with pytest.raises(AgentTeamsServiceError, match="coordinator"):
        await svc.create_team(
            "alice", {"name": "t", "members": MEMBERS, "coordinator": "不存在"}
        )
    with pytest.raises(AgentTeamsServiceError, match="unique"):
        await svc.create_team(
            "alice",
            {"name": "t", "members": MEMBERS + [dict(MEMBERS[0])],
             "coordinator": "主管"},
        )


@pytest.mark.asyncio
async def test_sessions_unique_across_teams(tmp_path):
    _, svc = await make_service(tmp_path)
    t1 = await svc.create_team(
        "alice", {"name": "a", "members": MEMBERS, "coordinator": "主管"}
    )
    t2 = await svc.create_team(
        "bob", {"name": "b", "members": MEMBERS, "coordinator": "写手"}
    )
    s1 = {m["session_id"] for m in t1["members"]}
    s2 = {m["session_id"] for m in t2["members"]}
    assert s1.isdisjoint(s2)


@pytest.mark.asyncio
async def test_remove_member_guards(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    victim = team["members"][1]
    busy_svc = AgentTeamService(
        db=db,
        core_lifecycle=FakeCoreLifecycle(),
        chat_service=FakeChatService(),
        busy_checker=lambda sid: sid == victim["session_id"],
    )
    with pytest.raises(AgentTeamsServiceError, match="busy"):
        await busy_svc.remove_member("alice", team["team_id"], victim["member_id"])
    coordinator = team["members"][0]
    with pytest.raises(AgentTeamsServiceError, match="coordinator"):
        await svc.remove_member("alice", team["team_id"], coordinator["member_id"])
    await svc.remove_member("alice", team["team_id"], victim["member_id"])
    updated = await svc.get_team("alice", team["team_id"])
    assert len(updated["members"]) == 2


@pytest.mark.asyncio
async def test_delete_team_guard_and_ownership(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    with pytest.raises(AgentTeamsServiceError, match="不存在"):
        await svc.get_team("bob", team["team_id"])
    await db.create_agent_team_run(
        run_id="r1", team_id=team["team_id"], workflow_id=None, mode="dag",
        input="x", status="running", graph_snapshot={}, node_states={}, rounds=[],
    )
    with pytest.raises(AgentTeamsServiceError, match="active run"):
        await svc.delete_team("alice", team["team_id"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_service.py -v`
Expected: FAIL — `ModuleNotFoundError: ... agent_team_service`

- [ ] **Step 3: Implement `astrbot/dashboard/services/agent_team_service.py`**

```python
"""Agent Teams team/member management (spec §6.1).

Workflow CRUD is appended to this class in Task 5; run lifecycle lives in
agent_team_run_service.py.
"""

import uuid
from typing import Callable

from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import AgentTeam
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderType

MAX_MEMBERS = 10
MAX_NAME_LEN = 32

DEFAULT_TEAM_CONFIG = {
    "failure_policy": "pause",  # pause | auto_skip
    "reply_timeout": 600.0,
    "max_rounds": 20,
    "max_parallel": 5,
    "inject_max_length": 4000,
}
VALID_FAILURE_POLICIES = ("pause", "auto_skip")


class AgentTeamsServiceError(Exception):
    """Raised for agent team service errors; the message is user-facing."""


def _new_id(n: int = 8) -> str:
    return uuid.uuid4().hex[:n]


def _team_to_dict(team: AgentTeam) -> dict:
    return {
        "team_id": team.team_id,
        "owner_username": team.owner_username,
        "name": team.name,
        "coordinator_member_id": team.coordinator_member_id,
        "members": team.members,
        "config": {**DEFAULT_TEAM_CONFIG, **(team.config or {})},
        "created_at": team.created_at,
        "updated_at": team.updated_at,
    }


class AgentTeamService:
    """Team and member management over the agent_teams tables."""

    def __init__(
        self,
        db: BaseDatabase,
        core_lifecycle,
        chat_service,
        busy_checker: Callable[[str], bool] | None = None,
    ) -> None:
        """Args:
        db: Database helper (repo methods from the agent-teams migration).
        core_lifecycle: Core lifecycle providing conversation/provider managers.
        chat_service: Dashboard ChatService for member session creation.
        busy_checker: Optional session_id -> bool; wired to the chat run
            registry to guard member removal while a turn is in flight.
        """
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.chat_service = chat_service
        self.busy_checker = busy_checker or (lambda session_id: False)

    # ---------- helpers ----------

    async def _get_owned(self, username: str, team_id: str) -> AgentTeam:
        team = await self.db.get_agent_team(team_id)
        if team is None or team.owner_username != username:
            raise AgentTeamsServiceError(f"团队 '{team_id}' 不存在")
        return team

    @staticmethod
    def _merged_config(config: dict | None) -> dict:
        merged = {**DEFAULT_TEAM_CONFIG, **(config or {})}
        if merged["failure_policy"] not in VALID_FAILURE_POLICIES:
            raise AgentTeamsServiceError(
                f"failure_policy 必须是 {VALID_FAILURE_POLICIES} 之一"
            )
        if not 0 < merged["max_parallel"] <= 5:
            raise AgentTeamsServiceError("max_parallel 必须在 1..5 之间")
        if not 0 < merged["max_rounds"] <= 20:
            raise AgentTeamsServiceError("max_rounds 必须在 1..20 之间")
        return merged

    async def _create_member(self, username: str, payload: dict) -> dict:
        """Create one member: session + conversation persona (+ provider).

        Args:
            username: Requesting dashboard user (session creator).
            payload: {name, persona_id? | system_prompt?, provider_id?}.

        Returns:
            The member dict stored in team.members.

        Raises:
            AgentTeamsServiceError: On invalid payload.
        """
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > MAX_NAME_LEN:
            raise AgentTeamsServiceError(f"成员名必填且 ≤{MAX_NAME_LEN} 字符")
        persona_id = payload.get("persona_id") or None
        system_prompt = str(payload.get("system_prompt") or "").strip()
        provider_id = str(payload.get("provider_id") or "").strip() or None
        if not persona_id and not system_prompt:
            raise AgentTeamsServiceError(
                f"成员 '{name}' 需要 persona_id 或 system_prompt 之一"
            )

        session = await self.chat_service.new_session(username, "webchat")
        session_id = session["session_id"]
        umo = str(MessageSession("webchat", MessageType.FRIEND_MESSAGE, session_id))
        await self.core_lifecycle.conversation_manager.new_conversation(
            umo, "webchat", persona_id=persona_id
        )
        if provider_id:
            await self.core_lifecycle.provider_manager.set_provider(
                provider_id, ProviderType.CHAT, umo
            )
        # A bare system_prompt (no persona_id) is stored on the member and
        # injected per-turn by the run service via TeamPorts context.
        return {
            "member_id": _new_id(),
            "name": name,
            "session_id": session_id,
            "umo": umo,
            "persona_id": persona_id,
            "provider_id": provider_id,
            "system_prompt": system_prompt or None,
        }

    # ---------- teams ----------

    async def create_team(self, username: str, payload: dict) -> dict:
        """Create a team and all its member sessions in one call."""
        name = str(payload.get("name") or "").strip()
        if not name:
            raise AgentTeamsServiceError("团队名不能为空")
        raw_members = payload.get("members") or []
        if not isinstance(raw_members, list) or len(raw_members) < 2:
            raise AgentTeamsServiceError("至少需要 2 名成员")
        if len(raw_members) > MAX_MEMBERS:
            raise AgentTeamsServiceError(f"成员数不能超过 {MAX_MEMBERS}")
        coordinator_name = str(payload.get("coordinator") or "").strip()

        members: list[dict] = []
        seen_names: set[str] = set()
        seen_sessions: set[str] = set()
        for raw in raw_members:
            member = await self._create_member(username, raw)
            if member["name"].lower() in seen_names:
                raise AgentTeamsServiceError(
                    f"成员名必须 unique: {member['name']}"
                )
            if member["session_id"] in seen_sessions:
                raise AgentTeamsServiceError("成员会话冲突，请重试")
            seen_names.add(member["name"].lower())
            seen_sessions.add(member["session_id"])
            members.append(member)

        if coordinator_name not in {m["name"] for m in members}:
            raise AgentTeamsServiceError(
                f"coordinator 必须是成员之一: {coordinator_name!r}"
            )
        team_id = _new_id()
        await self.db.create_agent_team(
            team_id=team_id,
            owner_username=username,
            name=name,
            coordinator_member_id=next(
                m["member_id"] for m in members if m["name"] == coordinator_name
            ),
            members=members,
            config=self._merged_config(payload.get("config")),
        )
        return await self.get_team(username, team_id)

    async def list_teams(self, username: str) -> dict:
        rows = await self.db.get_agent_teams_by_owner(username)
        return {"teams": [_team_to_dict(t) for t in rows]}

    async def get_team(self, username: str, team_id: str) -> dict:
        return _team_to_dict(await self._get_owned(username, team_id))

    async def update_team(self, username: str, team_id: str, payload: dict) -> dict:
        team = await self._get_owned(username, team_id)
        updates: dict = {}
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise AgentTeamsServiceError("团队名不能为空")
            updates["name"] = name
        if "coordinator" in payload:
            coordinator = str(payload["coordinator"]).strip()
            match = next(
                (m for m in team.members if m["name"] == coordinator), None
            )
            if match is None:
                raise AgentTeamsServiceError(f"协调者必须是成员之一: {coordinator!r}")
            updates["coordinator_member_id"] = match["member_id"]
        if "config" in payload:
            updates["config"] = self._merged_config(
                {**(team.config or {}), **(payload.get("config") or {})}
            )
        if updates:
            await self.db.update_agent_team(team_id, **updates)
        return await self.get_team(username, team_id)

    async def delete_team(self, username: str, team_id: str) -> dict:
        await self._get_owned(username, team_id)
        active = await self.db.get_active_agent_team_run(team_id)
        if active is not None:
            raise AgentTeamsServiceError("团队有 active run，无法删除")
        # Member sessions are preserved by design (spec §6.1): users manage
        # them from the chat page.
        await self.db.delete_agent_team(team_id)
        return {"message": "团队已删除"}

    # ---------- members ----------

    async def add_member(self, username: str, team_id: str, payload: dict) -> dict:
        team = await self._get_owned(username, team_id)
        if len(team.members) >= MAX_MEMBERS:
            raise AgentTeamsServiceError(f"成员数不能超过 {MAX_MEMBERS}")
        member = await self._create_member(username, payload)
        if any(m["name"].lower() == member["name"].lower() for m in team.members):
            raise AgentTeamsServiceError(f"成员名必须 unique: {member['name']}")
        await self.db.update_agent_team(team_id, members=[*team.members, member])
        return member

    async def remove_member(
        self, username: str, team_id: str, member_id: str
    ) -> dict:
        team = await self._get_owned(username, team_id)
        member = next((m for m in team.members if m["member_id"] == member_id), None)
        if member is None:
            raise AgentTeamsServiceError(f"成员 '{member_id}' 不存在")
        if member["member_id"] == team.coordinator_member_id:
            raise AgentTeamsServiceError("不能移除协调者，请先切换协调者")
        if self.busy_checker(member["session_id"]):
            raise AgentTeamsServiceError("成员会话正在执行，无法移除")
        if await self.db.get_active_agent_team_run(team_id):
            raise AgentTeamsServiceError("团队有 active run，无法移除成员")
        await self.db.update_agent_team(
            team_id, members=[m for m in team.members if m["member_id"] != member_id]
        )
        return {"message": "成员已移除"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent_teams/test_agent_team_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/dashboard/services/agent_team_service.py tests/agent_teams/
ruff check astrbot/dashboard/services/agent_team_service.py tests/agent_teams/
git add astrbot/dashboard/services/agent_team_service.py tests/agent_teams/test_agent_team_service.py
git commit -m "feat: add agent team and member management service"
```

---

### Task 5: Workflow CRUD + graph validation

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_service.py` (append the workflow section to `AgentTeamService`)
- Test: `tests/agent_teams/test_agent_team_workflow_service.py`

**Interfaces:**
- Consumes: `validate_dag` / `TeamDAGError` (Task 3), repo workflow methods (Task 2), `AgentTeamService` (Task 4).
- Produces on `AgentTeamService`:
  - `MAX_NODES = 20` (class attribute)
  - `async create_workflow(username, team_id, payload: dict) -> dict` — payload `{name, graph: {nodes, edges}, layout?}`; validates; fills missing layout with `{}`.
  - `async get_workflows(username, team_id) -> dict`
  - `async update_workflow(username, team_id, workflow_id, payload: dict) -> dict` — accepts partial `{name?, graph?, layout?}`; graph changes re-validated; even a name-only change re-validates the stored graph against the CURRENT roster (removed member → error).
  - `async delete_workflow(username, team_id, workflow_id) -> dict`
  - `validate_member_bindings(graph: dict, members: list[dict]) -> None` (staticmethod) — raises `AgentTeamsServiceError` listing missing member ids.
  - `_workflow_to_dict(row) -> dict` (staticmethod) — `{"workflow_id", "team_id", "name", "graph", "layout", "created_at", "updated_at"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_workflow_service.py
"""Workflow CRUD and validation tests."""

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamsServiceError,
    AgentTeamService,
)
from tests.agent_teams.test_agent_team_service import (
    FakeChatService,
    FakeCoreLifecycle,
    MEMBERS,
)

GRAPH = {
    "nodes": [
        {"id": "n1", "member_id": "unbound", "task": "调研 {{input}}"},
        {"id": "n2", "member_id": "unbound", "task": "根据 {{n1}} 写作"},
    ],
    "edges": [{"from": "n1", "to": "n2"}],
}


def make_service(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    return db, AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=FakeChatService()
    )


def bind_graph(graph: dict, team: dict) -> dict:
    """Point every node at a real member id of the team."""
    member_ids = [m["member_id"] for m in team["members"]]
    nodes = [
        {**n, "member_id": member_ids[i % len(member_ids)]}
        for i, n in enumerate(graph["nodes"])
    ]
    return {**graph, "nodes": nodes}


async def make_team(svc):
    return await svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )


@pytest.mark.asyncio
async def test_workflow_crud_roundtrip(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await make_team(svc)
    wf = await svc.create_workflow(
        "alice", team["team_id"], {"name": "流水线", "graph": bind_graph(GRAPH, team)}
    )
    assert wf["graph"]["edges"] == [{"from": "n1", "to": "n2"}]

    new_graph = bind_graph(
        {"nodes": [{"id": "a", "member_id": "x", "task": "t"}], "edges": []}, team
    )
    updated = await svc.update_workflow(
        "alice", team["team_id"], wf["workflow_id"], {"name": "v2", "graph": new_graph}
    )
    assert updated["name"] == "v2" and len(updated["graph"]["nodes"]) == 1

    await svc.delete_workflow("alice", team["team_id"], wf["workflow_id"])
    assert (await svc.get_workflows("alice", team["team_id"]))["workflows"] == []


@pytest.mark.asyncio
async def test_workflow_rejects_cycle_and_bad_member(tmp_path):
    _, svc = await make_service(tmp_path)
    team = await make_team(svc)
    cyclic = bind_graph(
        {
            "nodes": [
                {"id": "n1", "member_id": "x", "task": "t"},
                {"id": "n2", "member_id": "x", "task": "t"},
            ],
            "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n1"}],
        },
        team,
    )
    with pytest.raises(AgentTeamsServiceError, match="cycle"):
        await svc.create_workflow("alice", team["team_id"], {"name": "c", "graph": cyclic})
    ghost = bind_graph(GRAPH, team)
    ghost["nodes"][0]["member_id"] = "missing-member"
    with pytest.raises(AgentTeamsServiceError, match="missing-member"):
        await svc.create_workflow("alice", team["team_id"], {"name": "g", "graph": ghost})


@pytest.mark.asyncio
async def test_update_revalidates_against_current_members(tmp_path):
    db, svc = await make_service(tmp_path)
    team = await make_team(svc)
    wf = await svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": bind_graph(GRAPH, team)}
    )
    victim = next(
        m
        for m in team["members"]
        if m["member_id"] == wf["graph"]["nodes"][0]["member_id"]
    )
    await svc.remove_member("alice", team["team_id"], victim["member_id"])
    with pytest.raises(AgentTeamsServiceError):
        await svc.update_workflow(
            "alice", team["team_id"], wf["workflow_id"], {"name": "v2"}
        )
```

If the repo's pytest config cannot import across test modules (`from tests.agent_teams.test_agent_team_service import ...`), copy the three small fakes (`FakeChatService`, `FakeCoreLifecycle`, `MEMBERS`) into this file instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_workflow_service.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'create_workflow'`

- [ ] **Step 3: Implement — append to `AgentTeamService`**

```python
    # ---------- workflows ----------

    MAX_NODES = 20

    @staticmethod
    def _workflow_to_dict(row) -> dict:
        return {
            "workflow_id": row.workflow_id,
            "team_id": row.team_id,
            "name": row.name,
            "graph": row.graph,
            "layout": row.layout,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def validate_member_bindings(graph: dict, members: list[dict]) -> None:
        """Raise when any node references a member not on the team roster.

        Args:
            graph: {nodes, edges} graph dict.
            members: Current team members.

        Raises:
            AgentTeamsServiceError: Listing every missing member id.
        """
        known = {m["member_id"] for m in members}
        missing = sorted(
            {
                str(n.get("member_id"))
                for n in graph.get("nodes") or []
                if n.get("member_id") not in known
            }
        )
        if missing:
            raise AgentTeamsServiceError(
                f"节点绑定的成员不存在: {', '.join(missing)}"
            )

    def _validate_workflow_payload(self, team: AgentTeam, graph: dict) -> dict:
        """Validate a workflow graph against DAG rules and the team roster.

        Args:
            team: The owning team row.
            graph: {nodes, edges} raw payload.

        Returns:
            The normalized graph dict.

        Raises:
            AgentTeamsServiceError: On any validation failure.
        """
        from astrbot.dashboard.services.agent_team_dag import (
            TeamDAGError,
            validate_dag,
        )

        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if not isinstance(nodes, list) or len(nodes) < 1:
            raise AgentTeamsServiceError("工作流至少需要 1 个节点")
        if len(nodes) > self.MAX_NODES:
            raise AgentTeamsServiceError(f"节点数不能超过 {self.MAX_NODES}")
        for node in nodes:
            if not str(node.get("task") or "").strip():
                raise AgentTeamsServiceError(f"节点 {node.get('id')!r} 缺少任务模板")
        try:
            validate_dag(nodes, edges)
        except TeamDAGError as e:
            raise AgentTeamsServiceError(str(e)) from e
        self.validate_member_bindings(graph, team.members)
        return {"nodes": nodes, "edges": edges}

    async def create_workflow(
        self, username: str, team_id: str, payload: dict
    ) -> dict:
        team = await self._get_owned(username, team_id)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise AgentTeamsServiceError("工作流名不能为空")
        graph = self._validate_workflow_payload(team, payload.get("graph") or {})
        workflow_id = _new_id()
        await self.db.create_agent_team_workflow(
            workflow_id=workflow_id,
            team_id=team_id,
            name=name,
            graph=graph,
            layout=payload.get("layout") or {},
        )
        return self._workflow_to_dict(
            await self.db.get_agent_team_workflow(workflow_id)
        )

    async def get_workflows(self, username: str, team_id: str) -> dict:
        await self._get_owned(username, team_id)
        rows = await self.db.get_agent_team_workflows_by_team(team_id)
        return {"workflows": [self._workflow_to_dict(w) for w in rows]}

    async def update_workflow(
        self, username: str, team_id: str, workflow_id: str, payload: dict
    ) -> dict:
        team = await self._get_owned(username, team_id)
        row = await self.db.get_agent_team_workflow(workflow_id)
        if row is None or row.team_id != team_id:
            raise AgentTeamsServiceError(f"工作流 '{workflow_id}' 不存在")
        updates: dict = {}
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise AgentTeamsServiceError("工作流名不能为空")
            updates["name"] = name
        if "graph" in payload:
            updates["graph"] = self._validate_workflow_payload(
                team, payload.get("graph") or {}
            )
        else:
            # Re-validate the stored graph against the CURRENT roster so a
            # removed member invalidates dependent workflows immediately.
            self.validate_member_bindings(row.graph, team.members)
        if "layout" in payload:
            updates["layout"] = payload.get("layout") or {}
        if updates:
            await self.db.update_agent_team_workflow(workflow_id, **updates)
        return self._workflow_to_dict(
            await self.db.get_agent_team_workflow(workflow_id)
        )

    async def delete_workflow(
        self, username: str, team_id: str, workflow_id: str
    ) -> dict:
        await self._get_owned(username, team_id)
        row = await self.db.get_agent_team_workflow(workflow_id)
        if row is None or row.team_id != team_id:
            raise AgentTeamsServiceError(f"工作流 '{workflow_id}' 不存在")
        await self.db.delete_agent_team_workflow(workflow_id)
        return {"message": "工作流已删除"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agent_teams/ -v`
Expected: PASS (all previous tasks too)

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/dashboard/services/agent_team_service.py tests/agent_teams/
ruff check astrbot/dashboard/services/agent_team_service.py tests/agent_teams/
git add astrbot/dashboard/services/agent_team_service.py tests/agent_teams/test_agent_team_workflow_service.py
git commit -m "feat: add agent team workflow CRUD and validation"
```

---

### Task 6: `TeamPorts` — the delivery seam

**Files:**
- Create: `astrbot/dashboard/services/agent_team_ports.py`
- Modify: `astrbot/core/platform/sources/webchat/webchat_adapter.py` (3-line extra passthrough after the `collab_context` handling, around line 305)
- Test: `tests/agent_teams/test_agent_team_ports.py`

**Interfaces:**
- Consumes: `webchat_queue_mgr` (`get_or_create_queue`, `subscribe_system`), `ChatService.register_synthetic_chat_run(session_id, message_id, username, llm_checkpoint_id)` (`chat_service.py:1851`), `resolve_webchat_request_flags`, `BotMessageAccumulator` — identical mechanics to `AgentCollabService.build_ports_for_test`/`build_ports` (`agent_collab_service.py:36-44, 291-447`); copy those bodies and apply the three changes listed in Step 3.
- Produces:
  - `_conversation_id(session_id: str) -> str` (module-level, moved verbatim from collab)
  - `@dataclass class TeamPorts` — fields `deliver`, `collect`, `is_busy`, `emit`, `reply_timeout: float = 600.0`, `busy_poll_interval: float = 2.0`
  - `build_ports_for_test(mgr, username, emit, reply_timeout=600.0, busy_poll_interval=2.0, is_busy_override=None, run_registrar=None) -> TeamPorts` — payload key `team_context` (NOT `collab_context`)
  - `build_ports(chat_service, username, emit) -> TeamPorts` — production wiring (`run_registrar=chat_service.register_synthetic_chat_run`, `is_busy` reads `chat_service.chat_runs_by_session`)

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_ports.py
"""TeamPorts roundtrip over the real WebChatQueueMgr contract.

Mirrors tests/test_agent_collab_integration.py: a fake listener plays the
role of the webchat adapter, mirroring a scripted reply to system
subscribers after receiving the injected input.
"""

import pytest

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import WebChatQueueMgr
from astrbot.dashboard.services.agent_team_ports import build_ports_for_test


@pytest.mark.asyncio
async def test_deliver_collect_roundtrip_and_registrar_order():
    mgr = WebChatQueueMgr()
    scripted = {"conv-1": ["成员回复"]}
    registered: list[tuple[str, str]] = []
    inbox: list[dict] = []

    async def fake_listener(data):
        username, conv_id, payload = data
        inbox.append(payload)
        mid = payload["message_id"]
        for chunk, t in ((scripted[conv_id].pop(0), "plain"), ("", "end")):
            await mgr.put_system_event(
                conv_id,
                {"type": t, "message_id": mid, "data": chunk,
                 "streaming": False, "chain_type": "normal"},
            )

    mgr.set_listener(fake_listener)

    async def run_registrar(cid, message_id, checkpoint_id):
        registered.append((cid, message_id))

    events: list[dict] = []
    ports = build_ports_for_test(
        mgr, "alice", emit=events.append, run_registrar=run_registrar
    )
    message_id = await ports.deliver("conv-1", "任务文本", "团队上下文")
    reply, parts = await ports.collect("conv-1", message_id)

    assert reply == "成员回复"
    # register MUST have happened before the input was queued
    assert registered and registered[0][1] == message_id
    payload = inbox[0]
    assert payload["team_context"] == "团队上下文"
    assert payload["persist_user_history"] is True
    assert any(e["type"] == "message" and e["direction"] == "stream" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_ports.py -v`
Expected: FAIL — `ModuleNotFoundError: ... agent_team_ports`

- [ ] **Step 3: Implement `astrbot/dashboard/services/agent_team_ports.py`**

Copy the bodies of `AgentCollabService.build_ports_for_test` / `build_ports` / `_conversation_id` into module-level functions with exactly these changes: (1) ports class renamed `TeamPorts`; (2) payload key `collab_context` → `team_context`; (3) default `reply_timeout=600.0`.

```python
"""TeamPorts: delivery/collection seam for Agent Teams runners (spec §6.2).

Extracted from AgentCollabService so the DAGRunner (and the Plan-3
AutoOrchestrator) run over the same injectable I/O boundary; tests script
deliver/collect directly.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from astrbot.core.platform.sources.webchat.request_flags import (
    resolve_webchat_request_flags,
)
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    _extract_conversation_id,
    webchat_queue_mgr,
)
from astrbot.core.umo_alias import parse_umo


def _conversation_id(session_id: str) -> str:
    """Map a member session id (UMO or raw webchat conversation id) to the
    raw conversation id used by the queue manager."""
    if session_id.startswith("webchat!"):
        return _extract_conversation_id(session_id)
    parsed = parse_umo(session_id)
    if parsed["platform"] == "webchat":
        return parsed["session_id"]
    return session_id


@dataclass
class TeamPorts:
    """I/O ports for team runners; injected for testability."""

    deliver: Callable[[str, str, str | None], Awaitable[str]]
    collect: Callable[[str, str], Awaitable[tuple[str, list]]]
    is_busy: Callable[[str], bool]
    emit: Callable[[dict], None]
    reply_timeout: float = 600.0
    busy_poll_interval: float = 2.0


def build_ports_for_test(
    mgr,
    username: str,
    emit: Callable[[dict], None],
    reply_timeout: float = 600.0,
    busy_poll_interval: float = 2.0,
    is_busy_override: Callable[[str], bool] | None = None,
    run_registrar: Callable[[str, str, str], Awaitable[None]] | None = None,
) -> TeamPorts:
    """Build TeamPorts over a given WebChatQueueMgr (test seam).

    Args:
        mgr: WebChatQueueMgr instance.
        username: Sender name recorded on injected turns.
        emit: Runner event sink (SSE multiplexer).
        reply_timeout: Per-turn collection timeout in seconds.
        busy_poll_interval: Busy-wait poll interval in seconds.
        is_busy_override: Optional busy predicate override.
        run_registrar: Optional async (cid, message_id, checkpoint_id) hook
            awaited BEFORE the input is queued; production wires it to
            ChatService.register_synthetic_chat_run.

    Returns:
        Configured TeamPorts.
    """
    # One system-event subscription per conversation, created in deliver()
    # BEFORE the input item is queued, mirroring build_chat_stream's
    # register-then-inject ordering.
    subscriptions: dict[str, asyncio.Queue] = {}

    async def deliver(session_id: str, text: str, context: str | None = None) -> str:
        cid = _conversation_id(session_id)
        message_id = uuid.uuid4().hex
        llm_checkpoint_id = uuid.uuid4().hex
        queue = mgr.get_or_create_queue(cid)
        subscriptions.setdefault(cid, mgr.subscribe_system(cid))
        if run_registrar is not None:
            await run_registrar(cid, message_id, llm_checkpoint_id)
        await queue.put(
            (
                username,
                cid,
                {
                    "message": [{"type": "plain", "text": text}],
                    "selected_provider": None,
                    "selected_model": None,
                    "flags": resolve_webchat_request_flags({}),
                    "message_id": message_id,
                    "llm_checkpoint_id": llm_checkpoint_id,
                    "thread_selected_text": None,
                    "_api_key_allow_admin_role": None,
                    # Per-turn team framing, injected by build_main_agent as
                    # a temp extra (provider-facing, not persisted).
                    "team_context": context,
                    "persist_user_history": True,
                },
            )
        )
        return message_id

    async def collect(session_id: str, message_id: str) -> tuple[str, list]:
        from astrbot.dashboard.services.chat_service import BotMessageAccumulator

        cid = _conversation_id(session_id)
        queue = subscriptions.get(cid)
        if queue is None:
            raise asyncio.TimeoutError(
                f"no subscription for {cid}; deliver() must run first"
            )
        acc = BotMessageAccumulator()
        prev_text = ""
        while True:
            payload = await queue.get()
            if (
                not isinstance(payload, dict)
                or payload.get("message_id") != message_id
            ):
                continue
            msg_type = payload.get("type")
            if msg_type == "plain":
                acc.add_plain(
                    payload.get("data", ""),
                    chain_type=payload.get("chain_type"),
                    streaming=bool(payload.get("streaming")),
                )
                full = acc.plain_text()
                delta = full[len(prev_text):] if full.startswith(prev_text) else full
                prev_text = full
                if delta:
                    emit(
                        {
                            "type": "message",
                            "direction": "stream",
                            "session_id": session_id,
                            "text": delta,
                            "ts": time.time(),
                        }
                    )
            elif msg_type in ("complete", "end"):
                if (
                    msg_type == "complete"
                    and not payload.get("streaming")
                    and payload.get("data")
                ):
                    acc.add_plain(
                        payload["data"],
                        chain_type=payload.get("chain_type"),
                        streaming=False,
                    )
                return acc.plain_text(), acc.build_message_parts()

    return TeamPorts(
        deliver=deliver,
        collect=collect,
        is_busy=is_busy_override or (lambda sid: False),
        emit=emit,
        reply_timeout=reply_timeout,
        busy_poll_interval=busy_poll_interval,
    )


def build_ports(
    chat_service, username: str, emit: Callable[[dict], None]
) -> TeamPorts:
    """Build production TeamPorts bound to the real ChatService.

    Args:
        chat_service: Dashboard ChatService; injected turns register through
            register_synthetic_chat_run and busy detection reads
            chat_runs_by_session.
        username: Run owner, sender of injected turns.
        emit: Runner event sink (SSE multiplexer).

    Returns:
        TeamPorts wired to the global webchat queue manager.
    """
    ports = build_ports_for_test(
        webchat_queue_mgr,
        username,
        emit,
        run_registrar=chat_service.register_synthetic_chat_run,
    )
    ports.is_busy = lambda sid: bool(
        chat_service.chat_runs_by_session.get(_conversation_id(sid))
    )
    return ports
```

- [ ] **Step 4: WebChat adapter passthrough**

In `astrbot/core/platform/sources/webchat/webchat_adapter.py`, immediately after the `collab_context` block (lines ~305-307), add the identical pattern:

```python
                team_context = payload.get("team_context")
                if isinstance(team_context, str) and team_context.strip():
                    message_event.set_extra("team_context", team_context)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/agent_teams/test_agent_team_ports.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
ruff format astrbot/dashboard/services/agent_team_ports.py tests/agent_teams/ astrbot/core/platform/sources/webchat/webchat_adapter.py
ruff check astrbot/dashboard/services/agent_team_ports.py tests/agent_teams/ astrbot/core/platform/sources/webchat/webchat_adapter.py
git add astrbot/dashboard/services/agent_team_ports.py tests/agent_teams/test_agent_team_ports.py astrbot/core/platform/sources/webchat/webchat_adapter.py
git commit -m "feat: add TeamPorts delivery seam for agent teams"
```

---

### Task 7: `RunEventBus`

**Files:**
- Create: `astrbot/dashboard/services/agent_team_run_service.py` (bus only in this task; runners come in Task 8)
- Test: `tests/agent_teams/test_agent_team_run_bus.py`

**Interfaces:**
- Produces: `class RunEventBus` — `emit(event: dict) -> None` (append to in-memory history + fan out); `subscribe() -> asyncio.Queue` (maxsize 256, drop-oldest on overflow); `unsubscribe(queue) -> None`; `history() -> list[dict]` (defensive copy).

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_run_bus.py
"""RunEventBus fan-out, history and slow-consumer semantics."""

import asyncio

from astrbot.dashboard.services.agent_team_run_service import RunEventBus


async def test_history_replay_and_live_fanout():
    bus = RunEventBus()
    bus.emit({"type": "node_status", "node_id": "n1", "status": "running"})
    q = bus.subscribe()
    bus.emit({"type": "node_status", "node_id": "n1", "status": "done"})

    assert bus.history()[0]["node_id"] == "n1"
    event = await asyncio.wait_for(q.get(), timeout=1)
    assert event["status"] == "done"  # subscriber only sees post-subscription events
    bus.unsubscribe(q)


async def test_slow_consumer_drops_oldest():
    bus = RunEventBus()
    q = bus.subscribe()
    for i in range(300):
        bus.emit({"type": "tick", "i": i})
    first = await asyncio.wait_for(q.get(), timeout=1)
    assert first["i"] > 0  # oldest events were dropped, emit never blocked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_run_bus.py -v`
Expected: FAIL — `ImportError: cannot import name 'RunEventBus'`

- [ ] **Step 3: Implement**

Create `astrbot/dashboard/services/agent_team_run_service.py`:

```python
"""Agent Teams run lifecycle: event bus, DAGRunner, run service (spec §6.3/§6.5/§6.6)."""

import asyncio


class RunEventBus:
    """Per-run in-memory event history plus live subscriber fan-out.

    Mirrors the agent-collab SSE multiplexer pattern: late subscribers get a
    full history replay from the SSE layer, live events flow through
    per-subscriber bounded queues with drop-oldest on slow consumers.
    """

    _SUB_QUEUE_MAX = 256

    def __init__(self) -> None:
        self._history: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

    def emit(self, event: dict) -> None:
        """Record an event and fan it out to all subscribers."""
        self._history.append(event)
        for queue in list(self._subscribers):
            if queue.qsize() >= self._SUB_QUEUE_MAX:
                try:
                    queue.get_nowait()  # drop-oldest
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        """Register a live subscriber queue (post-subscription events only)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._SUB_QUEUE_MAX)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a previously registered subscriber queue."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def history(self) -> list[dict]:
        """Return a defensive copy of all emitted events."""
        return list(self._history)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent_teams/test_agent_team_run_bus.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/
ruff check astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/
git add astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/test_agent_team_run_bus.py
git commit -m "feat: add agent team run event bus"
```

---

### Task 8: `DAGRunner` + `AgentTeamRunService`

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_run_service.py`
- Test: `tests/agent_teams/test_agent_team_dag_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 2-7.
- Produces:
  - `class DAGRunner` — keyword-only ctor:
    `__init__(self, *, run_id: str, team_id: str, graph: dict, config: dict, members: list[dict], ports: TeamPorts, db, bus: RunEventBus, username: str, run_input: str = "", node_states: dict[str, dict] | None = None, on_member_stop: Callable[[str], object] | None = None)`.
    Attributes: `status: str`, `node_states: dict`, `task: asyncio.Task | None`. Methods: `async run()`, `pause()`, `resume()`, `request_stop()`, `async retry_node(node_id)`, `async skip_node(node_id)`, `snapshot() -> dict` (`{"run_id", "team_id", "status", "node_states", "progress"}`). `run()` is re-callable after `retry_node`/`resume`.
  - `class AgentTeamRunService` — ctor `__init__(self, db, chat_service, busy_checker=None, on_member_stop=None)`; instance attribute `ports_factory: Callable[[username, emit], TeamPorts]` (default `build_ports(chat_service, ...)`, overridden in tests). Methods:
    - `async start_run(username, team_id, payload: dict) -> dict` — payload `{mode: "dag", input: str, workflow_id: str}`; raises `AgentTeamsServiceError` containing "active run" when the team already has one.
    - `async resume_run(username, run_id) -> dict` (Task 9)
    - `async boot_sweep() -> int` (Task 9)
    - `get_run_snapshot(username, run_id) -> dict` / `async list_active_runs(username) -> dict` / `async list_team_runs(username, team_id) -> dict`
    - `pause_run(run_id) -> dict` / `async request_stop_run(run_id) -> dict` / `async retry_node(run_id, node_id) -> dict` / `async skip_node(run_id, node_id) -> dict`
    - `get_event_bus(run_id) -> RunEventBus`
  - Module helpers: `_run_to_dict(row) -> dict`, `async _require_team(db, username, team_id) -> dict` (imports `_team_to_dict` from the service module), `async _owned(db, username, team_id) -> bool`, `TERMINAL_RUN_STATUSES = ("completed", "stopped", "failed")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_dag_runner.py
"""DAGRunner end-to-end over scripted ports + persistence checks."""

import asyncio

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import (
    AgentTeamRunService,
    DAGRunner,
    RunEventBus,
)
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamsServiceError,
    AgentTeamService,
)
from tests.agent_teams.test_agent_team_service import (
    FakeChatService,
    FakeCoreLifecycle,
    MEMBERS,
)

GRAPH = {
    "nodes": [
        {"id": "n1", "member_id": "mA", "task": "调研 {{input}}"},
        {"id": "n2", "member_id": "mB", "task": "校对 {{input}}"},
        {"id": "n3", "member_id": "mC", "task": "汇总 {{n1}} 与 {{n2}}"},
    ],
    "edges": [{"from": "n1", "to": "n3"}, {"from": "n2", "to": "n3"}],
}

MEMBER_BY_SESSION: dict[str, dict] = {}


def make_members():
    members = []
    for i, raw in enumerate(MEMBERS):
        members.append(
            {
                "member_id": f"m{'ABC'[i]}",
                "name": raw["name"],
                "session_id": f"conv-{i}",
                "umo": f"webchat:FriendMessage:conv-{i}",
                "persona_id": raw.get("persona_id"),
                "provider_id": None,
                "system_prompt": None,
            }
        )
    MEMBER_BY_SESSION.clear()
    for m in members:
        MEMBER_BY_SESSION[m["session_id"]] = m
    return members


def scripted_ports(responses: dict, events: list, delivered: list) -> TeamPorts:
    """Ports whose collect() returns scripted per-member replies."""

    async def deliver(session_id: str, text: str, context=None) -> str:
        delivered.append((session_id, text))
        return f"mid-{len(delivered)}"

    async def collect(session_id: str, message_id: str) -> tuple[str, list]:
        member = MEMBER_BY_SESSION[session_id]
        result = responses[member["name"]]
        await asyncio.sleep(0.01)
        if isinstance(result, Exception):
            raise result
        return result, [{"type": "plain", "data": result}]

    return TeamPorts(
        deliver=deliver, collect=collect, is_busy=lambda sid: False,
        emit=events.append,
    )


CONFIG = {"failure_policy": "pause", "reply_timeout": 5.0,
          "max_parallel": 5, "inject_max_length": 4000}


async def wait_terminal(runner: DAGRunner, timeout_s: float = 5.0):
    for _ in range(int(timeout_s / 0.02)):
        if runner.status in ("completed", "paused", "stopped", "failed"):
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_dag_runner_happy_path(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    ports = scripted_ports(
        {"主管": "调研完成", "写手": "校对完成", "审校": "汇总完成"}, events, delivered
    )
    runner = DAGRunner(
        run_id="r1", team_id="t1", graph=GRAPH, config=CONFIG,
        members=list(MEMBER_BY_SESSION.values()), ports=ports, db=db,
        bus=RunEventBus(), username="alice", run_input="测试主题",
    )
    await runner.run()

    assert runner.status == "completed"
    assert all(s["status"] == "done" for s in runner.node_states.values())
    # n3's rendered task received BOTH predecessor results
    n3_delivery = next(d for d in delivered if d[0] == "conv-2")
    assert "调研完成" in n3_delivery[1] and "校对完成" in n3_delivery[1]
    row = await db.get_agent_team_run("r1")
    assert row.status == "completed"  # persisted
    assert any(e["type"] == "dag_progress" for e in events)


@pytest.mark.asyncio
async def test_failure_policy_pause_then_retry(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    responses = {"主管": "ok", "写手": RuntimeError("session gone"), "审校": "x"}
    ports = scripted_ports(responses, events, delivered)
    runner = DAGRunner(
        run_id="r2", team_id="t1", graph=GRAPH, config=CONFIG,
        members=list(MEMBER_BY_SESSION.values()), ports=ports, db=db,
        bus=RunEventBus(), username="alice", run_input="主题",
    )
    await runner.run()
    await wait_terminal(runner)
    assert runner.status == "paused"
    failed = [n for n, s in runner.node_states.items() if s["status"] == "failed"]
    assert failed

    responses["写手"] = "修好了"
    await runner.retry_node(failed[0])
    runner.task = asyncio.create_task(runner.run())
    for _ in range(250):
        if runner.status == "completed":
            break
        await asyncio.sleep(0.02)
    assert runner.status == "completed"


@pytest.mark.asyncio
async def test_failure_policy_auto_skip_cascades(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    make_members()
    events: list = []
    delivered: list = []
    responses = {"主管": "ok", "写手": RuntimeError("boom"), "审校": "x"}
    ports = scripted_ports(responses, events, delivered)
    runner = DAGRunner(
        run_id="r3", team_id="t1", graph=GRAPH,
        config={**CONFIG, "failure_policy": "auto_skip"},
        members=list(MEMBER_BY_SESSION.values()), ports=ports, db=db,
        bus=RunEventBus(), username="alice", run_input="主题",
    )
    await runner.run()
    assert runner.status == "completed"
    assert runner.node_states["n3"]["status"] == "skipped"  # cascade from n2


@pytest.mark.asyncio
async def test_start_run_full_lifecycle(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    chat = FakeChatService()
    team_svc = AgentTeamService(
        db=db, core_lifecycle=FakeCoreLifecycle(), chat_service=chat
    )
    team = await team_svc.create_team(
        "alice", {"name": "t", "members": MEMBERS, "coordinator": "主管"}
    )
    ids = [m["member_id"] for m in team["members"]]
    graph = {
        "nodes": [
            {"id": "n1", "member_id": ids[0], "task": "做 {{input}}"},
            {"id": "n2", "member_id": ids[1], "task": "查 {{n1}}"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    wf = await team_svc.create_workflow(
        "alice", team["team_id"], {"name": "w", "graph": graph}
    )
    run_svc = AgentTeamRunService(db=db, chat_service=chat)
    responses = {m["name"]: f"{m['name']}-done" for m in team["members"]}
    events: list = []
    delivered: list = []
    run_svc.ports_factory = lambda username, emit: scripted_ports(
        responses, events, delivered
    )

    snapshot = await run_svc.start_run(
        "alice", team["team_id"],
        {"mode": "dag", "input": "主题", "workflow_id": wf["workflow_id"]},
    )
    runner = run_svc._runners[snapshot["run_id"]]
    await wait_terminal(runner)
    snap = run_svc.get_run_snapshot("alice", snapshot["run_id"])
    assert snap["status"] == "completed"

    # a second run is rejected while an active row exists (API maps to 409)
    await db.create_agent_team_run(
        run_id="rbad", team_id=team["team_id"], workflow_id=None, mode="dag",
        input="x", status="running", graph_snapshot={}, node_states={}, rounds=[],
    )
    with pytest.raises(AgentTeamsServiceError, match="active run"):
        await run_svc.start_run(
            "alice", team["team_id"],
            {"mode": "dag", "input": "y", "workflow_id": wf["workflow_id"]},
        )
```

If the repo's pytest config cannot import across test modules, copy the small fakes into this file.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_dag_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'DAGRunner'`

- [ ] **Step 3: Implement — append to `astrbot/dashboard/services/agent_team_run_service.py`**

Update the module imports at the top (merge with Task 7's `import asyncio`):

```python
import asyncio
import time
import uuid
from typing import Callable

from astrbot import logger
from astrbot.dashboard.services.agent_team_dag import (
    TeamDAGError,
    downstream_of,
    render_task,
    topological_layers,
)
from astrbot.dashboard.services.agent_team_ports import TeamPorts, build_ports
from astrbot.dashboard.services.agent_team_service import (
    DEFAULT_TEAM_CONFIG,
    AgentTeamsServiceError,
    _team_to_dict,
)

TERMINAL_RUN_STATUSES = ("completed", "stopped", "failed")


def _run_to_dict(row) -> dict:
    return {
        "run_id": row.run_id,
        "team_id": row.team_id,
        "workflow_id": row.workflow_id,
        "mode": row.mode,
        "input": row.input,
        "status": row.status,
        "result_summary": row.result_summary,
        "graph": row.graph_snapshot,
        "node_states": row.node_states,
        "rounds": row.rounds,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
```

```python
class DAGRunner:
    """Wave-scheduled DAG execution over TeamPorts (spec §6.3).

    The runner owns one run row; every node transition is persisted
    immediately so an interrupted process can resume (Task 9). `run()` is
    re-callable after `retry_node`/`resume`.
    """

    def __init__(
        self,
        *,
        run_id: str,
        team_id: str,
        graph: dict,
        config: dict,
        members: list[dict],
        ports: TeamPorts,
        db,
        bus: RunEventBus,
        username: str,
        run_input: str = "",
        node_states: dict[str, dict] | None = None,
        on_member_stop: Callable[[str], object] | None = None,
    ) -> None:
        self.run_id = run_id
        self.team_id = team_id
        self.graph = graph
        self.config = {**DEFAULT_TEAM_CONFIG, **(config or {})}
        self.members = members
        self.ports = ports
        self.db = db
        self.bus = bus
        self.username = username
        self.run_input = run_input
        self.on_member_stop = on_member_stop
        self.status = "running"
        self.node_states: dict[str, dict] = node_states or {}
        self.task: asyncio.Task | None = None
        self._resume_wake = asyncio.Event()
        self._stop_requested = asyncio.Event()
        self._results: dict[str, str] = {}
        self._preds: dict[str, list[str]] = {}
        for edge in graph.get("edges", []):
            src, dst = str(edge["from"]), str(edge["to"])
            self._preds.setdefault(dst, []).append(src)

    def _member_by_id(self, member_id: str) -> dict | None:
        return next((m for m in self.members if m["member_id"] == member_id), None)

    def _emit(self, event: dict) -> None:
        self.bus.emit({"ts": time.time(), **event})

    async def _persist(self) -> None:
        """Flush status + node_states to the run row (per-transition)."""
        await self.db.update_agent_team_run(
            self.run_id, status=self.status, node_states=self.node_states
        )

    def _progress(self) -> dict:
        counts: dict[str, int] = {}
        for state in self.node_states.values():
            counts[state["status"]] = counts.get(state["status"], 0) + 1
        return {
            "type": "dag_progress",
            "done": counts.get("done", 0),
            "running": counts.get("running", 0),
            "pending": counts.get("pending", 0),
            "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0),
            "total": len(self.node_states),
        }

    def snapshot(self) -> dict:
        """Return a JSON-safe summary for API and SSE consumers."""
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "status": self.status,
            "node_states": self.node_states,
            "progress": {k: v for k, v in self._progress().items() if k != "type"},
        }

    # ---------- controls ----------

    def pause(self) -> None:
        """Halt scheduling; in-flight nodes settle, then run() returns."""
        if self.status == "running":
            self.status = "paused"

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "running"
            self._resume_wake.set()

    def request_stop(self) -> None:
        self._stop_requested.set()
        self._resume_wake.set()

    async def retry_node(self, node_id: str) -> None:
        """Re-queue a failed node and resume the run (spec §6.3).

        Args:
            node_id: The failed node to re-execute.

        Raises:
            AgentTeamsServiceError: If the node is not in `failed` state.
        """
        state = self.node_states.get(node_id)
        if state is None or state["status"] != "failed":
            raise AgentTeamsServiceError(f"节点 {node_id} 不可重试")
        state.update(status="pending", error=None, result=None,
                     started_at=None, finished_at=None)
        await self._persist()
        self.resume()

    async def skip_node(self, node_id: str) -> None:
        """Mark a failed node skipped and cascade-skip pending downstream.

        Args:
            node_id: The failed node to skip.

        Raises:
            AgentTeamsServiceError: If the node is not in `failed` state.
        """
        state = self.node_states.get(node_id)
        if state is None or state["status"] != "failed":
            raise AgentTeamsServiceError(f"节点 {node_id} 不可跳过")
        state["status"] = "skipped"
        for downstream in downstream_of(node_id, self.graph.get("edges", [])):
            if self.node_states[downstream]["status"] == "pending":
                self.node_states[downstream]["status"] = "skipped"
        await self._persist()
        self._emit(self._progress())
        self.resume()

    # ---------- node execution ----------

    async def _wait_if_busy(self, session_id: str) -> None:
        while self.ports.is_busy(session_id) and self.status == "running":
            self._emit({"type": "busy", "session_id": session_id})
            await asyncio.sleep(self.ports.busy_poll_interval)

    async def _execute_node(self, node: dict) -> None:
        """Deliver one node's task to its member and collect the reply."""
        node_id = node["id"]
        member = self._member_by_id(node["member_id"])
        state = self.node_states[node_id]
        if member is None:
            state.update(status="failed", error=f"member {node['member_id']} missing")
            await self._persist()
            self._emit({"type": "node_status", "node_id": node_id, "status": "failed"})
            self._emit(self._progress())
            return
        try:
            task_text = render_task(
                node.get("task", ""), self.run_input, self._results,
                int(self.config["inject_max_length"]),
            )
        except TeamDAGError as e:
            state.update(status="failed", error=str(e))
            await self._persist()
            self._emit({"type": "node_status", "node_id": node_id,
                        "status": "failed", "error": str(e)})
            self._emit(self._progress())
            return

        state.update(status="running", task_rendered=task_text,
                     started_at=time.time(), error=None)
        await self._persist()
        self._emit({"type": "node_status", "node_id": node_id,
                    "member_id": member["member_id"], "status": "running"})
        self._emit(self._progress())

        await self._wait_if_busy(member["session_id"])
        if self.status != "running":
            # paused/stop raced the delivery: put the node back to pending
            state.update(status="pending", started_at=None)
            await self._persist()
            return
        self._emit({"type": "message", "direction": "sent",
                    "member_id": member["member_id"],
                    "session_id": member["session_id"], "text": task_text})
        message_id = await self.ports.deliver(member["session_id"], task_text, None)
        try:
            reply, _parts = await asyncio.wait_for(
                self.ports.collect(member["session_id"], message_id),
                timeout=float(self.config["reply_timeout"]),
            )
        except asyncio.TimeoutError:
            state.update(status="failed", error="reply timeout",
                         finished_at=time.time())
        except Exception as e:  # noqa: BLE001
            state.update(status="failed", error=str(e), finished_at=time.time())
        else:
            state.update(status="done", result=reply, finished_at=time.time())
            self._results[node_id] = reply
            self._emit({"type": "message", "direction": "reply",
                        "member_id": member["member_id"],
                        "session_id": member["session_id"], "text": reply})
        await self._persist()
        self._emit({"type": "node_status", "node_id": node_id,
                    "member_id": member["member_id"],
                    "status": state["status"], "error": state.get("error")})
        self._emit(self._progress())

    # ---------- main loop ----------

    async def run(self) -> None:
        """Execute pending layers until completion, pause, or stop.

        Never raises: a crash lands the run on terminal `failed` so the UI
        never sticks on a running state.
        """
        try:
            await self._run_loop()
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent team run %s crashed", self.run_id)
            self.status = "failed"
            await self._persist()
            self._emit({"type": "stopped", "reason": f"runner error: {exc}"})
            return
        if self.status == "completed":
            self._emit({"type": "stopped", "reason": "done"})
        elif self.status == "stopped":
            self._emit({"type": "stopped", "reason": "user stop"})

    async def _run_loop(self) -> None:
        nodes_by_id = {n["id"]: n for n in self.graph.get("nodes", [])}
        while True:
            if self._stop_requested.is_set():
                self.status = "stopped"
                await self._persist()
                return
            ready = sorted(
                node_id
                for node_id, state in self.node_states.items()
                if state["status"] == "pending"
                and all(
                    self.node_states[p]["status"] in ("done", "skipped")
                    for p in self._preds.get(node_id, [])
                )
            )
            if not ready:
                break
            if self.status == "paused":
                await self._persist()
                self._emit({"type": "paused", "reason": "user pause"})
                self._resume_wake.clear()
                await self._resume_wake.wait()
                continue
            wave = [
                nodes_by_id[n] for n in ready[: int(self.config["max_parallel"])]
            ]
            await asyncio.gather(*(self._execute_node(n) for n in wave))
            await self._apply_failure_policy()

        if all(
            s["status"] in ("done", "skipped") for s in self.node_states.values()
        ):
            self.status = "completed"
            done = [
                s.get("result") or ""
                for s in self.node_states.values() if s["status"] == "done"
            ]
            await self.db.update_agent_team_run(
                self.run_id, status=self.status,
                result_summary="\n\n".join(done)[:2000],
            )
        else:
            await self._persist()

    async def _apply_failure_policy(self) -> None:
        """After each wave, apply the team failure policy to failed nodes."""
        failed = [n for n, s in self.node_states.items() if s["status"] == "failed"]
        if not failed:
            return
        if self.config["failure_policy"] == "auto_skip":
            for node_id in failed:
                self.node_states[node_id]["status"] = "skipped"
                for downstream in downstream_of(
                    node_id, self.graph.get("edges", [])
                ):
                    if self.node_states[downstream]["status"] == "pending":
                        self.node_states[downstream]["status"] = "skipped"
            await self._persist()
            self._emit(self._progress())
        else:
            self.status = "paused"
            await self._persist()
            self._emit({"type": "paused", "reason": "node failed",
                        "node_id": failed[0]})
```

```python
class AgentTeamRunService:
    """Run lifecycle: start/resume/stop DAG runs and expose snapshots."""

    def __init__(self, db, chat_service, busy_checker=None, on_member_stop=None):
        """Args:
        db: Database helper.
        chat_service: Dashboard ChatService (ports wiring + busy detection).
        busy_checker: Optional session_id -> bool override (tests).
        on_member_stop: Optional sync (session_id) hook invoked on run stop
            to cancel in-flight member turns; wired to the chat stop API in
            the follow-up plan.
        """
        self.db = db
        self.chat_service = chat_service
        self.busy_checker = busy_checker
        self.on_member_stop = on_member_stop
        # Overridden in tests; production uses build_ports over the real
        # webchat queue manager.
        self.ports_factory: Callable[[str, Callable], TeamPorts] = (
            lambda username, emit: build_ports(chat_service, username, emit)
        )
        self._runners: dict[str, DAGRunner] = {}
        self._buses: dict[str, RunEventBus] = {}

    async def start_run(self, username: str, team_id: str, payload: dict) -> dict:
        """Start a DAG run for a team.

        Args:
            username: Requesting dashboard user.
            team_id: Owning team.
            payload: {mode: "dag", input: str, workflow_id: str}.

        Returns:
            The initial run snapshot.

        Raises:
            AgentTeamsServiceError: On ownership/validation errors, or when
                the team already has an active run (message contains
                "active run"; the API layer maps it to 409).
        """
        team = await self._require_team(username, team_id)
        mode = str(payload.get("mode") or "dag")
        if mode != "dag":
            raise AgentTeamsServiceError("Plan 1 仅支持 mode=dag（自动编排见后续计划）")
        workflow_id = str(payload.get("workflow_id") or "")
        workflow = await self.db.get_agent_team_workflow(workflow_id)
        if workflow is None or workflow.team_id != team_id:
            raise AgentTeamsServiceError(f"工作流 '{workflow_id}' 不存在")
        if await self.db.get_active_agent_team_run(team_id):
            raise AgentTeamsServiceError("该团队已有 active run，无法重复启动")
        run_input = str(payload.get("input") or "").strip()
        if not run_input:
            raise AgentTeamsServiceError("运行输入不能为空")

        config = {**DEFAULT_TEAM_CONFIG, **(team["config"] or {})}
        node_states = {
            node["id"]: {
                "status": "pending", "member_id": node["member_id"],
                "task_rendered": None, "result": None, "error": None,
                "started_at": None, "finished_at": None,
            }
            for node in workflow.graph.get("nodes", [])
        }
        run_id = uuid.uuid4().hex[:12]
        await self.db.create_agent_team_run(
            run_id=run_id, team_id=team_id, workflow_id=workflow_id,
            mode=mode, input=run_input, status="running",
            graph_snapshot=workflow.graph, node_states=node_states, rounds=[],
        )
        runner = self._build_runner(
            run_id=run_id, team=team, graph=workflow.graph, config=config,
            run_input=run_input, node_states=node_states, username=username,
        )
        self._start_runner_task(runner)
        return runner.snapshot()

    def _build_runner(self, *, run_id, team, graph, config, run_input,
                      node_states, username) -> DAGRunner:
        bus = RunEventBus()
        runner = DAGRunner(
            run_id=run_id, team_id=team["team_id"], graph=graph, config=config,
            members=team["members"], ports=self.ports_factory(username, bus.emit),
            db=self.db, bus=bus, username=username, run_input=run_input,
            node_states=node_states, on_member_stop=self.on_member_stop,
        )
        self._runners[run_id] = runner
        self._buses[run_id] = bus
        return runner

    def _start_runner_task(self, runner: DAGRunner) -> None:
        runner.task = asyncio.create_task(
            runner.run(), name=f"agent_team_run_{runner.run_id}"
        )

    async def _require_run(self, username: str, run_id: str):
        """Load a run row and its owning team, enforcing ownership."""
        row = await self.db.get_agent_team_run(run_id)
        if row is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在")
        team = await self._require_team(username, row.team_id)
        return row, team

    async def _require_team(self, username: str, team_id: str) -> dict:
        team = await self.db.get_agent_team(team_id)
        if team is None or team.owner_username != username:
            raise AgentTeamsServiceError(f"团队 '{team_id}' 不存在")
        return _team_to_dict(team)

    def get_event_bus(self, run_id: str) -> RunEventBus:
        bus = self._buses.get(run_id)
        if bus is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在或已不在内存中")
        return bus

    def get_run_snapshot(self, username: str, run_id: str) -> dict:
        runner = self._runners.get(run_id)
        if runner is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在或已结束")
        return runner.snapshot()

    async def list_active_runs(self, username: str) -> dict:
        active = []
        for run_id, runner in list(self._runners.items()):
            if runner.status not in ("running", "paused"):
                continue
            row = await self.db.get_agent_team_run(run_id)
            if row is None:
                continue
            try:
                await self._require_team(username, row.team_id)
            except AgentTeamsServiceError:
                continue
            active.append(runner.snapshot())
        return {"runs": active}

    async def list_team_runs(self, username: str, team_id: str) -> dict:
        await self._require_team(username, team_id)
        rows = await self.db.get_agent_team_runs_by_team(team_id)
        return {"runs": [_run_to_dict(r) for r in rows]}

    def pause_run(self, run_id: str) -> dict:
        runner = self._require_runner(run_id)
        runner.pause()
        return {"message": "已暂停"}

    async def request_stop_run(self, run_id: str) -> dict:
        runner = self._require_runner(run_id)
        runner.request_stop()
        if self.on_member_stop is not None:
            for state in runner.node_states.values():
                if state["status"] != "running":
                    continue
                member = runner._member_by_id(state["member_id"])
                if member is not None:
                    self.on_member_stop(member["session_id"])
        return {"message": "停止中"}

    async def retry_node(self, run_id: str, node_id: str) -> dict:
        runner = self._require_runner(run_id)
        await runner.retry_node(node_id)
        if runner.task is None or runner.task.done():
            self._start_runner_task(runner)
        return {"message": "已重试"}

    async def skip_node(self, run_id: str, node_id: str) -> dict:
        runner = self._require_runner(run_id)
        await runner.skip_node(node_id)
        if runner.task is None or runner.task.done():
            self._start_runner_task(runner)
        return {"message": "已跳过"}

    def _require_runner(self, run_id: str) -> DAGRunner:
        runner = self._runners.get(run_id)
        if runner is None:
            raise AgentTeamsServiceError(f"运行 '{run_id}' 不存在或已结束")
        return runner
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agent_teams/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/
ruff check astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/
git add astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/test_agent_team_dag_runner.py
git commit -m "feat: add DAGRunner and agent team run service"
```

---

### Task 9: Hot resume + boot sweep

**Files:**
- Modify: `astrbot/dashboard/services/agent_team_run_service.py` (add `boot_sweep`, `resume_run` to `AgentTeamRunService`)
- Test: `tests/agent_teams/test_agent_team_resume.py`

**Interfaces:**
- Produces on `AgentTeamRunService`:
  - `async boot_sweep() -> int` — marks every run row with status `running`/`paused` as `interrupted` (in-memory runners are gone after restart); returns the count. Called once at dashboard startup (wired in Task 10).
  - `async resume_run(username, run_id) -> dict` — in-memory paused runner → `runner.resume()` + snapshot. Otherwise the row must be `interrupted` or `paused` (post-restart): nodes recorded `running` reset to `pending` (redo semantics, spec §6.5); `done`/`skipped` results preserved; runner rebuilt from `graph_snapshot` + `node_states`; run row set back to `running`; task spawned; returns snapshot.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent_teams/test_agent_team_resume.py
"""Boot sweep + hot resume semantics (spec §6.5)."""

import asyncio

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_ports import TeamPorts
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from tests.agent_teams.test_agent_team_service import FakeChatService


@pytest.mark.asyncio
async def test_boot_sweep_marks_stale_runs_interrupted(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    for run_id, status in (("r1", "running"), ("r2", "paused"), ("r3", "completed")):
        await db.create_agent_team_run(
            run_id=run_id, team_id="t1", workflow_id=None, mode="dag",
            input="x", status=status, graph_snapshot={}, node_states={}, rounds=[],
        )
    svc = AgentTeamRunService(db=db, chat_service=FakeChatService())
    assert await svc.boot_sweep() == 2
    assert (await db.get_agent_team_run("r1")).status == "interrupted"
    assert (await db.get_agent_team_run("r2")).status == "interrupted"
    assert (await db.get_agent_team_run("r3")).status == "completed"


@pytest.mark.asyncio
async def test_resume_preserves_done_and_redoes_running(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    events: list = []
    delivered: list = []

    async def deliver(session_id, text, context=None):
        delivered.append((session_id, text))
        return f"mid-{len(delivered)}"

    async def collect(session_id, message_id):
        await asyncio.sleep(0.01)
        return "重做完成", [{"type": "plain", "data": "重做完成"}]

    svc = AgentTeamRunService(db=db, chat_service=FakeChatService())
    svc.ports_factory = lambda username, emit: TeamPorts(
        deliver=deliver, collect=collect,
        is_busy=lambda sid: False, emit=events.append,
    )

    members = [
        {"member_id": "m1", "name": "a", "session_id": "c1",
         "umo": "webchat:FriendMessage:c1", "persona_id": None,
         "provider_id": None, "system_prompt": None},
        {"member_id": "m2", "name": "b", "session_id": "c2",
         "umo": "webchat:FriendMessage:c2", "persona_id": None,
         "provider_id": None, "system_prompt": None},
    ]
    graph = {
        "nodes": [
            {"id": "n1", "member_id": "m1", "task": "t1"},
            {"id": "n2", "member_id": "m2", "task": "t2 {{n1}}"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
    }
    node_states = {
        "n1": {"status": "done", "member_id": "m1", "task_rendered": "t1",
               "result": "前驱结果", "error": None, "started_at": None,
               "finished_at": None},
        "n2": {"status": "running", "member_id": "m2", "task_rendered": None,
               "result": None, "error": None, "started_at": None,
               "finished_at": None},
    }
    await db.create_agent_team_run(
        run_id="r1", team_id="t1", workflow_id=None, mode="dag", input="x",
        status="interrupted", graph_snapshot=graph, node_states=node_states,
        rounds=[],
    )
    await db.create_agent_team(
        team_id="t1", owner_username="alice", name="t",
        coordinator_member_id="m1", members=members, config={},
    )

    snapshot = await svc.resume_run("alice", "r1")
    runner = svc._runners[snapshot["run_id"]]
    for _ in range(250):
        if runner.status in ("completed", "paused", "stopped", "failed"):
            break
        await asyncio.sleep(0.02)
    snap = svc.get_run_snapshot("alice", "r1")
    assert snap["status"] == "completed"
    # n1's done result was preserved (never re-delivered); n2 was redone and
    # received n1's preserved result in its rendered task
    assert snap["node_states"]["n1"]["result"] == "前驱结果"
    n2_delivery = next(d for d in delivered if d[0] == "c2")
    assert "前驱结果" in n2_delivery[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent_teams/test_agent_team_resume.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'boot_sweep'`

- [ ] **Step 3: Implement — append to `AgentTeamRunService`**

```python
    async def boot_sweep(self) -> int:
        """Mark stale runs interrupted at startup (spec §6.5).

        In-memory runners do not survive a restart; rows still marked
        running/paused cannot make progress and must surface as
        `interrupted` so the panel offers resume.

        Returns:
            Number of rows transitioned to `interrupted`.
        """
        stale = await self.db.get_agent_team_runs_by_status(["running", "paused"])
        for row in stale:
            await self.db.update_agent_team_run(row.run_id, status="interrupted")
        return len(stale)

    async def resume_run(self, username: str, run_id: str) -> dict:
        """Resume a paused (in-memory) or interrupted (post-restart) run.

        Args:
            username: Requesting dashboard user.
            run_id: Persisted run id.

        Returns:
            The snapshot of the resumed (or rebuilt) runner.

        Raises:
            AgentTeamsServiceError: On ownership errors or a status that
                cannot be resumed.
        """
        runner = self._runners.get(run_id)
        if runner is not None:
            if runner.status != "paused":
                raise AgentTeamsServiceError("运行未处于暂停状态")
            runner.resume()
            return runner.snapshot()

        row, team = await self._require_run(username, run_id)
        if row.status not in ("interrupted", "paused"):
            raise AgentTeamsServiceError(f"运行 '{run_id}' 当前状态不可恢复")
        # Redo semantics: in-flight nodes at interruption are re-dispatched
        # from scratch; done/skipped nodes and their results are preserved.
        node_states = {
            node_id: (
                {**state, "status": "pending"}
                if state["status"] == "running"
                else state
            )
            for node_id, state in (row.node_states or {}).items()
        }
        config = {**DEFAULT_TEAM_CONFIG, **(team["config"] or {})}
        await self.db.update_agent_team_run(
            run_id, status="running", node_states=node_states
        )
        runner = self._build_runner(
            run_id=run_id, team=team, graph=row.graph_snapshot, config=config,
            run_input=row.input, node_states=node_states, username=username,
        )
        self._start_runner_task(runner)
        return runner.snapshot()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agent_teams/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff format astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/
ruff check astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/
git add astrbot/dashboard/services/agent_team_run_service.py tests/agent_teams/test_agent_team_resume.py
git commit -m "feat: add agent team run boot sweep and hot resume"
```

---

### Task 10: API routes, SSE endpoint, registration

**Files:**
- Create: `astrbot/dashboard/api/agent_teams.py`
- Modify: `astrbot/dashboard/api/router.py` (add to `child_routers`, router.py:44-71)
- Modify: `astrbot/dashboard/api/app.py` (legacy router mount + service registration + boot sweep call)
- Test: `tests/agent_teams/test_agent_team_api.py`

**Interfaces:**
- Consumes: services from Tasks 4-9; auth helpers from `astrbot/dashboard/api/auth.py`; `ok`/`error` from `astrbot/dashboard/responses.py`; module shape mirrors `agent_collab.py:1-60`.
- Produces: routes per spec §7 under both `/api/v1/agent_teams/*` (in OpenAPI) and `/api/agent_teams/*` (legacy). `app.state.services.agent_teams` / `app.state.services.agent_team_runs`.

- [ ] **Step 1: Inspect `responses.py` and `auth.py`**

Read `astrbot/dashboard/responses.py` to learn what `error()` returns (dict or JSONResponse) and read `astrbot/dashboard/api/auth.py` to confirm `require_chat_scope` exists (used by `chat.py:205`). Adapt `_handle` below to whatever `error()` returns (if it already returns a `JSONResponse`, wrap with the status via `JSONResponse(error(str(e)).body, ...)` or follow the pattern another v1 module uses for non-200 error statuses).

- [ ] **Step 2: Write the failing test**

```python
# tests/agent_teams/test_agent_team_api.py
"""API smoke tests over a minimal FastAPI app with dependency overrides."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import astrbot.dashboard.api.agent_teams as mod
from astrbot.dashboard.services.agent_team_service import AgentTeamsServiceError


class FakeTeamSvc:
    async def create_team(self, username, payload):
        return {"team_id": "t1", "name": payload.get("name")}

    async def list_teams(self, username):
        return {"teams": []}


class FakeRunSvc:
    async def start_run(self, username, team_id, payload):
        if payload.get("input") == "conflict":
            raise AgentTeamsServiceError("该团队已有 active run，无法重复启动")
        return {"run_id": "r1", "status": "running"}


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
    app.include_router(mod.legacy_router)

    async def _fake_auth():
        return "alice"

    app.dependency_overrides[mod.get_team_service] = lambda: FakeTeamSvc()
    app.dependency_overrides[mod.get_run_service] = lambda: FakeRunSvc()
    app.dependency_overrides[mod._auth_dep] = _fake_auth
    return TestClient(app, raise_server_exceptions=False)


def test_create_team_route(client):
    resp = client.post(
        "/api/v1/agent_teams", json={"name": "t", "members": [], "coordinator": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["team_id"] == "t1"


def test_start_run_maps_active_conflict_to_409(client):
    resp = client.post(
        "/api/v1/agent_teams/t1/runs",
        json={"mode": "dag", "input": "conflict", "workflow_id": "w1"},
    )
    assert resp.status_code == 409
```

- [ ] **Step 3: Implement `astrbot/dashboard/api/agent_teams.py`**

Module shape (all handlers get `username` from `Depends(_auth_dep)` and their service from `Depends(...)`; every service call is wrapped in `try/except AgentTeamsServiceError as e: return _handle(e)`):

```python
"""Agent Teams dashboard routes (spec §7)."""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from astrbot.dashboard.responses import error, ok
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService
from astrbot.dashboard.services.agent_team_service import (
    AgentTeamsServiceError,
    AgentTeamService,
)

from .auth import require_chat_scope

router = APIRouter(tags=["Agent Teams"])
legacy_router = APIRouter(
    prefix="/api/agent_teams",
    tags=["Dashboard Agent Teams"],
    include_in_schema=False,
)

# Single override target for tests; both routers use this dependency.
_auth_dep = require_chat_scope

_CONFLICT_MARKER = "active run"


def get_team_service(request: Request) -> AgentTeamService:
    return request.app.state.services.agent_teams


def get_run_service(request: Request) -> AgentTeamRunService:
    return request.app.state.services.agent_team_runs


def _handle(e: AgentTeamsServiceError):
    """Map a service error to an HTTP response; run conflicts become 409."""
    status = 409 if _CONFLICT_MARKER in str(e) else 400
    return JSONResponse(error(str(e)), status_code=status)


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
```

Handlers — each one is decorated with BOTH routers (stacked decorators give the v1 path and the legacy path the same relative route):

```python
@router.post("/agent_teams")
@legacy_router.post("/agent_teams")
async def create_team(
    request: Request,
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    try:
        return ok(await service.create_team(username, await _json_body(request)))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams")
@legacy_router.get("/agent_teams")
async def list_teams(
    username: str = Depends(_auth_dep),
    service: AgentTeamService = Depends(get_team_service),
):
    return ok(await service.list_teams(username))


@router.get("/agent_teams/{team_id}")
@legacy_router.get("/agent_teams/{team_id}")
async def get_team(team_id: str, username: str = Depends(_auth_dep),
                   service: AgentTeamService = Depends(get_team_service)):
    try:
        return ok(await service.get_team(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.patch("/agent_teams/{team_id}")
@legacy_router.patch("/agent_teams/{team_id}")
async def update_team(team_id: str, request: Request,
                      username: str = Depends(_auth_dep),
                      service: AgentTeamService = Depends(get_team_service)):
    try:
        return ok(await service.update_team(username, team_id, await _json_body(request)))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.delete("/agent_teams/{team_id}")
@legacy_router.delete("/agent_teams/{team_id}")
async def delete_team(team_id: str, username: str = Depends(_auth_dep),
                      service: AgentTeamService = Depends(get_team_service)):
    try:
        return ok(await service.delete_team(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/{team_id}/members")
@legacy_router.post("/agent_teams/{team_id}/members")
async def add_member(team_id: str, request: Request,
                     username: str = Depends(_auth_dep),
                     service: AgentTeamService = Depends(get_team_service)):
    try:
        return ok(await service.add_member(username, team_id, await _json_body(request)))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.delete("/agent_teams/{team_id}/members/{member_id}")
@legacy_router.delete("/agent_teams/{team_id}/members/{member_id}")
async def remove_member(team_id: str, member_id: str,
                        username: str = Depends(_auth_dep),
                        service: AgentTeamService = Depends(get_team_service)):
    try:
        return ok(await service.remove_member(username, team_id, member_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/{team_id}/workflows")
@legacy_router.post("/agent_teams/{team_id}/workflows")
async def create_workflow(team_id: str, request: Request,
                          username: str = Depends(_auth_dep),
                          service: AgentTeamService = Depends(get_team_service)):
    try:
        return ok(await service.create_workflow(
            username, team_id, await _json_body(request)
        ))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/{team_id}/workflows")
@legacy_router.get("/agent_teams/{team_id}/workflows")
async def list_workflows(team_id: str, username: str = Depends(_auth_dep),
                         service: AgentTeamService = Depends(get_team_service)):
    try:
        return ok(await service.get_workflows(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.put("/agent_teams/workflows/{workflow_id}")
@legacy_router.put("/agent_teams/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, request: Request,
                          username: str = Depends(_auth_dep),
                          service: AgentTeamService = Depends(get_team_service)):
    try:
        body = await _json_body(request)
        return ok(await service.update_workflow(
            username, str(body.get("team_id") or ""), workflow_id, body
        ))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.delete("/agent_teams/workflows/{workflow_id}")
@legacy_router.delete("/agent_teams/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request,
                          username: str = Depends(_auth_dep),
                          service: AgentTeamService = Depends(get_team_service)):
    try:
        body = await _json_body(request)
        return ok(await service.delete_workflow(
            username, str(body.get("team_id") or ""), workflow_id
        ))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/{team_id}/runs")
@legacy_router.post("/agent_teams/{team_id}/runs")
async def start_run(team_id: str, request: Request,
                    username: str = Depends(_auth_dep),
                    service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(await service.start_run(username, team_id, await _json_body(request)))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/runs/active")
@legacy_router.get("/agent_teams/runs/active")
async def active_runs(username: str = Depends(_auth_dep),
                      service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(await service.list_active_runs(username))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/{team_id}/runs")
@legacy_router.get("/agent_teams/{team_id}/runs")
async def team_runs(team_id: str, username: str = Depends(_auth_dep),
                    service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(await service.list_team_runs(username, team_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/pause")
@legacy_router.post("/agent_teams/runs/{run_id}/pause")
async def pause_run(run_id: str, username: str = Depends(_auth_dep),
                    service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(service.pause_run(run_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/resume")
@legacy_router.post("/agent_teams/runs/{run_id}/resume")
async def resume_run(run_id: str, username: str = Depends(_auth_dep),
                     service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(await service.resume_run(username, run_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/stop")
@legacy_router.post("/agent_teams/runs/{run_id}/stop")
async def stop_run(run_id: str, username: str = Depends(_auth_dep),
                   service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(await service.request_stop_run(run_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/nodes/{node_id}/retry")
@legacy_router.post("/agent_teams/runs/{run_id}/nodes/{node_id}/retry")
async def retry_node(run_id: str, node_id: str,
                     username: str = Depends(_auth_dep),
                     service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(await service.retry_node(run_id, node_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.post("/agent_teams/runs/{run_id}/nodes/{node_id}/skip")
@legacy_router.post("/agent_teams/runs/{run_id}/nodes/{node_id}/skip")
async def skip_node(run_id: str, node_id: str,
                    username: str = Depends(_auth_dep),
                    service: AgentTeamRunService = Depends(get_run_service)):
    try:
        return ok(await service.skip_node(run_id, node_id))
    except AgentTeamsServiceError as e:
        return _handle(e)


@router.get("/agent_teams/runs/{run_id}/stream")
@legacy_router.get("/agent_teams/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request,
                     username: str = Depends(_auth_dep),
                     service: AgentTeamRunService = Depends(get_run_service)):
    try:
        bus = service.get_event_bus(run_id)
    except AgentTeamsServiceError as e:
        return _handle(e)

    async def generator():
        queue = bus.subscribe()
        try:
            for event in bus.history():
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield (
                        f"data: "
                        f"{json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                    )
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream")
```

- [ ] **Step 4: Register in `router.py` and `app.py`**

- `astrbot/dashboard/api/router.py`: `from .agent_teams import router as agent_teams_router` and add `agent_teams_router` to the `child_routers` tuple (router.py:44-71).
- `astrbot/dashboard/api/app.py`:
  1. Add the legacy mount next to the other legacy routers (app.py ~214-242): `app.include_router(agent_teams_router.legacy_router)` — follow the exact pattern used for the collab legacy router one line away.
  2. Hoist `chat = ChatService(db, core_lifecycle)` into a local variable before the `SimpleNamespace(...)` literal, replace the `chat=ChatService(db, core_lifecycle)` entry with `chat=chat`, then after the namespace:

```python
    services.agent_teams = AgentTeamService(
        db=db,
        core_lifecycle=core_lifecycle,
        chat_service=chat,
        busy_checker=lambda session_id: bool(
            chat.chat_runs_by_session.get(session_id)
        ),
    )
    services.agent_team_runs = AgentTeamRunService(
        db=db,
        chat_service=chat,
        busy_checker=services.agent_teams.busy_checker,
        on_member_stop=None,  # wired to the chat stop API in the follow-up plan
    )
```

  3. In the async startup block that wires the goal-loop run registrar (the `from astrbot.core.goal.goal_service import goal_service` section, app.py ~155-175), add:

```python
    # Agent Teams: mark stale runs interrupted after a restart so the panel
    # can offer resume (spec §6.5). Guarded so startup never fails on it.
    try:
        await services.agent_team_runs.boot_sweep()
    except Exception:  # noqa: BLE001
        logger.warning("agent team boot sweep failed", exc_info=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/agent_teams/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
ruff format astrbot/dashboard/ tests/agent_teams/
ruff check astrbot/dashboard/ tests/agent_teams/
git add astrbot/dashboard/api/agent_teams.py astrbot/dashboard/api/router.py astrbot/dashboard/api/app.py tests/agent_teams/test_agent_team_api.py
git commit -m "feat: add agent teams API routes and SSE stream"
```

---

### Task 11: Full verification pass

**Files:**
- No new files; verification only.

- [ ] **Step 1: Run the full backend test suite**

Run: `pytest tests/ -x -q`
Expected: PASS (all pre-existing suites plus `tests/agent_teams`)

- [ ] **Step 2: Lint and format the whole repo**

Run: `ruff format . && ruff check .`
Expected: no errors; if formatting fixes appear, commit them as `chore: apply ruff formatting`.

- [ ] **Step 3: Manual smoke (server via `uv run main.py`, dashboard via `cd dashboard && pnpm dev`)**

1. Create a team via the legacy API with 3 members; verify three new WebChat sessions appear and each carries its bound persona.
2. Save a 3-node workflow; start a run with `mode=dag`; verify each member session receives its task and replies in the WebChat page, and `GET /api/agent_teams/runs/active` lists the run.
3. Kill the server mid-run and restart: verify the run row is `interrupted`, then `POST /api/agent_teams/runs/{id}/resume` completes it with done nodes preserved (spot-check a member session's history shows no duplicate completed turns).

- [ ] **Step 4: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: agent teams backend verification fixes"
```

---

## Plan Notes

- **Out of scope (Plans 2/3):** frontend panel/editor (spec §8); `AutoOrchestrator` + `AgentTeamToolRegistry` + `team_dispatch`/`team_finish` tools (spec §6.4); real `on_member_stop` wiring to `POST /chat/sessions/{id}/stop`; Collab retirement (spec §2.2).
- The `team_context` extra set by the adapter (Task 6) is consumed by Plan 3's coordinator flow; DAG nodes deliver `context=None` in this plan. A member's stored bare `system_prompt` is likewise injected per-turn in Plan 3's coordinator context assembly — DAG-mode members should use personas in v1.
- `DAGRunner.run()` is intentionally re-callable: `retry_node`/`skip_node`/`resume_run` put nodes back to `pending` and callers spawn a new `run()` task (see Task 8's `retry_node`/`skip_node` service wrappers).
- After each task: `ruff format` + `ruff check` must be clean; keep imports exact (`ruff` flags unused ones).
