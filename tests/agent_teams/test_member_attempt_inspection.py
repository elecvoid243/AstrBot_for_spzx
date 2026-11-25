"""Inspect only evidence attributed to an autonomous member's attempt."""

import asyncio
import json

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_autonomous_assets import team_env as team_env


@pytest.mark.asyncio
async def test_inspection_keeps_unconfirmed_deliveries_out_of_attempt(team_env):
    """Inspect a live dispatch without mistaking delivery for model consumption.

    Args:
        team_env: Isolated services and the controlled runtime tool boundary.
    """
    env = team_env
    env.member_started, env.member_release = asyncio.Event(), asyncio.Event()

    async def coordinate(tools, binding):
        """Send a member a note and start its task.

        Args:
            tools: Runtime tools bound to the actual coordinator.
            binding: Coordinator execution identity.
        """
        manage = tools["team_manage"]
        member = json.loads(
            await manage.call(
                None,
                action="create_member",
                request_id="writer",
                name="Writer",
                preset_id="writer",
            )
        )
        await tools["team_communicate"].call(
            None,
            action="send",
            request_id="note",
            recipient_member_id=member["member_id"],
            text="Unconfirmed confidential note",
        )
        task = json.loads(
            await manage.call(
                None,
                action="dispatch",
                request_id="draft",
                member_id=member["member_id"],
                name="Draft",
                task="Preserve {{year}} literally",
            )
        )
        await manage.call(
            None, action="wait", request_id="wait", task_ids=[task["task_id"]]
        )

    env.script = coordinate
    root = await env.service.start_run(
        "alice", env.asset["asset_id"], {"version": 1, "inputs": {"goal": "Write"}}
    )
    try:
        await asyncio.wait_for(env.member_started.wait(), 5)
        run = await env.service.get_run("alice", root["run_id"])
        task = next(
            task for task in run["tasks"] if task["snapshot"].get("created_by_task_id")
        )
        attempt_id = task["state"]["attempt_id"]
        args = (root["run_id"], task["run_id"], attempt_id)
        evidence = await env.service.inspect_member_attempt("alice", *args)
        assert evidence["dispatch"]["task"] == "Preserve {{year}} literally"
        assert evidence["messages"] == []
        assert evidence["unconfirmed_deliveries"] == 1
        assert evidence["result_artifact"] is None
        with pytest.raises(RoleAssetError) as denied:
            await env.service.inspect_member_attempt("bob", *args)
        assert denied.value.status == 404
        with pytest.raises(RoleAssetError) as unrelated:
            await env.service.inspect_member_attempt(
                "alice", *args[:2], run["tasks"][0]["state"]["attempt_id"]
            )
        assert unrelated.value.status == 404
    finally:
        env.member_release.set()
