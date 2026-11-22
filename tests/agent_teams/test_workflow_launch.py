"""Normal Dashboard launches preserve a retry identity across dispatch recovery."""

import asyncio
import hashlib
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from astrbot.core.agent_team_execution import AgentTeamExecutionRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.dashboard.api import chat, open_api
from astrbot.dashboard.api.auth import AuthContext, require_dashboard_user
from astrbot.dashboard.api.workflow_assets import router
from astrbot.dashboard.responses import ApiError, error
from astrbot.dashboard.services.api_key_service import ApiKeyService
from astrbot.dashboard.services.chat_service import ChatService
from astrbot.dashboard.services.open_api_service import OpenApiService
from tests.agent_teams.test_chat_browser_access import send_chat_command
from tests.agent_teams.test_workflow_assets import (
    completed_run,
    workflow_env as workflow_env,
)


@pytest.mark.asyncio
async def test_dashboard_launch_recovers_the_committed_root(workflow_env, monkeypatch):
    """Recover a lost wake via the same request and reject a changed payload.

    Args:
        workflow_env: Workflow services and resources in isolated SQLite.
        monkeypatch: Inject the interruption between commit and scheduler wake.
    """
    env = workflow_env
    app = FastAPI()
    app.state.services = SimpleNamespace(workflow_assets=env.service)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    url = f"/api/v1/workflow_assets/{env.asset['asset_id']}/runs"
    payload = {"version": 1, "request_id": "bot-start"}
    committed = []
    advance_run = env.service.advance_run

    async def drop_first_wake(username, run_id):
        if not committed:
            committed.append(run_id)
            raise RuntimeError("Lost wake after launch commit")
        return await advance_run(username, run_id)

    monkeypatch.setattr(env.service, "advance_run", drop_first_wake)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        assert (await client.post(url, json=payload)).status_code == 500
        responses = await asyncio.gather(
            *(client.post(url, json=payload) for _ in range(3))
        )
        assert all(response.status_code == 200 for response in responses)
        run_id = committed[0]
        assert {response.json()["data"]["run_id"] for response in responses} == {run_id}
        changed = await client.post(url, json={**payload, "inputs": {"extra": True}})
        assert changed.status_code == 409
        independent = await client.post(
            url, json={**payload, "request_id": "new-start"}
        )
        assert independent.status_code == 200
        assert independent.json()["data"]["run_id"] != run_id
        assert (await completed_run(env.service, run_id))["status"] == "completed"
        env.inventory["models"] = []
        replay = await client.post(url, json={**payload, "replay_only": True})
        assert replay.status_code == 200
        assert replay.json()["data"]["run_id"] == run_id
        probe = await client.post(
            url, json={**payload, "request_id": "uncommitted", "replay_only": True}
        )
        assert probe.status_code == 404
        assert probe.json()["data"]["fields"][0]["code"] == "LAUNCH_NOT_COMMITTED"


def launch_app(env):
    """Host the actual launch, API-key, and Chat routes against isolated services.

    Args:
        env: Workflow fixture with an isolated database.

    Returns:
        The local ASGI app with a synthetic signed-in Dashboard owner.
    """
    app = FastAPI()
    app.state.db = env.db
    lifecycle = SimpleNamespace(
        conversation_manager=None,
        platform_message_history_manager=PlatformMessageHistoryManager(env.db),
        umop_config_router=None,
        platform_manager=None,
        astrbot_config_mgr=SimpleNamespace(confs={}, get_conf_list=lambda: []),
    )
    app.state.services = SimpleNamespace(
        workflow_assets=env.service,
        chat=ChatService(env.db, lifecycle),
        open_api=OpenApiService(env.db, lifecycle),
    )
    for routes in (router, chat.router, open_api.router):
        app.include_router(routes, prefix="/api/v1")
    app.dependency_overrides[require_dashboard_user] = lambda: "alice"
    app.dependency_overrides[open_api.require_chat_scope] = lambda: AuthContext(
        "alice", ["chat"], role="admin"
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(_request, exc):
        return JSONResponse(error(exc.message, exc.data), status_code=exc.status_code)

    return app


@pytest.mark.asyncio
async def test_fixed_version_launch_contract_from_dashboard_platform_and_api(
    workflow_env,
):
    """Freeze equal port values and one child contract across the three launch routes.

    Args:
        workflow_env: Isolated services and SQLite.
    """
    env = workflow_env
    service = env.service
    inputs = {
        "type": "object",
        "properties": {
            "query": {"type": "object"},
            "note": {"type": ["string", "null"], "default": "default note"},
        },
        "required": ["query"],
    }
    ports = service.ports_factory("alice")
    deliver = ports.deliver

    async def metered_delivery(
        session_id, text, context=None, execution_token=None, message_id=None
    ):
        binding = AgentTeamExecutionRegistry.resolve(execution_token)
        receipt = await binding.begin_usage(
            {
                "receipt_id": message_id,
                "invocation_id": message_id,
                "kind": "model",
                "input_tokens": 2,
                "max_output_tokens": 3,
            }
        )
        await binding.report_usage(
            receipt["receipt_id"], {"input_tokens": 2, "output_tokens": 3}
        )
        return await deliver(session_id, text, context, execution_token, message_id)

    ports.deliver = metered_delivery
    service.ports_factory = lambda _username: ports
    outputs = {**inputs, "required": ["query", "note"]}
    definition = {
        "name": "Fixed child",
        "port_contract_version": 2,
        "input_contract": inputs,
        "entry_task_ids": ["result"],
        "result_task_id": "result",
        "tasks": [
            {
                "id": "result",
                "name": "Result",
                "transform": "map_fields",
                "input_contract": inputs,
                "output_contract": outputs,
                "inputs": {name: {"input": name} for name in inputs["properties"]},
            }
        ],
    }
    child = await service.save_asset(
        "alice",
        {
            "definition": {
                **definition,
                "entry_task_ids": ["writer"],
                "edges": [{"source": "writer", "target": "result"}],
                "tasks": [env.asset["definition"]["tasks"][0], *definition["tasks"]],
            }
        },
    )
    definition = {
        **definition,
        "name": "Authorized report",
        "limits": {"max_steps": 1},
        "tasks": [
            {
                **definition["tasks"][0],
                "transform": None,
                "workflow": {"asset_id": child["asset_id"], "version": 1},
            }
        ],
    }
    asset = await service.save_asset("alice", {"definition": definition})
    asset_id = asset["asset_id"]
    for saved in (asset, child):
        await service.save_asset(
            "alice",
            {
                "definition": {**saved["definition"], "name": "New publication"},
                "expected_version": 1,
            },
            saved["asset_id"],
        )
    keys = ApiKeyService(env.db)
    first, second, unscoped = [
        await keys.create_api_key({"name": name, "scopes": scopes}, created_by="alice")
        for name, scopes in [
            ("Report key", ["workflow"]),
            ("Another key", ["workflow"]),
            ("Chat-only key", ["chat"]),
        ]
    ]
    api = f"/api/v1/workflows/{asset_id}/runs"
    dashboard = f"/api/v1/workflow_assets/{asset_id}"
    request = {"version": 1, "inputs": {"query": {"max": 0}}, "request_id": "initial"}
    headers = {"Authorization": f"ApiKey {first['api_key']}"}
    other_headers = {"Authorization": f"ApiKey {second['api_key']}"}
    platform_grant = {
        "platform_id": "qq-main",
        "sender_id": "12345",
        "source": "qq-main:FriendMessage:12345",
        "profile_id": "default",
        "version": 1,
    }
    optional = await service.save_asset(
        "alice",
        {
            "definition": {
                **child["definition"],
                "name": "Optional source",
                "entry_task_ids": ["result"],
                "edges": [],
                "input_contract": {
                    "type": "object",
                    "properties": {
                        "note": {"type": ["string", "null"], "default": "x"}
                    },
                },
                "tasks": [
                    {
                        **child["definition"]["tasks"][-1],
                        "input_contract": {
                            "type": "object",
                            "properties": {
                                "note": {"type": ["string", "null"], "default": "x"}
                            },
                        },
                        "output_contract": {
                            "type": "object",
                            "properties": {"note": {"type": ["string", "null"]}},
                            "required": ["note"],
                        },
                        "inputs": {"note": {"input": "note"}},
                    }
                ],
            }
        },
    )
    for offered in (optional, env.asset):
        await service.chat.save_access("alice", offered["asset_id"], platform_grant)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=launch_app(env)), base_url="http://test"
    ) as client:
        assert (
            await client.post(api, json=request, headers=headers)
        ).status_code == 404
        denied_scope = await client.post(
            api,
            json=request,
            headers={"Authorization": f"ApiKey {unscoped['api_key']}"},
        )
        assert denied_scope.status_code == 403
        command = lambda inputs: f"/asset {asset_id} | {json.dumps(inputs)}"
        _, denied = await send_chat_command(service, command(request["inputs"]))
        assert "not available" in denied[0]["message"][0]["data"]["text"]
        for key in (first, second):
            grant = f"{dashboard}/api_access/{key['key_id']}"
            assert (await client.put(grant, json={"version": 1})).status_code == 200
        assert (
            await client.put(f"{dashboard}/chat_access", json=platform_grant)
        ).status_code == 200
        _, listed = await send_chat_command(service, "/asset list")
        directory = json.loads(
            listed[0]["message"][0]["data"]["text"].split("\n", 1)[1]
        )["assets"]
        by_id = {item["asset_id"]: item for item in directory}
        entry = by_id[asset_id]
        assert by_id[optional["asset_id"]]["required_inputs"] == []
        assert "input_types" not in by_id[env.asset["asset_id"]]
        assert entry["required_inputs"] == ["query"]
        assert entry["input_types"] == {"query": "object", "note": ["string", "null"]}
        assert (
            await client.post(api, json={**request, "version": 2}, headers=headers)
        ).status_code == 404

        wrong = {**request, "inputs": {"query": []}}
        bot_error = await client.post(f"{dashboard}/runs", json=wrong)
        api_error = await client.post(api, json=wrong, headers=headers)
        _, messages = await send_chat_command(service, command(wrong["inputs"]))
        chat_error = json.loads(messages[0]["message"][0]["data"]["text"])
        assert bot_error.status_code == api_error.status_code == 400
        assert (
            bot_error.json()["data"]["fields"]
            == api_error.json()["data"]["fields"]
            == chat_error["fields"]
        )
        assert chat_error["fields"][0]["path"] == "inputs.query"

        bot = await client.post(
            f"{dashboard}/runs", json={**request, "source": "caller supplied"}
        )
        public = await client.post(api, json=request, headers=headers)
        assert bot.status_code == public.status_code == 200, (bot.text, public.text)
        assert bot.json()["data"]["snapshot"]["source"] == "dashboard"
        _, messages = await send_chat_command(
            service, command(request["inputs"]), message_id="onebot-original"
        )
        platform_run = re.search(
            r"Accepted run ([0-9a-f]{32})", messages[0]["message"][0]["data"]["text"]
        )[1]
        ids = [
            bot.json()["data"]["run_id"],
            platform_run,
            public.json()["data"]["run_id"],
        ]
        effective = {"query": {"max": 0}, "note": "default note"}
        for run_id in ids:
            saved = await completed_run(service, run_id)
            assert saved["status"] == "completed"
            assert saved["snapshot"]["asset_version"] == 1
            assert saved["snapshot"]["inputs"] == saved["result"] == effective
            assert saved["usage"]["limits"]["max_steps"] == 1
            assert saved["usage"]["steps_used"] == 1
            assert saved["usage"]["tokens"]["reported"] == 5
            child_scope = saved["scopes"][0]
            assert child_scope["snapshot"]["root_run_id"] == run_id
            assert child_scope["snapshot"]["asset_version"] == 1

        body = {**request, "inputs": effective}
        assert (await client.post(f"{dashboard}/runs", json=body)).json()["data"][
            "run_id"
        ] == ids[0]
        assert (await client.post(api, json=body, headers=headers)).json()["data"][
            "run_id"
        ] == ids[2]
        _, messages = await send_chat_command(
            service, command(effective), message_id="onebot-original"
        )
        assert platform_run in messages[0]["message"][0]["data"]["text"]
        changed = {**request, "inputs": {**effective, "note": None}}
        assert (
            await client.post(api, json=changed, headers=headers)
        ).status_code == 409
        _, messages = await send_chat_command(
            service, command(changed["inputs"]), message_id="onebot-original"
        )
        assert (
            "reused with a different payload"
            in messages[0]["message"][0]["data"]["text"]
        )

        _, messages = await send_chat_command(
            service, command(effective), message_id="onebot-independent"
        )
        independent = re.search(
            r"Accepted run ([0-9a-f]{32})", messages[0]["message"][0]["data"]["text"]
        )[1]
        assert independent not in ids
        ids.append(independent)
        chat_sessions = await client.get(
            "/api/v1/chat/sessions",
            params={"include_run_conversations": True, "cursor": "start"},
        )
        sessions = chat_sessions.json()["data"]["items"]
        assert sorted(item["session_id"] for item in sessions) == sorted(
            f"workflow-run-{run_id}" for run_id in ids
        )
        read = f"/api/v1/workflows/runs/{ids[2]}"
        assert (await client.get(read, headers=headers)).json()["data"] == {
            "asset_id": asset_id,
            "asset_version": 1,
            "run_id": ids[2],
            "status": "completed",
            "result": effective,
        }
        assert (await client.get(read, headers=other_headers)).status_code == 404
        assert (
            await client.get(read.replace(ids[2], platform_run), headers=headers)
        ).status_code == 404
        _, messages = await send_chat_command(
            service, f"/asset status {platform_run}", sender="777"
        )
        assert "not found" in messages[0]["message"][0]["data"]["text"].lower()

        assert (
            await client.delete(f"{dashboard}/api_access/{first['key_id']}")
        ).status_code == 200
        assert (
            await client.post(api, json=request, headers=headers)
        ).status_code == 404
        assert (await client.get(read, headers=headers)).status_code == 404
        changed_profile = {**platform_grant, "profile_id": "different-profile"}
        assert (
            await client.put(f"{dashboard}/chat_access", json=changed_profile)
        ).status_code == 200
        caller = ChatCaller.model_validate(
            {k: v for k, v in changed_profile.items() if k != "version"}
        )
        with pytest.raises(
            RoleAssetError, match="original platform source authorization changed"
        ):
            await service.chat.invoke(
                caller,
                asset_id,
                effective,
                request_id=hashlib.sha256(b"onebot-original").hexdigest(),
            )
        assert (
            await client.delete(f"{dashboard}/chat_access/qq-main/12345")
        ).status_code == 200
        _, messages = await send_chat_command(
            service, command(effective), message_id="onebot-original"
        )
        assert "not available" in messages[0]["message"][0]["data"]["text"]


@pytest.mark.asyncio
async def test_revocation_blocks_a_queued_api_run_and_its_fixed_child(
    workflow_env, monkeypatch
):
    """The owner can still see a queued root after its delegated grant is revoked.

    Args:
        workflow_env: Isolated workflow services and SQLite.
        monkeypatch: Interrupt the launch immediately before its first scheduler wake.
    """
    env = workflow_env
    service = env.service
    saved = await service.save_asset(
        "alice",
        {
            "definition": {
                "name": "Queued fixed child",
                "result_task_id": "call",
                "tasks": [
                    {
                        "id": "call",
                        "name": "Call",
                        "workflow": {
                            "asset_id": env.asset["asset_id"],
                            "version": 1,
                        },
                    }
                ],
            }
        },
    )
    key = await ApiKeyService(env.db).create_api_key(
        {"scopes": ["workflow"]}, created_by="alice"
    )
    asset_id = saved["asset_id"]
    grant = f"/api/v1/workflow_assets/{asset_id}/api_access/{key['key_id']}"
    api = f"/api/v1/workflows/{asset_id}/runs"
    headers = {"Authorization": f"ApiKey {key['api_key']}"}
    advance = service.advance_run

    async def delay_first_wake(username, run_id):
        return await service.get_run(username, run_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=launch_app(env)), base_url="http://test"
    ) as client:
        assert (await client.put(grant, json={"version": 1})).status_code == 200
        monkeypatch.setattr(service, "advance_run", delay_first_wake)
        queued = await client.post(
            api, json={"version": 1, "request_id": "queued"}, headers=headers
        )
        assert queued.status_code == 200, queued.text
        run_id = queued.json()["data"]["run_id"]
        detail = f"/api/v1/workflow_runs/{run_id}"
        monkeypatch.setattr(service, "advance_run", advance)
        assert (
            await client.post(
                f"{detail}/control", json={"action": "pause", "request_id": "p"}
            )
        ).status_code == 200
        assert (await client.delete(grant)).status_code == 200
        assert (
            await client.post(
                f"{detail}/control", json={"action": "continue", "request_id": "c"}
            )
        ).status_code == 200
        await service.advance_run("alice", run_id)
        owner = (await client.get(detail)).json()["data"]
        assert owner["status"] == "blocked"
        assert "no longer has access" in owner["state"]["reason"]
        assert owner["scopes"] == [] and env.observations == []
        assert (
            await client.get(f"/api/v1/workflows/runs/{run_id}", headers=headers)
        ).status_code == 404


@pytest.mark.asyncio
async def test_external_launch_keeps_fixed_child_history_behind_dashboard_access(
    workflow_env,
):
    """Check historical input policy at launch and preserve Dashboard bindings.

    Args:
        workflow_env: Isolated persisted workflow and historical artifact.
    """
    env = workflow_env
    service = env.service
    historical = await service.start_run("alice", env.asset["asset_id"], {"version": 1})
    history = await completed_run(service, historical["run_id"])
    typed = {
        "type": "object",
        "properties": {"reference": {"type": "object", "format": "astrbot-artifact"}},
        "required": ["reference"],
    }
    task = {
        "id": "output",
        "name": "Reference",
        "transform": "map_fields",
        "inputs": {"reference": {"input": "reference"}},
        "input_contract": typed,
        "output_contract": typed,
    }
    definition = {
        "name": "Historical input",
        "port_contract_version": 2,
        "input_contract": typed,
        "entry_task_ids": ["output"],
        "result_task_id": "output",
        "tasks": [task],
    }
    with pytest.raises(RoleAssetError) as invalid:
        await service.save_asset(
            "alice",
            {
                "definition": {
                    **definition,
                    "tasks": [
                        {
                            **task,
                            "input_contract": {
                                **typed,
                                "properties": {
                                    "reference": {
                                        **typed["properties"]["reference"],
                                        "default": history["result_ref"],
                                    }
                                },
                            },
                        }
                    ],
                }
            },
        )
    assert any("without defaults" in field["message"] for field in invalid.value.fields)
    child = await service.save_asset("alice", {"definition": definition})
    parent = await service.save_asset(
        "alice",
        {
            "definition": {
                **definition,
                "name": "Fixed parent",
                "tasks": [
                    {
                        **task,
                        "name": "Call child",
                        "transform": None,
                        "workflow": {"asset_id": child["asset_id"], "version": 1},
                    }
                ],
            }
        },
    )
    parent_id = parent["asset_id"]
    key = await ApiKeyService(env.db).create_api_key(
        {"scopes": ["workflow"]}, created_by="alice"
    )
    root = f"/api/v1/workflow_assets/{parent_id}"
    launch = {
        "version": 1,
        "request_id": "history-attempt",
        "inputs": {"reference": history["result_ref"]},
    }
    headers = {"Authorization": f"ApiKey {key['api_key']}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=launch_app(env)), base_url="http://test"
    ) as client:
        grant = await client.put(
            f"{root}/api_access/{key['key_id']}", json={"version": 1}
        )
        assert grant.status_code == 200, grant.text
        denied = await client.post(
            f"/api/v1/workflows/{parent_id}/runs", json=launch, headers=headers
        )
        assert denied.status_code == 403, denied.text
        platform = {
            "version": 1,
            "platform_id": "qq-main",
            "sender_id": "12345",
            "profile_id": "default",
            "source": "qq-main:FriendMessage:12345",
        }
        grant = await client.put(f"{root}/chat_access", json=platform)
        assert grant.status_code == 200, grant.text
        _, denied_platform = await send_chat_command(
            service, f"/asset {parent_id} | {json.dumps(launch['inputs'])}"
        )
        assert (
            "Historical reference inputs require Dashboard authorization"
            in denied_platform[0]["message"][0]["data"]["text"]
        )
        assert {
            item["run_id"] for item in (await service.list_runs("alice"))["runs"]
        } == {historical["run_id"]}
        dashboard = await client.post(f"{root}/runs", json=launch)
        assert dashboard.status_code == 200, dashboard.text
        complete = await completed_run(service, dashboard.json()["data"]["run_id"])
        assert complete["result"] == {"reference": history["result_ref"]}
