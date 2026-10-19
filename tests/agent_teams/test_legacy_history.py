"""Read legacy history through shared services and durable SQLite storage."""

import asyncio

import pytest
import pytest_asyncio

from astrbot.core.agent_teams.assets import RoleAssetService
from astrbot.core.agent_teams.workflows import WorkflowService
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.dashboard.services.agent_team_run_service import AgentTeamRunService


@pytest_asyncio.fixture
async def history_env(tmp_path):
    """Provide shared public history services over an isolated real database.

    Args:
        tmp_path: Isolated test runtime directory.

    Yields:
        Database, history service and v2 service for public behavior scenarios.
    """
    from astrbot.core.agent_teams.history import RunHistoryService

    db = SQLiteDatabase(str(tmp_path / "history.db"))

    async def catalog():
        """Return the controlled external resource inventory.

        Returns:
            One available local model and profile for normal readiness checks.
        """
        return {
            "profiles": [{"id": "default"}],
            "models": [{"id": "local-model"}],
            "personas": [],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        }

    workflows = WorkflowService(db, RoleAssetService(db, catalog_loader=catalog))
    legacy = AgentTeamRunService(db, None)
    try:
        yield db, RunHistoryService(db, workflows, legacy), workflows
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("sample", range(12))
async def test_owned_paused_history_remains_resumable_in_the_original_engine(
    history_env,
    sample,
):
    """Keep a naturally parked legacy run distinct from lost-process history.

    Args:
        history_env: Real database and shared public history services.
        sample: One of twelve predeclared normal-completion observations.
    """
    from astrbot.core.agent_team_tools import AgentTeamToolRegistry
    from astrbot.core.agent_teams.history import RunHistoryService
    from astrbot.dashboard.services.agent_team_ports import TeamPorts

    db, _, workflows = history_env
    legacy = AgentTeamRunService(db, None)
    history = RunHistoryService(db, workflows, legacy)
    coordinator_umo = "webchat:FriendMessage:paused-lead"
    delivered = []
    finish_on_next = False
    final_response_waiting = asyncio.Event()
    release_final_response = asyncio.Event()

    async def deliver(session_id, text, context=None, **kwargs):
        """Supply an external coordinator response, finishing after public resume.

        Args:
            session_id: Original member session receiving the model request.
            text: Coordinator prompt supplied by the legacy engine.
            context: Optional request context supplied by the engine.
            **kwargs: Other delivery options supplied by the public port.

        Returns:
            Identity consumed by the corresponding response collection.
        """
        delivered.append(session_id)
        if finish_on_next:
            tools = AgentTeamToolRegistry.get_tools(coordinator_umo)
            await next(tool for tool in tools if tool.name == "team_finish").call(
                None, summary="Completed after original engine resume"
            )
        return f"message-{len(delivered)}"

    async def collect(session_id, message_id, member_id=None, **kwargs):
        """Return a controlled model reply without dispatching a member task.

        Args:
            session_id: Original member session whose response is collected.
            message_id: Identity returned by the delivery port.
            member_id: Optional member association supplied by the engine.
            **kwargs: Other collection options supplied by the public port.

        Returns:
            Original response text and its empty process-parts collection.
        """
        if finish_on_next:
            final_response_waiting.set()
            await release_final_response.wait()
        return "No dispatch in this model response", []

    legacy.ports_factory = lambda username, emit: TeamPorts(
        deliver, collect, lambda session_id: False, emit
    )
    await db.create_agent_team(
        team_id="paused-team",
        owner_username="alice",
        name="Original paused team",
        coordinator_member_id="lead",
        config={"max_rounds": 20, "reply_timeout": 5},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "session_id": "paused-lead",
                "umo": coordinator_umo,
                "system_prompt": "Coordinate",
            },
            {
                "member_id": "writer",
                "name": "Writer",
                "session_id": "paused-writer",
                "umo": "webchat:FriendMessage:paused-writer",
                "system_prompt": "Write",
            },
        ],
    )
    run = await legacy.start_run(
        "alice",
        "paused-team",
        {"mode": "auto", "input": f"Pause without dispatch {sample}"},
    )
    run_id = run["run_id"]
    for _ in range(250):
        observed = await history.get_record("alice", "legacy_run", run_id)
        if (
            observed["run"]["status"] == "paused"
            and not observed["run"]["execution_active"]
        ):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("The original auto run did not naturally park")

    record = await history.get_record("alice", "legacy_run", run_id)
    assert len(delivered) == 2
    assert record["read_only"] is True
    assert record["run"]["status"] == "paused"
    assert record["run"]["execution_active"] is False
    assert record["run"]["history_status"] == "paused"
    assert (await history.list_records("alice"))["records"][0]["status"] == "paused"
    assert any(
        item["run_id"] == run_id
        for item in (await legacy.list_active_runs("alice"))["runs"]
    )

    finish_on_next = True
    await legacy.resume_run("alice", run_id)
    await asyncio.wait_for(final_response_waiting.wait(), 5)

    async def observe_completion():
        """Check every public history observation through normal completion.

        Returns:
            The original durable completed history record.
        """
        for _ in range(250):
            observed = await history.get_record("alice", "legacy_run", run_id)
            assert observed["run"]["history_status"] != "interrupted"
            if observed["run"]["status"] == "completed":
                return observed
            await asyncio.sleep(0)
        pytest.fail("The original run did not persist its completion")

    readers = [asyncio.create_task(observe_completion()) for _ in range(3)]
    await asyncio.sleep(0)
    release_final_response.set()
    completed = await asyncio.wait_for(
        asyncio.gather(*readers, return_exceptions=True), 10
    )
    assert len(delivered) == 3
    for observed in completed:
        if isinstance(observed, BaseException):
            raise observed
        assert observed["run"]["history_status"] == "completed"
        assert (
            observed["run"]["result_summary"]
            == "Completed after original engine resume"
        )


@pytest.mark.asyncio
async def test_legacy_result_keeps_its_source_after_reopening_database(tmp_path):
    """Read saved results and original transcript associations after reopening.

    Args:
        tmp_path: Isolated durable SQLite directory.
    """
    db_path = tmp_path / "history.db"
    db = SQLiteDatabase(str(db_path))
    await db.create_agent_team(
        team_id="old-team",
        owner_username="alice",
        name="Research team",
        coordinator_member_id="lead",
        config={},
        members=[],
    )
    await db.create_agent_team_run(
        run_id="old-run",
        team_id="old-team",
        workflow_id="old-workflow",
        mode="dag",
        input="Original research goal",
        status="completed",
        graph_snapshot={
            "nodes": [{"id": "research", "member_id": "writer"}],
            "edges": [],
        },
        node_states={
            "research": {
                "member_id": "writer",
                "status": "done",
                "result": "Full original evidence",
            }
        },
        rounds=[],
    )
    await db.update_agent_team_run("old-run", result_summary="Original final report")
    await db.append_agent_team_run_message(
        run_id="old-run",
        member_id="writer",
        node_id="research",
        round=None,
        turn_id="original-turn",
        direction="reply",
        text="Full original evidence",
        parts=[{"type": "plain", "data": "Full original evidence"}],
        metadata=None,
    )
    await db.engine.dispose()
    db = SQLiteDatabase(str(db_path))
    workflows = WorkflowService(db, RoleAssetService(db))
    legacy = AgentTeamRunService(db, None)
    try:
        from astrbot.core.agent_teams.history import RunHistoryService

        history = RunHistoryService(db, workflows, legacy)
        record = await history.get_record("alice", "legacy_run", "old-run")
        assert record["engine"] == "legacy"
        assert record["read_only"] is True
        assert record["run"]["result_summary"] == "Original final report"
        assert (
            record["run"]["node_states"]["research"]["result"]
            == "Full original evidence"
        )
        assert record["source"] == {"kind": "legacy_run", "id": "old-run"}
        transcript = await history.get_process("alice", "old-run", "writer")
        assert transcript["messages"][0]["turn_id"] == "original-turn"
        assert transcript["messages"][0]["node_id"] == "research"
        assert (await workflows.list_runs("alice"))["runs"] == []
    finally:
        await workflows.shutdown()
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_long_term_history_requires_owned_member_session_and_preserves_raw_messages(
    history_env,
):
    """Keep original long-term messages behind both ownership checks.

    Args:
        history_env: Real database and shared public history services.
    """
    db, history, workflows = history_env
    await db.create_platform_session("alice", session_id="writer-session")
    await db.create_platform_session("bob", session_id="foreign-session")
    await db.create_agent_team(
        team_id="team",
        owner_username="alice",
        name="Legacy writers",
        coordinator_member_id="writer",
        config={},
        members=[
            {
                "member_id": "writer",
                "name": "Writer",
                "session_id": "writer-session",
                "umo": "webchat:FriendMessage:foreign-session",
            },
            {
                "member_id": "foreign",
                "name": "Foreign",
                "session_id": "foreign-session",
            },
        ],
    )
    messages = [
        {"role": "user", "content": "Original question"},
        {
            "role": "assistant",
            "content": "Original answer",
            "reasoning_content": "Actual saved reasoning",
            "tool_calls": [
                {
                    "id": "call-original",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-original",
            "content": "Original tool evidence",
        },
    ]
    await db.create_conversation(
        "webchat:FriendMessage:writer-session",
        "webchat",
        messages,
        title="Writer history",
        cid="owned-conversation",
    )
    await db.create_conversation(
        "webchat:FriendMessage:foreign-session",
        "webchat",
        [{"role": "user", "content": "Bob's secret"}],
        cid="foreign-conversation",
    )
    records = await history.list_records("alice", limit=1)
    assert [r["source"] for r in records["records"]] == [
        {"kind": "legacy_conversation", "id": "owned-conversation"}
    ]
    record = await history.get_record(
        "alice", "legacy_conversation", "owned-conversation", limit=2
    )
    assert record["conversation"]["messages"] == messages[:2]
    assert record["conversation"]["next_offset"] == 2
    last = await history.get_record(
        "alice", "legacy_conversation", "owned-conversation", offset=2, limit=2
    )
    assert last["conversation"]["messages"] == messages[2:]
    assert last["conversation"]["next_offset"] is None
    assert record["source_links"] == [
        {
            "team_id": "team",
            "team_name": "Legacy writers",
            "member_id": "writer",
            "member_name": "Writer",
            "session_id": "writer-session",
        }
    ]
    from astrbot.core.agent_teams.assets import RoleAssetError

    for user, identity in [
        ("bob", "owned-conversation"),
        ("alice", "foreign-conversation"),
    ]:
        with pytest.raises(RoleAssetError) as error:
            await history.get_record(user, "legacy_conversation", identity)
        assert error.value.status == 404
    assert (await history.list_records("nobody"))["records"] == []
    assert (await workflows.list_runs("alice"))["runs"] == []


@pytest.mark.asyncio
async def test_unified_history_pages_owned_old_and_new_sources_without_converting_old_runs(
    history_env,
):
    """Page distinct engine sources without changing the original saved status.

    Args:
        history_env: Real database and shared public history services.
    """
    db, history, workflows = history_env
    role = await workflows.roles.save_role(
        "alice",
        {
            "definition": {"name": "Writer", "prompt": "Write."},
            "installation": {"bindings": {"model": "local-model"}},
        },
    )
    asset = await workflows.save_asset(
        "alice",
        {
            "definition": {
                "name": "New report",
                "role_id": role["role_id"],
                "role_version": 1,
                "task": "Fresh goal",
            }
        },
    )
    new = await workflows.start_run("alice", asset["asset_id"], {"version": 1})
    await db.create_agent_team(
        team_id="old-team",
        owner_username="alice",
        name="Old report",
        coordinator_member_id="writer",
        config={},
        members=[],
    )
    await db.create_agent_team_run(
        run_id="old-run",
        team_id="old-team",
        workflow_id=None,
        mode="auto",
        input="Old goal",
        status="running",
        graph_snapshot={},
        node_states={},
        rounds=[],
    )
    pages, offset = [], 0
    while offset is not None:
        page = await history.list_records("alice", limit=1, offset=offset)
        pages.extend(page["records"])
        offset = page["next_offset"]
    assert [item["source"] for item in pages] == [
        {"kind": "legacy_run", "id": "old-run"},
        {"kind": "v2_run", "id": new["run_id"]},
    ]
    assert pages[0]["status"] == "interrupted"
    assert (await history.get_record("alice", "legacy_run", "old-run"))["run"][
        "status"
    ] == "running"
    detail = await history.get_record("alice", "v2_run", new["run_id"])
    assert detail["run"]["snapshot"]["asset_id"] == asset["asset_id"]
    assert (await history.list_records("bob"))["records"] == []
    from astrbot.core.agent_teams.assets import RoleAssetError

    for kind, identity in [("legacy_run", "old-run"), ("v2_run", new["run_id"])]:
        with pytest.raises(RoleAssetError) as error:
            await history.get_record("bob", kind, identity)
        assert error.value.status == 404
    with pytest.raises(RoleAssetError):
        await workflows.recover_run("alice", "old-run")
    assert len((await workflows.list_runs("alice"))["runs"]) == 1


@pytest.mark.asyncio
async def test_history_locates_migrated_asset_for_a_fresh_run_without_old_context(
    history_env,
):
    """Locate the current migrated definition and start an independent context.

    Args:
        history_env: Real database and shared public history services.
    """
    from astrbot.core.agent_teams.migration import LegacyMigrationService

    db, history, workflows = history_env
    await db.create_platform_session("alice", session_id="old-session")
    await db.create_agent_team(
        team_id="team",
        owner_username="alice",
        name="Old team",
        coordinator_member_id="lead",
        config={},
        members=[
            {
                "member_id": "lead",
                "name": "Lead",
                "session_id": "old-session",
                "provider_id": "local-model",
                "system_prompt": "Coordinate.",
            },
            {
                "member_id": "writer",
                "name": "Writer",
                "provider_id": "local-model",
                "system_prompt": "Write.",
            },
        ],
    )
    await db.create_conversation(
        "webchat:FriendMessage:old-session",
        "webchat",
        [{"role": "user", "content": "Previous private history"}],
        cid="old-conversation",
    )
    await db.create_agent_team_workflow(
        workflow_id="flow",
        team_id="team",
        name="Report",
        graph={
            "nodes": [
                {"id": "write", "member_id": "writer", "task": "Write {{input}}"}
            ],
            "edges": [],
        },
        layout={},
    )
    await db.create_agent_team_run(
        run_id="old-run",
        team_id="team",
        workflow_id="flow",
        mode="dag",
        input="Previous goal",
        status="interrupted",
        graph_snapshot={},
        node_states={},
        rounds=[],
    )
    migration = LegacyMigrationService(db, workflows.roles, workflows)
    preview = await migration.preview("alice", "team")
    migrated = await migration.confirm(
        "alice",
        "team",
        {"preview_token": preview["preview_token"], "save_pending": True},
    )
    record = await history.get_record("alice", "legacy_run", "old-run")
    target = record["migrated_assets"][0]
    assert target["asset_id"] == migrated["mapping"]["workflows"]["flow"]["asset_id"]
    new = await workflows.start_run(
        "alice",
        target["asset_id"],
        {"version": target["version"], "inputs": {"input": "Fresh goal"}},
    )
    assert "Previous private history" not in str(new)
    assert "old-session" not in str(new["snapshot"])
    old = await history.get_record("alice", "legacy_conversation", "old-conversation")
    assert old["conversation"]["messages"] == [
        {"role": "user", "content": "Previous private history"}
    ]
    asset = await workflows.get_asset("alice", target["asset_id"])
    await workflows.save_asset(
        "alice",
        {
            "definition": {**asset["definition"], "name": "Reviewed current report"},
            "expected_version": 1,
        },
        target["asset_id"],
    )
    current = await history.get_record("alice", "legacy_run", "old-run")
    assert current["migrated_assets"][0]["version"] == 2
    assert current["migrated_assets"][0]["name"] == "Reviewed current report"


@pytest.mark.asyncio
async def test_http_history_scopes_sources_and_pages_original_process(history_env):
    """Enforce caller ownership and original process cursors at the HTTP entry.

    Args:
        history_env: Real database and shared public history services.
    """
    from types import SimpleNamespace

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from astrbot.dashboard.api.auth import require_dashboard_user
    from astrbot.dashboard.api.workflow_assets import router

    db, history, _ = history_env
    await db.create_agent_team(
        team_id="team",
        owner_username="alice",
        name="Old report",
        coordinator_member_id="writer",
        config={},
        members=[],
    )
    await db.create_agent_team_run(
        run_id="old-run",
        team_id="team",
        workflow_id=None,
        mode="auto",
        input="Old goal",
        status="completed",
        graph_snapshot={},
        node_states={},
        rounds=[],
    )
    for index in range(3):
        await db.append_agent_team_run_message(
            run_id="old-run",
            member_id="writer",
            node_id=None,
            round=1,
            turn_id=f"turn-{index}",
            direction="reply",
            text=f"Response {index}",
            parts=[{"type": "think", "text": "Actual thinking"}],
            metadata=None,
        )
    app = FastAPI()
    app.state.services = SimpleNamespace(run_history=history)
    app.include_router(router, prefix="/api/v1")
    caller = {"name": "alice"}
    app.dependency_overrides[require_dashboard_user] = lambda: caller["name"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/api/v1/agent_history", params={"limit": 1})
        assert page.status_code == 200
        assert page.json()["data"]["records"][0]["source"] == {
            "kind": "legacy_run",
            "id": "old-run",
        }
        process = await client.get(
            "/api/v1/agent_history/legacy_run/old-run/members/writer",
            params={"limit": 2},
        )
        first = process.json()["data"]
        assert [message["text"] for message in first["messages"]] == [
            "Response 1",
            "Response 2",
        ]
        last = await client.get(
            "/api/v1/agent_history/legacy_run/old-run/members/writer",
            params={"limit": 2, "before_id": first["next_before_id"]},
        )
        assert [message["text"] for message in last.json()["data"]["messages"]] == [
            "Response 0"
        ]
        assert last.json()["data"]["next_before_id"] is None
        assert (
            await client.post("/api/v1/agent_history/legacy_run/old-run")
        ).status_code == 405
        assert (
            await client.get("/api/v1/agent_history", params={"limit": 101})
        ).status_code == 422
        caller["name"] = "bob"
        assert (await client.get("/api/v1/agent_history")).json()["data"][
            "records"
        ] == []
        assert (
            await client.get("/api/v1/agent_history/legacy_run/old-run")
        ).status_code == 404
        assert (
            await client.get("/api/v1/agent_history/legacy_run/old-run/members/writer")
        ).status_code == 404
