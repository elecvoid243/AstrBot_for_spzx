"""Exercise decision adapters through the plugin and workflow entry points."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from astrbot.core.db.po import DashboardAccount
from astrbot.core.provider.decision import DecisionService
from astrbot.core.provider.entities import DecisionError
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.provider.sources.openai_decision_source import OpenAIDecisionProvider
from astrbot.core.provider.sources.system_one_decision_source import (
    SystemOneDecisionProvider,
)
from tests.agent_teams.test_workflow_assets import completed_run
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai", "system_one"])
async def test_decision_workflow_uses_bound_provider_and_retries(
    workflow_env, protocol, monkeypatch
):
    requests = []
    fail_next = False

    def send(request):
        nonlocal fail_next
        requests.append((request.url.path, json.loads(request.content)))
        if fail_next:
            fail_next = False
            return httpx.Response(503)
        answers = (
            [{"name": "q0", "type": "predicate", "probability": 0.2}]
            if protocol == "openai"
            else {"answer": {"noul": 0.2}}
        )
        return httpx.Response(
            200, json={"answers": answers, "usage": {"total_tokens": 42}}
        )

    provider_cls = (
        OpenAIDecisionProvider if protocol == "openai" else SystemOneDecisionProvider
    )
    provider = provider_cls(
        {
            "id": "judge",
            "type": f"{protocol}_decision",
            "api_base": "https://decision.example/v1",
            "model": "decision-model",
        },
        {},
        transport=httpx.MockTransport(send),
    )
    provider_settings = {}
    manager = ProviderManager(
        SimpleNamespace(
            confs={"default": {"provider": [], "provider_settings": provider_settings}}
        ),
        workflow_env.db,
        SimpleNamespace(default_persona=""),
    )
    manager.inst_map["judge"] = provider
    workflow_env.roles.core_lifecycle = SimpleNamespace(provider_manager=manager)
    workflow_env.inventory["decision_providers"] = [{"id": "judge", "name": "Judge"}]
    async with workflow_env.db.get_db() as session:
        session.add(
            DashboardAccount(
                username="alice",
                legacy_owner="alice",
                role="admin",
                password_hash="fixture",
            )
        )
        await session.commit()

    service = workflow_env.service
    previous_wake = service.advance_run
    test_task = asyncio.current_task()
    blocked_published = asyncio.Event()
    finish_previous_dispatch = asyncio.Event()

    async def hold_blocked_wake(*args, **kwargs):
        run = await previous_wake(*args, **kwargs)
        if run["status"] == "blocked" and asyncio.current_task() is not test_task:
            blocked_published.set()
            await finish_previous_dispatch.wait()
        return run

    monkeypatch.setattr(service, "advance_run", hold_blocked_wake)
    definition = {
        "name": "Check evidence",
        "port_contract_version": 2,
        "input_contract": {
            "type": "object",
            "properties": {"evidence": {"type": "string"}},
            "required": ["evidence"],
        },
        "result_task_id": "judge",
        "tasks": [
            {
                "id": "judge",
                "name": "Judge",
                "transform": "decision_v1",
                "decision_provider_id": "judge",
                "decision": {"instructions": "Is the evidence complete?"},
                "inputs": {"state": {"input": "evidence"}},
            }
        ],
    }
    try:
        sdk = DecisionService(manager)
        with pytest.raises(DecisionError):
            await sdk.yes_no("Is the evidence complete?", state="Draft")
        provider_settings["decision_provider_id"] = "judge"
        assert await sdk.yes_no("Is the evidence complete?", state="Draft") is False
        assert (
            await sdk.yes_no(
                "Is the evidence complete?", state="Draft", provider_id="judge"
            )
            is False
        )
        provider_settings["decision_provider_id"] = "unavailable"
        asset = await service.save_asset("alice", {"definition": definition})
        fail_next = True
        started = await service.start_run(
            "alice", asset["asset_id"], {"version": 1, "inputs": {"evidence": "Draft"}}
        )
        await asyncio.wait_for(blocked_published.wait(), 5)
        run = await completed_run(service, started["run_id"])
        assert run["status"] == "blocked", run
        task = run["tasks"][0]
        assert task["status"] == "blocked"
        assert task["state"]["issues"][0]["code"] == "DECISION"
        await service.retry_task(
            "alice",
            task["run_id"],
            {
                "request_id": "retry-judge",
                "attempt_id": task["state"]["attempt_id"],
                "input_policy": "original",
            },
        )
        run = await completed_run(service, started["run_id"])
        assert run["status"] == "completed", run
        task = run["tasks"][0]
        assert task["result"] == {"matched": False, "probability": 0.2}
        assert task["state"]["decision"] == {
            "provider_id": "judge",
            "model": "decision-model",
        }
        assert len(requests) == 4
        assert requests[0][0] == (
            "/v1/decisions" if protocol == "openai" else "/v1/systemone"
        )
        package = await service.export_asset("alice", asset["asset_id"], 1)
        assert [(dep["kind"], dep["source_id"]) for dep in package["requirements"]] == [
            ("decision_providers", "judge"),
        ]
        workflow_env.inventory["decision_providers"] = [
            {"id": "judge-local", "name": "Target judge"}
        ]
        manager.inst_map["judge-local"] = provider
        assert not (await service.preview_import("alice", {"package": package}))[
            "ready"
        ]
        payload = {
            "package": package,
            "selections": [
                {
                    "kind": "decision_providers",
                    "source_id": "judge",
                    "target_id": "judge-local",
                }
            ],
        }
        assert (await service.preview_import("alice", payload))["ready"]
        imported = await service.import_asset("alice", payload)
        assert (
            imported["asset"]["definition"]["tasks"][0]["decision_provider_id"]
            == "judge-local"
        )
    finally:
        finish_previous_dispatch.set()
        await provider.terminate()
