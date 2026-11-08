"""Read-only remote presentation keeps the exposed conversation as its boundary."""

import asyncio
import hashlib
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from astrbot.core.subagent_progress import SubagentProgress
from astrbot.dashboard.api import remote_chat
from tests.test_remote_chat_resources import (  # noqa: F401
    account_dashboard,
    connected_instances,
    fake_core_lifecycle,
    ordinary_chat,
)
from tests.test_remote_chat_resources import resources as resources  # noqa: F401


@pytest.mark.asyncio
async def test_remote_subagent_trace_and_configuration_are_parent_scoped(
    resources, monkeypatch
):
    local, remote, _, session, other, headers, binding, path = resources
    client = local[0]
    target, server, _, db, admin, credential = remote
    progress = SubagentProgress(db)
    monkeypatch.setattr(remote_chat, "subagent_progress", progress)
    await progress.record(
        f"webchat:FriendMessage:webchat!{admin['resource_owner']}!{session.session_id}",
        "poet-instance",
        True,
        {
            "subagent_run_id": "poet-run",
            "agent_name": "poet",
            "kind": "started",
            "payload": {
                "input_preview": "Write a poem",
                "configuration": {"model": "remote-model"},
            },
            "ts": 1,
        },
    )
    params = {"revision": binding["revision"]}
    response = await client.get(
        path + "/subagent-runs/poet-run/configuration", headers=headers, params=params
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"model": "remote-model"}
    assert (
        await client.get(
            path.replace(session.session_id, other.session_id)
            + "/subagent-runs/poet-run/configuration",
            headers=headers,
            params=params,
        )
    ).status_code == 502

    # Consume the real endpoint iterator: an idle trace must also recheck grants.
    request = Request(
        {
            "type": "http",
            "app": server.asgi_app,
            "headers": [
                (b"authorization", ("RemoteSession " + credential["key"]).encode())
            ],
        }
    )
    dependency = remote_chat.require_remote_account(request)
    account = await anext(dependency)
    response = await remote_chat.remote_subagent_events(
        session.session_id, request, "poet-run", account
    )
    stream = response.body_iterator
    try:
        snapshot = await asyncio.wait_for(anext(stream), 2)
        assert "Write a poem" in snapshot and "poet-run" in snapshot
        await target.put(
            "/api/v1/chat/remote-access/exposure",
            headers={"Authorization": "Bearer " + admin["token"]},
            json={"mode": "selected", "session_ids": []},
        )
        revoked = await asyncio.wait_for(anext(stream), 2)
        assert "access_error" in revoked
    finally:
        await stream.aclose()
        await dependency.aclose()


@pytest.mark.asyncio
async def test_remote_file_diff_uses_recorded_source_and_never_local_paths(
    resources, tmp_path
):
    local, remote, _, session, other, headers, binding, path = resources
    client = local[0]
    db = remote[3]
    file = tmp_path / "remote.txt"
    file.write_text("Remote-only output\n", encoding="utf-8")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    record = await db.insert_platform_message_history(
        "webchat",
        session.session_id,
        {
            "type": "bot",
            "message": [],
            "file_changes": {
                "files": [
                    {
                        "path": str(file),
                        "runtime": "local",
                        "kind": "created",
                        "backup_id": "",
                        "sha256": digest,
                        "_canonical_path": str(file.resolve()),
                    }
                ]
            },
        },
    )
    params = {
        "revision": binding["revision"],
        "path": str(file),
        "message_id": record.id,
    }
    response = await client.get(path + "/file-diff", headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert "+Remote-only output" in response.json()["data"]["diff"]
    assert (
        await client.get(
            path.replace(session.session_id, other.session_id) + "/file-diff",
            headers=headers,
            params=params,
        )
    ).status_code == 502
    params["path"] = str(tmp_path / "unpublished.txt")
    assert (
        await client.get(path + "/file-diff", headers=headers, params=params)
    ).status_code == 502


@pytest.mark.asyncio
async def test_shell_output_proxy_is_read_only_and_owned_by_remote_session(
    resources, monkeypatch
):
    local, remote, _, session, _, headers, binding, path = resources
    service = remote[1].asgi_app.state.services.chat
    output = AsyncMock(
        return_value={
            "stdout": "remote log",
            "cursor": 10,
            "status": "completed",
            "session_closed": True,
        }
    )
    monkeypatch.setattr(service, "get_shell_session_output", output)
    response = await local[0].get(
        path + "/shell-sessions/process/output",
        headers=headers,
        params={"revision": binding["revision"], "cursor": 3},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["stdout"] == "remote log"
    output.assert_awaited_once_with(
        remote[4]["resource_owner"],
        session.session_id,
        "process",
        cursor=3,
        yield_time_ms=1000,
    )
    assert (
        await local[0].post(path + "/shell-sessions/process/terminate", headers=headers)
    ).status_code in {403, 404, 405}


@pytest.mark.asyncio
async def test_session_snapshot_includes_live_goal_and_shell_information(
    resources, monkeypatch
):
    _, remote, _, session, _, _, _, _ = resources
    _, server, _, _, _, credential = remote
    service = server.asgi_app.state.services.chat
    goal = {
        "goal": "Finish remote work",
        "turns_used": 2,
        "max_turns": 15,
        "status": "active",
    }
    goal_read = AsyncMock(return_value={"goal": goal})
    monkeypatch.setattr(service, "get_session_goal", goal_read)
    monkeypatch.setattr(
        service,
        "get_session_shell_sessions",
        AsyncMock(
            return_value={
                "sessions": [{"session_id": "remote-process", "status": "running"}]
            }
        ),
    )
    request = Request(
        {
            "type": "http",
            "app": server.asgi_app,
            "headers": [
                (b"authorization", ("RemoteSession " + credential["key"]).encode())
            ],
        }
    )
    dependency = remote_chat.require_remote_account(request)
    account = await anext(dependency)
    monkeypatch.setattr(
        service,
        "session_selection",
        AsyncMock(return_value={"selected_provider": None, "selected_model": None}),
    )
    monkeypatch.setattr(
        server.asgi_app.state.services.providers,
        "list_providers",
        lambda **_: {
            "providers": [{"id": "remote-model", "max_context_tokens": 128000}]
        },
    )

    async def telemetry():
        yield 'data: {"type":"context_compression","data":{"strategy":"llm_compress","tokens_before":20000,"tokens_after":12800}}\n\n'

    monkeypatch.setattr(
        service, "get_active_chat_runs", lambda *_: [{"run_id": "telemetry"}]
    )
    monkeypatch.setattr(
        service, "build_chat_run_stream", AsyncMock(return_value=telemetry())
    )
    response = await remote_chat.remote_session_events(
        session.session_id, request, account
    )
    iterator = response.body_iterator
    try:
        import json

        snapshot = json.loads(
            (await asyncio.wait_for(anext(iterator), 3)).removeprefix("data: ")
        )["data"]
        assert snapshot["context_limit"] == 128000
        assert snapshot["goal"] == goal
        assert (
            snapshot["shell_sessions"]["sessions"][0]["session_id"] == "remote-process"
        )
        goal_read.assert_awaited_once_with(account.resource_owner, session.session_id)
        compressed = json.loads(
            (await asyncio.wait_for(anext(iterator), 3)).removeprefix("data: ")
        )["data"]
        assert compressed["context_compression"]["notice"]["tokens_after"] == 12800

    finally:
        await iterator.aclose()
        await dependency.aclose()
