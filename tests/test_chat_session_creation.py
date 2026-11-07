"""Independent ordinary session creation through the real ChatUI tool path."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlmodel import select

from astrbot.builtin_stars.session_messages.tools import (
    CreateChatSessionTool,
    ListChatSessionsTool,
    SessionMessageChain,
)
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import current_tool_invocation
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.db.po import ChatUIProject, DashboardAccount
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.utils.webchat_access import DashboardPrincipal
from tests.test_chat_session_messages import (
    ControlledProvider,
    tool_call,
    wait_until,
)
from tests.test_chat_session_messages import (
    account_dashboard as account_dashboard,
)
from tests.test_chat_session_messages import (
    fake_core_lifecycle as fake_core_lifecycle,
)
from tests.test_chat_session_messages import (
    messaging as messaging,
)
from tests.test_chat_session_messages import (
    ordinary_chat as ordinary_chat,
)


@pytest_asyncio.fixture
async def creation(messaging):
    """Prepare a trusted execution with a live one-turn model override.

    Args:
        messaging: Isolated production SQLite, HTTP, tools and Agent pipeline.

    Returns:
        Messaging fixture, tool context and a task-local invocation function.
    """
    m = messaging

    async def respond(kwargs):
        return LLMResponse(role="assistant", completion_text="Created session response")

    provider = ControlledProvider("gpt-mini", respond)
    m.providers["gpt-mini"] = provider
    m.core.provider_manager.inst_map.update(m.providers)
    for config in m.core.provider_manager.providers_config:
        if config["id"] == "gpt-mini":
            config["model"] = "gpt-mini"
    account = DashboardPrincipal(
        m.user["account_id"], m.user["resource_owner"], "user", m.user["username"]
    )
    event = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                m.ids[0],
                {
                    "message": [{"type": "plain", "text": "source secret history"}],
                    "message_id": "source-create",
                    "_dashboard_account": account,
                    "thinking_effort": "xhigh",
                },
            )
        )
    )
    context = ContextWrapper(
        context=AstrAgentContext(
            context=m.context,
            event=event,
            extra={
                "main_agent_runner": SimpleNamespace(
                    provider=provider,
                    req=SimpleNamespace(
                        model="gpt-mini", llm_params={"thinking_effort": "xhigh"}
                    ),
                )
            },
        )
    )

    async def invoke(payload, identity=None):
        """Use a fresh real invocation per task unless deliberately retrying.

        Args:
            payload: Public tool arguments.
            identity: Server invocation identity for an intentional retry.

        Returns:
            Parsed creation result, or the tool's rejection string.
        """
        token = current_tool_invocation.set(identity or uuid.uuid4().hex)
        try:
            result = await CreateChatSessionTool().call(context, **payload)
            return result if result.startswith("error:") else json.loads(result)
        finally:
            current_tool_invocation.reset(token)

    return m, context, invoke


@pytest.mark.asyncio
@pytest.mark.parametrize("project_mode", ["inherit", "none", "explicit", "name"])
async def test_create_only_inherits_effective_settings_and_lists_empty_session(
    creation, project_mode
):
    """Creation persists the current execution's choices and no source history.

    Args:
        creation: Trusted execution and creation tool.
        project_mode: Inherited, explicitly absent, ID-selected or name-selected project.
    """
    m, context, invoke = creation
    owner = m.user["resource_owner"]
    projects = m.server.asgi_app.state.services.chat_projects
    project = await projects.create_project(owner, {"title": "Source project"})
    await projects.add_session_to_project(
        owner, {"session_id": m.ids[0], "project_id": project["project_id"]}
    )
    await m.service.session_selection(
        owner, m.ids[0], {"selected_provider": "gpt-mini", "thinking_effort": "low"}
    )
    runtime = ControlledProvider("runtime-provider", m.providers["gpt-mini"].respond)
    m.providers["runtime-provider"] = runtime
    m.core.provider_manager.inst_map["runtime-provider"] = runtime
    m.core.provider_manager.providers_config.append(
        {
            "id": "runtime-provider",
            "enable": True,
            "provider_source_id": "openai-source",
            "model": "runtime-provider",
        }
    )
    context.context.extra["main_agent_runner"].provider = runtime
    context.context.extra["main_agent_runner"].req.model = "runtime-provider"
    m.core.astrbot_config_mgr.confs["inherited"] = dict(m.core.astrbot_config)
    await m.service.session_config(owner, m.ids[0], "inherited")
    payload = {"title": "Independent B"}
    expected_project = project["project_id"]
    expected_name = "Source project"
    if project_mode == "none":
        payload["project_id"] = None
        expected_project = expected_name = None
    elif project_mode in ("explicit", "name"):
        other = await projects.create_project(owner, {"title": "Other project"})
        expected_project = other["project_id"]
        expected_name = "Other project"
        payload["project_id" if project_mode == "explicit" else "project_name"] = (
            expected_project if project_mode == "explicit" else "Other project"
        )
    queue = webchat_queue_mgr.subscribe_system(m.ids[0])
    try:
        result = await invoke(payload)
        assert result["status"] == "created"
        sid = result["session_id"]
        m.ids.append(sid)
        assert result["project_id"] == expected_project
        assert result["project_name"] == expected_name
        assert result["selection"]["thinking_effort"] == "xhigh"
        assert result["selection"]["selected_model"] == "runtime-provider"
        assert result["selection"]["selected_provider"] == "runtime-provider"
        assert not runtime.calls
        assert result["config_id"] == "inherited"
        assert not m.providers["gpt-mini"].calls
        assert not m.events
        assert (await queue.get())["type"] == "chat_session_created"
        detail = (
            await m.client.get(f"/api/v1/chat/sessions/{sid}", headers=m.headers)
        ).json()["data"]
        assert detail["history"] == []
        assert (await m.service.session_config(owner, sid))["config_id"] == "inherited"
        listed = (
            await projects.get_project_sessions(owner, expected_project)
            if expected_project
            else await m.service.get_sessions(owner, "webchat")
        )
        assert any(
            item["session_id"] == sid and item["display_name"] == "Independent B"
            for item in listed
        )
        assert (await m.service.session_selection(owner, m.ids[0]))[
            "thinking_effort"
        ] == "low"
    finally:
        webchat_queue_mgr.unsubscribe_system(m.ids[0], queue)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "foreign_project",
        "missing_project",
        "provider",
        "model",
        "profile",
        "identity",
        "disabled_account",
        "extra",
        "title",
    ],
)
async def test_creation_rejects_unavailable_resources_without_allocating(
    creation, invalid
):
    """Validate identity and resources before creating anything.

    Args:
        creation: Trusted execution and creation tool.
        invalid: Resource, account, or public argument rejection case.
    """
    m, context, invoke = creation
    payload = {"title": "Must not exist"}
    if invalid == "foreign_project":
        project = await m.server.asgi_app.state.services.chat_projects.create_project(
            m.foreign["resource_owner"], {"title": "Foreign"}
        )
        payload["project_id"] = project["project_id"]
    elif invalid == "missing_project":
        payload["project_id"] = "missing"
    elif invalid == "provider":
        payload["selected_provider"] = "missing"
    elif invalid == "model":
        payload["selected_model"] = "unauthorized-model"
    elif invalid == "profile":
        await m.core.umop_config_router.update_route(
            context.context.event.unified_msg_origin, "missing"
        )
    elif invalid == "identity":
        context.context.event.set_extra("_dashboard_account", None)
    elif invalid == "disabled_account":
        async with m.db.get_db() as connection:
            account = await connection.get(DashboardAccount, m.user["account_id"])
            account.enabled = False
            await connection.commit()
    elif invalid == "extra":
        payload["idempotency_key"] = "model-forged"
    else:
        payload["title"] = " "
    result = await invoke(payload)
    assert result.startswith("error:"), result
    assert len(await m.service.get_sessions(m.user["resource_owner"], "webchat")) == 2


@pytest.mark.asyncio
async def test_config_failure_cleans_session_route_and_preferences(
    creation, monkeypatch
):
    """A failure after preference writes leaves no incomplete session.

    Args:
        creation: Trusted execution and creation tool.
        monkeypatch: Fails project assignment after configuration is persisted.
    """
    from astrbot.dashboard.services.chatui_project_service import (
        ChatUIProjectService,
        ChatUIProjectServiceError,
    )

    m, context, invoke = creation
    project = await ChatUIProjectService(m.db).create_project(
        m.user["resource_owner"], {"title": "Project"}
    )
    allocated = []
    original = m.service.new_session

    async def allocate(*args):
        result = await original(*args)
        allocated.append(result["session_id"])
        return result

    monkeypatch.setattr(m.service, "new_session", allocate)
    monkeypatch.setattr(
        ChatUIProjectService,
        "add_session_to_project",
        AsyncMock(side_effect=ChatUIProjectServiceError("assignment failed")),
    )
    assert "assignment failed" in await invoke(
        {"title": "Rollback", "project_id": project["project_id"]}
    )
    sid = allocated[0]
    assert await m.db.get_platform_session_by_id(sid) is None
    assert await m.db.get_preference("webchat_session", sid, "model_selection") is None
    assert not any(sid in key for key in m.core.umop_config_router.umop_to_conf_id)


@pytest.mark.asyncio
async def test_real_invocation_retry_concurrency_and_timeout(creation, monkeypatch):
    """Retry joins one creation while independent concurrent calls remain distinct.

    Args:
        creation: Trusted execution and creation tool.
        monkeypatch: Holds allocation while callers retry or time out.
    """
    m, context, invoke = creation
    entered, release = asyncio.Event(), asyncio.Event()
    original = m.service.new_session

    async def allocate(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(m.service, "new_session", allocate)
    first = asyncio.create_task(
        invoke({"title": "Retry B", "text": "First message"}, "real-call")
    )
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    retry = asyncio.create_task(
        invoke({"title": "Retry B", "text": "First message"}, "real-call")
    )
    independent = asyncio.create_task(invoke({"title": "Independent C"}))
    release.set()
    b, c = await asyncio.gather(retry, independent)
    m.ids.extend([b["session_id"], c["session_id"]])
    assert b["session_id"] != c["session_id"]
    assert await invoke({"title": "Retry B", "text": "First message"}, "real-call") == b
    assert len(await m.service.get_sessions(m.user["resource_owner"], "webchat")) == 4
    await wait_until(lambda: not m.service.chat_runs_by_session)
    assert len(m.events) == 1
    assert context.context.event.get_extra("_session_message_chain").count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["limit", "stopped", "admission"])
async def test_send_failure_returns_reusable_created_id_and_preserves_chain(
    creation, monkeypatch, failure
):
    """First-message rejection keeps the configured session and shared send budget.

    Args:
        creation: Trusted execution and creation tool.
        monkeypatch: Rejects only first-message admission.
        failure: Exhausted budget, stopped source, or admission exception.
    """
    m, context, invoke = creation
    event = context.context.event
    chain = SessionMessageChain(
        count=8 if failure == "limit" else 0, sessions={m.ids[0]}
    )
    event.set_extra("_session_message_chain", chain)
    if failure == "stopped":
        chain.stopped.add(m.ids[0])
    original = m.service.build_chat_stream
    if failure == "admission":
        monkeypatch.setattr(
            m.service,
            "build_chat_stream",
            AsyncMock(side_effect=RuntimeError("admission unavailable")),
        )
    result = await invoke({"title": "Reusable B", "text": "first"}, "failed-first-send")
    assert (
        await invoke({"title": "Reusable B", "text": "first"}, "failed-first-send")
        == result
    )
    assert result["status"] == "created_send_failed"
    assert result["send_error"]
    assert "send" not in result
    sid = result["session_id"]
    m.ids.append(sid)
    assert await m.db.get_platform_session_by_id(sid) is not None
    assert event.get_extra("_session_message_chain") is chain
    assert not m.events
    monkeypatch.setattr(m.service, "build_chat_stream", original)
    # A later real human turn starts a fresh event and may reuse this exact B.
    event.set_extra("_session_message_chain", None)
    accepted = await m.service.send_session_message(event, sid, "valid later input")
    assert accepted["session_id"] == sid
    await wait_until(lambda: not m.service.chat_runs_by_session)
    assert len(m.events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("project_selector", ["none", "name"])
async def test_main_agent_creates_sends_continues_and_receives_reply(
    creation, project_selector
):
    """Real tools create B, send first text, continue from A and reply back.

    Args:
        creation: Isolated production main Agent, history, admission and tools.
        project_selector: Explicitly ungrouped or named-project creation.
    """
    m, context, invoke = creation
    project = await m.server.asgi_app.state.services.chat_projects.create_project(
        m.user["resource_owner"], {"title": "Named project"}
    )
    a = m.ids[0]
    created = []
    b_calls = 0

    async def respond_a(kwargs):
        history = kwargs.get("contexts", [])
        text = json.dumps(history)
        if "later reply from B" in text:
            return LLMResponse(role="assistant", completion_text="A received the reply")
        results = [row for row in history if row.get("role") == "tool"]
        if not results:
            return tool_call(
                "create_chat_session",
                {
                    "title": "Agent-created B",
                    **(
                        {"project_id": None}
                        if project_selector == "none"
                        else {"project_name": "Named project"}
                    ),
                    "thinking_effort": "high",
                    "text": "first input to B",
                },
            )
        result = json.loads(results[-1]["content"])
        if not created:
            created.append(result)
        return LLMResponse(role="assistant", completion_text="B created")

    async def respond_b(kwargs):
        nonlocal b_calls
        b_calls += 1
        text = json.dumps(kwargs.get("contexts", []))
        assert "source secret history" not in text
        assert kwargs["llm_params"]["thinking_effort"] == "high"
        if b_calls == 2:
            return tool_call(
                "send_chat_message", {"session_id": a, "text": "later reply from B"}
            )
        return LLMResponse(role="assistant", completion_text="B response")

    m.providers["gpt-mini"].respond = respond_a
    recipient = ControlledProvider("recipient", respond_b)
    m.providers["recipient"] = recipient
    m.core.provider_manager.inst_map["recipient"] = recipient
    m.core.provider_manager.providers_config.append(
        {
            "id": "recipient",
            "enable": True,
            "provider_source_id": "openai-source",
            "model": "recipient",
        }
    )
    original_a = respond_a

    async def explicit_b(kwargs):
        result = await original_a(kwargs)
        if result.tools_call_name == ["create_chat_session"]:
            result.tools_call_args[0]["selected_provider"] = "recipient"
        return result

    m.providers["gpt-mini"].respond = explicit_b
    response = await m.client.post(
        "/api/v1/chat",
        headers=m.headers,
        json={"session_id": a, "message": "source secret history"},
    )
    assert response.status_code == 200
    await wait_until(lambda: bool(created) and not m.service.chat_runs_by_session)
    result = created[0]
    assert result["send"]["status"] == "accepted"
    assert result["project_id"] == (
        None if project_selector == "none" else project["project_id"]
    )
    assert result["project_name"] == (
        None if project_selector == "none" else "Named project"
    )
    b = result["session_id"]
    m.ids.append(b)
    await m.service.send_session_message(m.events[0], b, "second input to B")
    await wait_until(lambda: b_calls == 3 and not m.service.chat_runs_by_session)
    for sid in (a, b):
        history = (
            await m.client.get(f"/api/v1/chat/sessions/{sid}", headers=m.headers)
        ).json()["data"]["history"]
        assert any(row["content"].get("agent_source") for row in history)
        assert any(row["content"]["type"] == "bot" for row in history)
    assert m.events[0].get_extra("_session_message_chain").count == 3
    # Human continuation uses B's saved model and effort through normal admission.
    response = await m.client.post(
        "/api/v1/chat",
        headers=m.headers,
        json={"session_id": b, "message": "human continuation"},
    )
    assert response.status_code == 200
    assert b_calls == 4


@pytest.mark.asyncio
async def test_creation_notification_failure_does_not_lose_created_id(
    creation, monkeypatch
):
    """Publishing a sidebar hint cannot hide a successfully configured session.

    Args:
        creation: Trusted execution and creation tool.
        monkeypatch: Fails the optional sidebar notification.
    """
    m, context, invoke = creation
    monkeypatch.setattr(
        webchat_queue_mgr,
        "put_system_event",
        AsyncMock(side_effect=RuntimeError("notification unavailable")),
    )
    result = await invoke({"title": "Still created"}, "notification-call")
    assert result["status"] == "created"
    assert await m.db.get_platform_session_by_id(result["session_id"]) is not None
    assert await invoke({"title": "Still created"}, "notification-call") == result
    m.ids.append(result["session_id"])


@pytest.mark.asyncio
async def test_creation_requires_real_invocation_identity(creation):
    """Model arguments cannot substitute for the executor's invocation identity.

    Args:
        creation: Trusted execution without an active executor call scope.
    """
    m, context, invoke = creation
    assert current_tool_invocation.get() is None
    result = await CreateChatSessionTool().call(context, title="No identity")
    assert result.startswith("error:")
    assert len(await m.service.get_sessions(m.user["resource_owner"], "webchat")) == 2


@pytest.mark.asyncio
async def test_created_notice_reaches_existing_system_stream(creation):
    """The ordinary source stream forwards creation without creating a chat record.

    Args:
        creation: Production chat service and source execution.
    """
    m, context, invoke = creation
    stream = await m.service.build_system_stream(m.user["resource_owner"], m.ids[0])
    try:
        result = await invoke({"title": "Visible B"})
        m.ids.append(result["session_id"])
        frame = await asyncio.wait_for(anext(stream), 2)
        assert '"type": "chat_session_created"' in frame
        assert result["session_id"] in frame
        history = await m.service.get_sorted_platform_history(
            await m.db.get_platform_session_by_id(m.ids[0])
        )
        assert not history
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_list_tool_returns_folder_titles_without_per_session_project_queries(
    creation, monkeypatch
):
    """Use the joined project title without exposing another owner's association.

    Args:
        creation: Real SQLite and trusted tool execution.
        monkeypatch: Rejects additional project queries during discovery.
    """
    m, context, invoke = creation
    owner = m.user["resource_owner"]
    projects = m.server.asgi_app.state.services.chat_projects
    project = await projects.create_project(owner, {"title": "Research folder"})
    await projects.add_session_to_project(
        owner, {"session_id": m.ids[1], "project_id": project["project_id"]}
    )
    plain = await m.db.create_platform_session(creator=owner, display_name="No folder")
    foreign = await projects.create_project(
        m.foreign["resource_owner"], {"title": "Private foreign folder"}
    )
    foreign_session = await m.db.create_platform_session(
        creator=m.foreign["resource_owner"]
    )
    # A stale cross-owner relation must not disclose the foreign folder's ID or title.
    stale = await m.db.create_platform_session(
        creator=owner, display_name="Stale relation"
    )
    await m.db.add_session_to_project(stale.session_id, foreign["project_id"])
    archived = await m.db.create_platform_session(creator=owner)
    await m.db.update_platform_session(archived.session_id, archived=1)
    hidden = await m.db.create_platform_session(creator=owner, is_group=1)
    m.ids.extend(
        [plain.session_id, stale.session_id, archived.session_id, hidden.session_id]
    )
    monkeypatch.setattr(
        m.db,
        "get_chatui_project_by_id",
        AsyncMock(side_effect=AssertionError("Unexpected per-session project lookup")),
    )
    monkeypatch.setattr(
        m.db,
        "get_chatui_projects_by_creator",
        AsyncMock(side_effect=AssertionError("Discovery must reuse joined titles")),
    )
    result = json.loads(await ListChatSessionsTool().call(context))
    by_id = {item["session_id"]: item for item in result}
    assert set(by_id) == {m.ids[1], plain.session_id, stale.session_id}
    assert by_id[m.ids[1]] == {
        "session_id": m.ids[1],
        "display_name": "B",
        "project_id": project["project_id"],
        "project_name": "Research folder",
    }
    for sid in (plain.session_id, stale.session_id):
        assert by_id[sid]["project_id"] is None
        assert by_id[sid]["project_name"] is None
    assert foreign_session.session_id not in by_id
    assert foreign["project_id"] not in json.dumps(result)
    assert "Private foreign folder" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_message", [False, True])
async def test_create_by_name_finds_empty_owned_folder_and_retries_once(
    creation, first_message
):
    """Named selection overrides the source folder and ignores a foreign namesake.

    Args:
        creation: Real SQLite, main Agent, histories and creation tool.
        first_message: Whether to deliver an initial message through the shared send path.
    """
    m, context, invoke = creation
    owner = m.user["resource_owner"]
    projects = m.server.asgi_app.state.services.chat_projects
    source = await projects.create_project(owner, {"title": "Source folder"})
    await projects.add_session_to_project(
        owner, {"session_id": m.ids[0], "project_id": source["project_id"]}
    )
    foreign = await projects.create_project(
        m.foreign["resource_owner"], {"title": "研究项目"}
    )
    target = await projects.create_project(owner, {"title": "研究项目"})
    assert await projects.get_project_sessions(owner, target["project_id"]) == []
    payload = {"title": "Named B", "project_name": "  研究项目  "}
    if first_message:
        payload["text"] = "First input to the named folder"
    result = await invoke(payload, "create-in-named-folder")
    assert result["status"] == "created", result
    assert result["project_id"] == target["project_id"]
    assert result["project_name"] == "研究项目"
    assert await invoke(payload, "create-in-named-folder") == result
    m.ids.append(result["session_id"])
    assert [
        item["session_id"]
        for item in await projects.get_project_sessions(owner, target["project_id"])
    ] == [result["session_id"]]
    assert (
        await projects.get_project_sessions(
            m.foreign["resource_owner"], foreign["project_id"]
        )
        == []
    )
    _, total = await m.db.get_platform_sessions_by_creator_paginated(owner, "webchat")
    assert total == 3
    if first_message:
        assert result["send"]["status"] == "accepted"
        await wait_until(lambda: not m.service.chat_runs_by_session)
        assert len(m.events) == 1
        assert context.context.event.get_extra("_session_message_chain").count == 1
    else:
        assert "send" not in result
        assert not m.events
        assert not m.providers["gpt-mini"].calls


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_project_name_lookup_checks_all_owned_pages(creation, duplicate):
    """Find old empty projects and reject names duplicated beyond the first page.

    Args:
        creation: Real database and trusted creation tool.
        duplicate: Whether a newer project duplicates the oldest project's title.
    """
    m, context, invoke = creation
    owner = m.user["resource_owner"]
    projects = m.server.asgi_app.state.services.chat_projects
    target = await projects.create_project(owner, {"title": "Research folder"})
    await projects.add_session_to_project(
        owner, {"session_id": m.ids[0], "project_id": target["project_id"]}
    )
    async with m.db.get_db() as connection:
        row = (
            await connection.execute(
                select(ChatUIProject).where(
                    ChatUIProject.project_id == target["project_id"]
                )
            )
        ).scalar_one()
        row.updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        connection.add_all(
            [
                ChatUIProject(
                    creator=owner,
                    title=f"Filler {index}",
                    updated_at=datetime(2001, 1, 1, tzinfo=timezone.utc),
                )
                for index in range(100)
            ]
        )
        await connection.commit()
    first_page = await m.db.get_chatui_projects_by_creator(owner)
    assert len(first_page) == 100
    assert target["project_id"] not in {item.project_id for item in first_page}
    foreign = await projects.create_project(
        m.foreign["resource_owner"], {"title": "Research folder"}
    )
    if duplicate:
        second = await projects.create_project(owner, {"title": "Research folder"})
    result = await invoke({"title": "Named B", "project_name": "Research folder"})
    if duplicate:
        assert result.startswith("error: Project name is ambiguous"), result
        candidates = json.loads(result.split("use project_id from: ", 1)[1])
        assert set(candidates) == {target["project_id"], second["project_id"]}
        assert foreign["project_id"] not in result
        _, total = await m.db.get_platform_sessions_by_creator_paginated(
            owner, "webchat"
        )
        assert total == 2
        # The caller can resolve the ambiguity by using an advertised ID.
        result = await invoke(
            {"title": "ID-selected B", "project_id": target["project_id"]}
        )
    assert result["status"] == "created", result
    assert result["project_id"] == target["project_id"]
    assert result["project_name"] == "Research folder"
    m.ids.append(result["session_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector,reason",
    [
        ({"project_name": "Missing"}, "not found"),
        ({"project_name": "research folder"}, "not found"),
        ({"project_name": ""}, "nonempty string"),
        ({"project_name": "  "}, "nonempty string"),
        ({"project_name": None}, "nonempty string"),
        ({"project_name": 42}, "nonempty string"),
        ({"project_name": []}, "nonempty string"),
        (
            {"project_name": "Research folder", "project_id": "owned"},
            "mutually exclusive",
        ),
        ({"project_name": "Research folder", "project_id": None}, "mutually exclusive"),
    ],
)
async def test_invalid_project_name_rejects_before_allocating(
    creation, monkeypatch, selector, reason
):
    """Reject invalid, missing, foreign-only or conflicting names before allocation.

    Args:
        creation: Real SQLite and trusted creation tool.
        monkeypatch: Tracks allocation without replacing the real storage path.
        selector: Model-supplied name and optional conflicting project ID.
        reason: Required error explanation.
    """
    m, context, invoke = creation
    projects = m.server.asgi_app.state.services.chat_projects
    project = await projects.create_project(
        m.user["resource_owner"], {"title": "Research folder"}
    )
    foreign = await projects.create_project(
        m.foreign["resource_owner"], {"title": "Missing"}
    )
    payload = {"title": "Must not allocate", **selector}
    if payload.get("project_id") == "owned":
        payload["project_id"] = project["project_id"]
    allocate = AsyncMock(wraps=m.service.new_session)
    monkeypatch.setattr(m.service, "new_session", allocate)
    result = await invoke(payload)
    assert result.startswith("error:"), result
    assert reason in result
    assert foreign["project_id"] not in result
    allocate.assert_not_awaited()
    _, total = await m.db.get_platform_sessions_by_creator_paginated(
        m.user["resource_owner"], "webchat"
    )
    assert total == 2
