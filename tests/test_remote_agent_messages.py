"""Agent admissions share one authority across independent dashboard databases."""

import asyncio
import json
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from astrbot.builtin_stars.session_messages.tools import (
    SendChatMessageTool,
)
from astrbot.core.agent.tool import current_tool_invocation
from astrbot.core.agent_team_execution import (
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.db.po import DashboardAccount
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.core.utils.webchat_access import DashboardPrincipal
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.services import remote_chat_service
from astrbot.dashboard.services.chat_service import RemoteChatAcceptanceUnknown
from tests.test_remote_chat_connections import (  # noqa: F401
    account_dashboard,
    connected_instances,
    fake_core_lifecycle,
)
from tests.test_remote_chat_connections import (
    ordinary_chat as ordinary_chat,
)
from tests.test_remote_chat_creation import remote_tools as remote_tools  # noqa: F401


@pytest.mark.asyncio
async def test_remote_agent_admission_provenance_and_atomic_budget(remote_tools):
    instances, context, bindings, _ = remote_tools
    local, target, _ = instances
    _, server, _, db, _, users = local
    remote, remote_server, _, remote_db, remote_admin, credential = target
    target_session = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    tool = SendChatMessageTool()
    selector = {
        "connection_id": bindings[0]["connection_id"],
        "connection_revision": bindings[0]["revision"],
    }
    result = json.loads(
        await tool.call(
            context,
            session_id=target_session.session_id,
            text="Agent remote message",
            **selector,
        )
    )
    assert result["status"] == "accepted", result
    assert type(result["input_message_id"]) is int
    saved_input = await remote_db.get_platform_message_history_by_id(
        result["input_message_id"]
    )
    assert saved_input is not None
    assert saved_input.user_id == target_session.session_id
    item = await webchat_queue_mgr.get_or_create_queue(target_session.session_id).get()
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    remote_event = adapter.create_event(await adapter.convert_message(item))
    assert (
        remote_event.get_extra("_agent_message_source").public_data()["remote"] is True
    )
    assert "Local binding" in remote_event.message_str
    assert credential["key"] not in remote_event.message_str + json.dumps(result)
    chain = context.context.event.get_extra("_session_message_chain")
    assert chain.count == 1
    # The remote accepted execution survives the tool return and requests its
    # next local send through the still-live origin-initiated polling channel.
    chain.count = 14
    local_target = await db.create_platform_session(creator=users[0]["resource_owner"])
    remote_target = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    results = await asyncio.gather(
        server.asgi_app.state.services.chat.send_session_message(
            context.context.event, local_target.session_id, "A branch"
        ),
        remote_server.asgi_app.state.services.chat.send_session_message(
            remote_event, remote_target.session_id, "B branch"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(row, dict) for row in results) == 1, results
    assert chain.count == 15
    assert "limit reached" in str(results)
    # Human message endpoints never accept a claimed Agent identity.
    forged = await remote.post(
        f"/api/v1/remote-chat/sessions/{target_session.session_id}/messages",
        headers={"Authorization": "RemoteSession " + credential["key"]},
        json={
            "text": "forged",
            "request_id": "10000000-0000-0000-0000-000000000000",
            "agent_source": {"kind": "agent"},
        },
    )
    assert forged.status_code == 422
    for service in (
        server.asgi_app.state.services.remote_chat,
        remote_server.asgi_app.state.services.remote_chat,
    ):
        for task in service.control_tasks:
            task.cancel()
        await asyncio.gather(*service.control_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_origin_limit_and_cached_policy(remote_tools):
    """Tighten at origin even for remote local branches and revalidate cached sends.

    Args:
        remote_tools: Two authenticated production dashboard applications.
    """
    instances, context, bindings, _ = remote_tools
    local, target, _ = instances
    _, server, core, _, _, _ = local
    _, remote_server, _, remote_db, remote_admin, _ = target
    tool = SendChatMessageTool()
    selector = {
        "connection_id": bindings[0]["connection_id"],
        "connection_revision": bindings[0]["revision"],
    }
    recipient = await remote_db.create_platform_session(
        creator=remote_admin["resource_owner"]
    )
    token = current_tool_invocation.set(("run", "same-send"))
    try:
        assert (
            json.loads(
                await tool.call(
                    context, session_id=recipient.session_id, text="First", **selector
                )
            )["status"]
            == "accepted"
        )
        item = await webchat_queue_mgr.get_or_create_queue(recipient.session_id).get()
        adapter = WebChatAdapter({}, {}, asyncio.Queue())
        event = adapter.create_event(await adapter.convert_message(item))
        core.astrbot_config["session_messages"]["max_chain_sends"] = 2
        targets = [
            await remote_db.create_platform_session(
                creator=remote_admin["resource_owner"]
            )
            for _ in range(2)
        ]
        results = await asyncio.gather(
            *(
                remote_server.asgi_app.state.services.chat.send_session_message(
                    event, row.session_id, "Branch"
                )
                for row in targets
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(row, dict) for row in results) == 1, results
        chain = context.context.event.get_extra("_session_message_chain")
        assert chain.count == 2 and chain.max_sends == 2
        core.astrbot_config["session_messages"]["enable"] = False
        assert (
            await tool.call(
                context, session_id=recipient.session_id, text="First", **selector
            )
        ).startswith("error:")
    finally:
        current_tool_invocation.reset(token)
        tasks = list(server.asgi_app.state.services.remote_chat.control_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_final_limit_lowered_while_waiting_for_authority(
    remote_tools, monkeypatch
):
    """Do not enqueue an old reservation after the receiving host lowers its limit.

    Args:
        remote_tools: Independent production source and target instances.
        monkeypatch: Lowers target policy immediately after the authority responds.
    """
    instances, context, bindings, _ = remote_tools
    local, target, _ = instances
    recipient = await target[3].create_platform_session(
        creator=target[4]["resource_owner"]
    )
    selector = {
        "connection_id": bindings[0]["connection_id"],
        "connection_revision": bindings[0]["revision"],
    }
    first = await SendChatMessageTool().call(
        context, session_id=recipient.session_id, text="First", **selector
    )
    assert json.loads(first)["status"] == "accepted"
    item = await webchat_queue_mgr.get_or_create_queue(recipient.session_id).get()
    chain = item[2]["_agent_message_source"].chain
    original = chain.control.reserve

    async def lower_after_response(limit, request_id, consume=True):
        """Lower the target between its successful reservation and final gate."""
        result = await original(limit, request_id, consume)
        target[2].astrbot_config["session_messages"]["max_chain_sends"] = 1
        return result

    monkeypatch.setattr(chain.control, "reserve", lower_after_response)
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(await adapter.convert_message(item))
    destination = await target[3].create_platform_session(
        creator=target[4]["resource_owner"]
    )
    try:
        from astrbot.dashboard.services.chat_service import ChatServiceError

        with pytest.raises(ChatServiceError, match="limit reached"):
            await target[1].asgi_app.state.services.chat.send_session_message(
                event, destination.session_id, "Old limit cannot admit this"
            )
        root = context.context.event.get_extra("_session_message_chain")
        assert root.max_sends == 1
        assert webchat_queue_mgr.get_or_create_queue(destination.session_id).empty()
    finally:
        tasks = list(local[1].asgi_app.state.services.remote_chat.control_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_both_policies_exposure_choice_and_account_boundaries(remote_tools):
    """Reject each new automatic send at its live authorization boundary.

    Args:
        remote_tools: Account-isolated ordinary source and target applications.
    """
    from astrbot.core.tools.interactive_choice_registry import registry
    from astrbot.dashboard.services.chat_service import build_webchat_unified_msg_origin

    instances, context, bindings, _ = remote_tools
    local, target, _ = instances
    recipient = await target[3].create_platform_session(
        creator=target[4]["resource_owner"]
    )
    principal = context.context.event.get_extra("_dashboard_account")
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    selector = {
        "connection_id": bindings[0]["connection_id"],
        "connection_revision": bindings[0]["revision"],
    }
    future = asyncio.get_running_loop().create_future()
    try:
        for gate in (
            "source_agent",
            "target_agent",
            "source_remote",
            "target_remote",
            "exposure",
            "choice",
        ):
            source = await local[3].create_platform_session(
                creator=principal.resource_owner
            )
            context.context.event = adapter.create_event(
                await adapter.convert_message(
                    (
                        principal.resource_owner,
                        source.session_id,
                        {
                            "message": [
                                {"type": "plain", "text": "Independent human turn"}
                            ],
                            "message_id": source.session_id,
                            "_dashboard_account": principal,
                        },
                    )
                )
            )
            for instance in (local, target):
                instance[2].astrbot_config["session_messages"]["enable"] = True
                instance[2].astrbot_config["chatui"]["remote_sessions_enabled"] = True
            await target[1].asgi_app.state.services.remote_chat.set_exposure(
                await target[1].asgi_app.state.services.remote_chat.authenticate(
                    target[5]["key"]
                ),
                "all",
                [],
            )
            if gate.endswith("agent"):
                (local if gate.startswith("source") else target)[2].astrbot_config[
                    "session_messages"
                ]["enable"] = False
            elif gate.endswith("remote"):
                (local if gate.startswith("source") else target)[2].astrbot_config[
                    "chatui"
                ]["remote_sessions_enabled"] = False
            elif gate == "exposure":
                service = target[1].asgi_app.state.services.remote_chat
                await service.set_exposure(
                    await service.authenticate(target[5]["key"]), "selected", []
                )
            else:
                registry.add(
                    "remote-agent-choice",
                    build_webchat_unified_msg_origin(recipient),
                    future,
                    {"prompt": "Human only"},
                    0,
                    99999999999,
                )
            result = await SendChatMessageTool().call(
                context, session_id=recipient.session_id, text="Must reject", **selector
            )
            assert (
                result.startswith("error:")
                or json.loads(result)["status"] == "rejected"
            ), (gate, result)
            assert webchat_queue_mgr.get_or_create_queue(recipient.session_id).empty()
            controls = local[1].asgi_app.state.services.remote_chat.outbound_controls
            assert not any(
                task.done() and task.exception() is not None
                for task in controls.values()
            )
        assert not future.done()
    finally:
        registry.remove("remote-agent-choice")
        tasks = list(local[1].asgi_app.state.services.remote_chat.control_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_unknown_acceptance_is_never_refunded_or_retried(
    remote_tools, monkeypatch
):
    """A lost response preserves its reservation and invocation's delivery identity.

    Args:
        remote_tools: Independent source and target databases and dashboard APIs.
        monkeypatch: Drops only the already-accepted delivery response.
    """
    instances, context, bindings, _ = remote_tools
    local, target, _ = instances
    service = local[1].asgi_app.state.services.remote_chat
    recipient = await target[3].create_platform_session(
        creator=target[4]["resource_owner"]
    )
    original = service.read_connection
    sends = 0

    async def drop_response(*args, **kwargs):
        """Drop an accepted response while allowing ordinary control requests."""
        nonlocal sends
        result = await original(*args, **kwargs)
        if args[2].endswith("/messages"):
            sends += 1
            raise ApiError(
                "Acceptance unknown",
                status_code=502,
                data={"code": "remote_acceptance_unknown"},
            )
        return result

    monkeypatch.setattr(service, "read_connection", drop_response)
    token = current_tool_invocation.set(("run", "uncertain-send"))
    selector = {
        "connection_id": bindings[0]["connection_id"],
        "connection_revision": bindings[0]["revision"],
    }
    try:
        for _ in range(2):
            result = await SendChatMessageTool().call(
                context,
                session_id=recipient.session_id,
                text="Do not retry",
                **selector,
            )
            assert "remote_acceptance_unknown" in result
        assert sends == 1
        assert context.context.event.get_extra("_session_message_chain").count == 1
        target[2].astrbot_config["session_messages"]["enable"] = False
        revoked = await SendChatMessageTool().call(
            context, session_id=recipient.session_id, text="Do not retry", **selector
        )
        assert (
            revoked.startswith("error:") and "remote_acceptance_unknown" not in revoked
        )
        assert sends == 1
        queue = webchat_queue_mgr.get_or_create_queue(recipient.session_id)
        await queue.get()
        assert queue.empty()
    finally:
        current_tool_invocation.reset(token)
        tasks = list(service.control_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    [
        "unknown",
        "processing",
        "history",
        "history-revoked",
        "local-switch",
        "source-revoked",
        "rejected",
        "disabled-before-send",
        "channel-failed",
        "accepted",
    ],
)
async def test_debug_remote_agent_write_needs_verified_receipt(
    ordinary_chat, monkeypatch, boundary
):
    """Exercise post-send failures at the controlled remote HTTP boundary.

    Args:
        ordinary_chat: Local Dashboard and independent account database.
        monkeypatch: Installs the controlled remote HTTP endpoint.
        boundary: Remote reply or permission check that ends this invocation.
    """
    _, server, core, db, _, users = ordinary_chat
    user = users[0]
    principal = DashboardPrincipal(
        user["account_id"], user["resource_owner"], "user", user["username"]
    )
    source = await db.create_platform_session(creator=principal.resource_owner)
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    event = adapter.create_event(
        await adapter.convert_message(
            (
                principal.resource_owner,
                source.session_id,
                {
                    "message": [{"type": "plain", "text": "Send a report"}],
                    "message_id": "source",
                    "_dashboard_account": principal,
                },
            )
        )
    )
    chat = server.asgi_app.state.services.chat
    service = chat.remote_chat
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    async with db.get_db() as session:
        account = await session.get(DashboardAccount, principal.account_id)
    connection_id = await service.save_connection(
        account, "Controlled target", "http://remote", "arc_controlled"
    )
    (connection,) = (await service.connections(account))["connections"]
    sends = []
    history = []

    async def remote(request):
        """Return controlled protocol responses and record the actual write.

        Args:
            request: HTTP request sent by the installed remote ChatUI adapter.

        Returns:
            Response from the controlled remote instance.
        """
        path = request.url.path
        if path.endswith("/channels"):
            if boundary == "channel-failed":
                raise httpx.ReadError("Control response lost", request=request)
            data = {"channel_id": "target-channel", "limit": 5}
        elif path.endswith("/poll"):
            await asyncio.Future()
        elif path.endswith("/messages"):
            sends.append(json.loads(request.content))
            if boundary == "rejected":
                return httpx.Response(403)
            if boundary == "local-switch":
                core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
            elif boundary == "source-revoked":
                await db.update_platform_session(source.session_id, archived=1)
            data = {
                "status": boundary
                if boundary in {"unknown", "processing"}
                else "accepted",
                "run_id": "private-remote-run",
            }
        else:
            assert path.endswith("/history"), path
            history.append(path)
            if boundary == "history-revoked":
                return httpx.Response(403)
            if boundary != "accepted":
                raise httpx.ReadError("private history details", request=request)
            data = []
        return httpx.Response(200, json={"status": "ok", "data": data})

    remote_httpx = SimpleNamespace(**vars(httpx))
    remote_httpx.AsyncClient = lambda **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(remote), **kwargs
    )
    monkeypatch.setattr(remote_chat_service, "httpx", remote_httpx)
    context = SimpleNamespace(
        context=SimpleNamespace(
            context=SimpleNamespace(session_message_service=chat),
            event=event,
            extra={"main_agent_runner": SimpleNamespace(provider=None, req=None)},
        )
    )
    token = current_team_execution.set(
        NodeExecutionBinding(
            run_id="debug-run",
            team_id="team",
            member_id="sender",
            node_id="send",
            umo=event.unified_msg_origin,
            owner_username=principal.resource_owner,
            config_id=None,
            preview_tool_write=AsyncMock(),
        )
    )
    try:
        tool = SendChatMessageTool()
        args = {
            "connection_id": connection_id,
            "connection_revision": connection["revision"],
            "session_id": "remote-recipient",
            "text": "Reviewed report",
        }
        if boundary == "disabled-before-send":
            core.astrbot_config["chatui"]["remote_sessions_enabled"] = False
            assert (
                await tool.call(context, **args)
                == "error: Remote session connections are disabled"
            )
            assert not sends and not history
            return
        if boundary == "channel-failed":
            assert (
                await tool.call(context, **args) == "error: remote_acceptance_unknown"
            )
            assert not sends and not history
            return
        if boundary == "rejected":
            assert await tool.call(context, **args) == "error: remote_disabled"
        elif boundary == "accepted":
            result = json.loads(await tool.call(context, **args))
            assert result["status"] == "accepted"
            assert result["run_id"] == "private-remote-run"
        else:
            with pytest.raises(
                RemoteChatAcceptanceUnknown,
                match="^Remote message acceptance needs verification$",
            ) as uncertain:
                await tool.call(context, **args)
            diagnostic = "".join(traceback.format_exception(uncertain.value))
            assert "private-remote-run" not in diagnostic
            assert "private history details" not in diagnostic
        (delivery,) = sends
        assert delivery["session_id"] == "remote-recipient"
        assert delivery["text"] == "Reviewed report"
        assert len(history) == int(
            boundary in {"history", "history-revoked", "accepted"}
        )
    finally:
        current_team_execution.reset(token)
        tasks = list(service.control_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
