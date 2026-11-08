"""Remote creation and Agent tools against two isolated ordinary ChatUI apps."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from astrbot.builtin_stars.session_messages.tools import (
    CreateChatSessionTool,
    ListChatSessionsTool,
    ReadChatSessionTool,
)
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import current_tool_invocation
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.db.po import DashboardAccount
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.star.context import Context
from astrbot.core.utils.webchat_access import DashboardPrincipal
from tests.test_remote_chat_connections import (  # noqa: F401
    account_dashboard,
    fake_core_lifecycle,
    ordinary_chat,
)
from tests.test_remote_chat_connections import (
    connected_instances as connected_instances,
)


@pytest_asyncio.fixture
async def remote_tools(connected_instances):
    """Build a trusted source and two direct bindings to one remote account.

    Args:
        connected_instances: Independent production ASGI apps and SQLite databases.

    Returns:
        Instance fixture, tool context, connections and local authorization headers.
    """
    local, target, observed = connected_instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = target
    headers = {"Authorization": "Bearer " + users[0]["token"]}
    bindings = []
    for name in ("Desktop", "Desktop"):
        await client.post(
            "/api/v1/chat/remote-connections",
            headers=headers,
            json={
                "name": name,
                "base_url": "http://remote",
                "key": credential["key"],
            },
        )
    bindings = (
        await client.get("/api/v1/chat/remote-connections", headers=headers)
    ).json()["data"]["connections"]
    source = await db.create_platform_session(creator=users[0]["resource_owner"])
    await db.create_platform_session(
        creator=users[0]["resource_owner"], display_name="Local target"
    )
    principal = DashboardPrincipal(
        users[0]["account_id"], users[0]["resource_owner"], "user", users[0]["username"]
    )
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(
        await adapter.convert_message(
            (
                principal.resource_owner,
                source.session_id,
                {
                    "message": [{"type": "plain", "text": "Discover remote sessions"}],
                    "message_id": "source",
                    "_dashboard_account": principal,
                },
            )
        )
    )
    tool_context = object.__new__(Context)
    tool_context.session_message_service = server.asgi_app.state.services.chat
    context = ContextWrapper(
        context=AstrAgentContext(
            context=tool_context,
            event=event,
            extra={
                "main_agent_runner": SimpleNamespace(provider=None, req=None),
            },
        )
    )
    return connected_instances, context, bindings, headers


@pytest.mark.asyncio
async def test_human_creation_permissions_selected_visibility_and_first_message(
    remote_tools,
):
    instances, context, bindings, headers = remote_tools
    local, target, observed = instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = target
    remote_headers = {"Authorization": "Bearer " + remote_admin["token"]}
    await remote.put(
        "/api/v1/chat/remote-access/exposure",
        headers=remote_headers,
        json={"mode": "selected"},
    )
    path = "/api/v1/chat/remote-connections/" + bindings[0]["connection_id"]
    body = {
        "title": "Created remotely",
        "project_id": None,
        "revision": bindings[0]["revision"],
    }
    assert (
        remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] is False
    )
    denied = await client.post(path + "/sessions", headers=headers, json=body)
    assert (
        denied.status_code == 502 and denied.json()["data"]["code"] == "remote_disabled"
    )
    remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] = True
    projects = [
        await remote_db.create_chatui_project(
            creator=remote_admin["resource_owner"], title="Same folder"
        )
        for _ in range(2)
    ]
    options = (
        await client.get(
            path + "/creation",
            headers=headers,
            params={"revision": bindings[0]["revision"]},
        )
    ).json()["data"]
    assert options["allowed"] and len(options["projects"]) == 2
    body["project_id"] = projects[1].project_id
    rev = remote_server.asgi_app.state.services.remote_chat.grant_revisions.get(
        remote_admin["account_id"], 0
    )
    # Human access does not depend on either Agent switch.
    core.astrbot_config["session_messages"]["enable"] = False
    remote_core.astrbot_config["session_messages"]["enable"] = False
    created = await client.post(path + "/sessions", headers=headers, json=body)
    assert created.status_code == 200, created.text
    sid = created.json()["data"]["session_id"]
    assert (
        remote_server.asgi_app.state.services.remote_chat.grant_revisions[
            remote_admin["account_id"]
        ]
        > rev
    )
    for binding in bindings:
        rows = (
            await client.get(
                "/api/v1/chat/remote-connections/"
                + binding["connection_id"]
                + "/sessions",
                headers=headers,
            )
        ).json()["data"]
        assert any(
            row["session_id"] == sid and row["project_id"] == projects[1].project_id
            for row in rows
        )
    assert await db.get_platform_session_by_id(sid) is None
    concurrent = await asyncio.gather(
        *(
            client.post(
                path + "/sessions",
                headers=headers,
                json={**body, "title": f"Parallel {index}"},
            )
            for index in range(2)
        )
    )
    assert all(response.status_code == 200 for response in concurrent)
    visible = (await client.get(path + "/sessions", headers=headers)).json()["data"]
    assert {response.json()["data"]["session_id"] for response in concurrent} <= {
        row["session_id"] for row in visible
    }
    # Creation disabled again preserves history and the RC04 ordinary admission path.
    remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] = False
    from uuid import uuid4

    sent = await client.post(
        path + f"/sessions/{sid}/messages",
        headers=headers,
        json={
            "text": "First human input",
            "request_id": str(uuid4()),
            "revision": body["revision"],
        },
    )
    assert sent.json()["data"]["status"] == "accepted", sent.text
    from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
        webchat_queue_mgr,
    )

    item = await webchat_queue_mgr.get_or_create_queue(sid).get()
    assert item[1] == sid and item[2]["message"][0]["text"] == "First human input"
    await webchat_queue_mgr.get_or_create_back_queue(
        sent.json()["data"]["run_id"], sid
    ).put({"type": "end", "data": ""})
    assert (
        await client.get(path + f"/sessions/{sid}/history", headers=headers)
    ).status_code == 200
    assert (
        await client.post(path + "/sessions", headers=headers, json=body)
    ).status_code == 502
    ordinary = await remote.get(
        "/api/v1/chat/sessions/new",
        headers={"Authorization": "RemoteSession " + credential["key"]},
    )
    assert ordinary.status_code in (401, 403)
    other = {"Authorization": "Bearer " + users[1]["token"]}
    assert (
        await client.post(path + "/sessions", headers=other, json=body)
    ).status_code == 404


@pytest.mark.asyncio
async def test_agent_discovery_read_budget_creation_and_switches(remote_tools):
    instances, context, bindings, headers = remote_tools
    local, target, observed = instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = target
    remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] = True
    projects = [
        await remote_db.create_chatui_project(
            creator=remote_admin["resource_owner"], title="Same folder"
        )
        for _ in range(2)
    ]
    # A target binding to a third instance must never appear in discovery.
    async with remote_db.get_db() as connection:
        target_account = await connection.get(
            DashboardAccount, remote_admin["account_id"]
        )
    await remote_server.asgi_app.state.services.remote_chat.save_connection(
        target_account, "Third machine", "http://third", credential["key"]
    )
    binding = bindings[0]
    selector = {
        "connection_id": binding["connection_id"],
        "connection_revision": binding["revision"],
    }
    create = CreateChatSessionTool()

    async def invoke(**payload):
        """Call one real task-scoped Agent creation invocation.

        Args:
            **payload: Public creation arguments.

        Returns:
            Tool response text.
        """
        token = current_tool_invocation.set(("test", str(len(observed))))
        try:
            return await create.call(context, **payload)
        finally:
            current_tool_invocation.reset(token)

    ambiguous = await invoke(title="Ambiguous", project_name="Same folder", **selector)
    assert ambiguous.startswith("error:"), ambiguous
    result = await invoke(
        title="Agent remote session", project_id=projects[1].project_id, **selector
    )
    assert not result.startswith("error:"), result
    created = json.loads(result)
    sid = created["session_id"]
    rows = json.loads(await ListChatSessionsTool().call(context))
    found = [row for row in rows if row.get("session_id") == sid]
    assert len(found) == 2 and len({row["target_id"] for row in found}) == 2
    assert all(
        row["project_name"] == "Same folder"
        and row["project_id"] == projects[1].project_id
        for row in found
    )
    assert not any(row.get("connection_name") == "Third machine" for row in rows)
    assert any(
        row["display_name"] == "Local target" and row["connection_id"] is None
        for row in rows
    )
    newest = None
    for i in range(12):
        newest = await remote_db.insert_platform_message_history(
            "webchat",
            sid,
            {
                "type": "bot",
                "message": [
                    {"type": "plain", "text": str(i) + "\\" * 20000},
                    {
                        "type": "tool_call",
                        "tool_calls": [
                            {
                                "name": "example",
                                "args": "private arguments",
                                "result": "private tool result",
                            }
                        ],
                    },
                    {"type": "file", "filename": "notes.txt", "url": "/private/path"},
                    {"type": "private_context", "text": "private checkpoint"},
                ],
            },
        )
    read = ReadChatSessionTool()
    first = await read.call(
        context, session_id=sid, limit=10, max_chars_per_message=8000, **selector
    )
    assert (
        len(first) <= 40000
        and "private checkpoint" not in first
        and "/private/path" not in first
    )
    page = json.loads(first)
    assert page["has_more"] and page["response_budget_limited"]
    assert page["active_run_ids"] == []
    assert page["last_message_id"] == newest.id
    assert "private arguments" not in first and "private tool result" not in first
    older = json.loads(
        await read.call(
            context,
            session_id=sid,
            before_id=page["next_before_id"],
            limit=10,
            include_tool_results=True,
            max_chars_per_message=100,
            **selector,
        )
    )
    assert older["messages"][-1]["message_id"] < page["next_before_id"]
    assert older["last_message_id"] == newest.id
    hidden = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    await remote.put(
        "/api/v1/chat/remote-access/exposure",
        headers={"Authorization": "Bearer " + remote_admin["token"]},
        json={"mode": "selected", "session_ids": [sid]},
    )
    assert (
        await read.call(context, session_id=hidden.session_id, **selector)
    ).startswith("error:")
    await remote_db.insert_platform_message_history(
        "webchat",
        sid,
        {
            "type": "bot",
            "message": [
                {
                    "type": "tool_call",
                    "tool_calls": [
                        {
                            "name": "example",
                            "args": "visible args",
                            "result": "visible result",
                        }
                    ],
                },
            ],
        },
    )
    tool_page = await read.call(
        context, session_id=sid, limit=1, include_tool_results=True, **selector
    )
    assert "visible args" in tool_page and "visible result" in tool_page
    omitted = await read.call(context, session_id=sid, limit=1, **selector)
    assert "visible result" not in omitted
    foreign = await remote_db.create_platform_session(creator="other-account")
    assert (
        await read.call(context, session_id=foreign.session_id, **selector)
    ).startswith("error:")
    assert (await invoke(title="No implicit project", **selector)).startswith("error:")
    assert (
        await invoke(title="No send bypass", project_id=None, text="send", **selector)
    ).startswith("error:")
    remote_core.astrbot_config["session_messages"]["enable"] = False
    assert (await read.call(context, session_id=sid, **selector)).startswith("error:")
    assert (await invoke(title="Denied Agent", project_id=None, **selector)).startswith(
        "error:"
    )
    assert not any(
        row.get("connection_id")
        for row in json.loads(await ListChatSessionsTool().call(context))
    )
    remote_core.astrbot_config["session_messages"]["enable"] = True
    core.astrbot_config["session_messages"]["enable"] = False
    assert (await ListChatSessionsTool().call(context)).startswith("error:")
    assert (await read.call(context, session_id=sid, **selector)).startswith("error:")
    core.astrbot_config["session_messages"]["enable"] = True
    async with db.get_db() as connection:
        source_account = await connection.get(DashboardAccount, users[0]["account_id"])
    await server.asgi_app.state.services.remote_chat.save_connection(
        source_account, "Desktop", "http://remote", None, binding["connection_id"]
    )
    assert (await read.call(context, session_id=sid, **selector)).startswith("error:")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["agent", "remote", "account"])
async def test_discovery_rechecks_source_after_remote_io(
    remote_tools, monkeypatch, change
):
    instances, context, bindings, headers = remote_tools
    local, target, observed = instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = target
    await remote_db.create_platform_session(creator=remote_admin["resource_owner"])
    service = server.asgi_app.state.services.remote_chat
    original = service.read_connection

    async def change_after_response(*args, **kwargs):
        """Change source admission while a remote response is in flight.

        Args:
            *args: Ordinary direct binding read arguments.
            **kwargs: Bounded operation options.

        Returns:
            Remote response obtained before the source changed.
        """
        response = await original(*args, **kwargs)
        if change == "agent":
            core.astrbot_config["session_messages"]["enable"] = False
        elif change == "remote":
            core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
        else:
            async with db.get_db() as connection:
                account = await connection.get(DashboardAccount, users[0]["account_id"])
                account.enabled = False
                connection.add(account)
                await connection.commit()
        return response

    monkeypatch.setattr(service, "read_connection", change_after_response)
    result = await ListChatSessionsTool().call(context)
    if change == "remote":
        assert all(row["connection_id"] is None for row in json.loads(result))
    else:
        assert result.startswith("error:")


@pytest.mark.asyncio
async def test_cached_creation_obeys_current_host_and_agent_permissions(remote_tools):
    instances, context, bindings, headers = remote_tools
    local, target, observed = instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = target
    remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] = True
    args = {
        "title": "Cached once",
        "project_id": None,
        "connection_id": bindings[0]["connection_id"],
        "connection_revision": bindings[0]["revision"],
    }
    token = current_tool_invocation.set(("cached", "same"))
    try:
        tool = CreateChatSessionTool()
        first = await tool.call(context, **args)
        assert not first.startswith("error:"), first
        assert (
            json.loads(await tool.call(context, **args))["session_id"]
            == json.loads(first)["session_id"]
        )
        remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] = False
        assert (await tool.call(context, **args)).startswith("error:")
        remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] = True
        remote_core.astrbot_config["session_messages"]["enable"] = False
        assert (await tool.call(context, **args)).startswith("error:")
        remote_core.astrbot_config["session_messages"]["enable"] = True
        core.astrbot_config["session_messages"]["enable"] = False
        assert (await tool.call(context, **args)).startswith("error:")
    finally:
        current_tool_invocation.reset(token)


@pytest.mark.asyncio
async def test_manual_exposure_save_is_not_lost_during_creation(
    remote_tools, monkeypatch
):
    instances, context, bindings, headers = remote_tools
    local, target, observed = instances
    client, server, core, db, admin, users = local
    remote, remote_server, remote_core, remote_db, remote_admin, credential = target
    remote_core.astrbot_config["chatui"]["remote_session_creation_enabled"] = True
    old = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    chosen = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    rh = {"Authorization": "Bearer " + remote_admin["token"]}
    await remote.put(
        "/api/v1/chat/remote-access/exposure",
        headers=rh,
        json={"mode": "selected", "session_ids": [old.session_id]},
    )
    entered, release = asyncio.Event(), asyncio.Event()
    chat = remote_server.asgi_app.state.services.chat
    original = chat.session_selection

    async def pause_selection(*args, **kwargs):
        """Pause a real creation while its manual exposure save arrives.

        Args:
            *args: Ordinary session selection arguments.
            **kwargs: Ordinary selection options.

        Returns:
            Validated target model preferences.
        """
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(chat, "session_selection", pause_selection)
    binding = bindings[0]
    creation = asyncio.create_task(
        client.post(
            "/api/v1/chat/remote-connections/" + binding["connection_id"] + "/sessions",
            headers=headers,
            json={
                "title": "Concurrent policy",
                "project_id": None,
                "revision": binding["revision"],
            },
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    manual = asyncio.create_task(
        remote.put(
            "/api/v1/chat/remote-access/exposure",
            headers=rh,
            json={"mode": "selected", "session_ids": [chosen.session_id]},
        )
    )
    await asyncio.sleep(0.05)
    release.set()
    created, saved = await asyncio.gather(creation, manual)
    assert created.status_code == 200 and saved.status_code == 200
    policy = (await remote.get("/api/v1/chat/remote-access", headers=rh)).json()["data"]
    assert policy["mode"] == "selected" and policy["session_ids"] == [chosen.session_id]
