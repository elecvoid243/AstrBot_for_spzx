"""Agent admissions share one authority across independent dashboard databases."""

import asyncio
import json

import pytest

from astrbot.builtin_stars.session_messages.tools import (
    SendChatMessageTool,
)
from astrbot.core.agent.tool import current_tool_invocation
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr
from astrbot.dashboard.responses import ApiError
from tests.test_remote_chat_connections import (  # noqa: F401
    account_dashboard,
    connected_instances,
    fake_core_lifecycle,
    ordinary_chat,
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
