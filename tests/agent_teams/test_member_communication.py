"""Controlled member messages observed through public run and tool boundaries."""

import asyncio
import json

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_autonomous_assets import team_env


@pytest.mark.asyncio
async def test_dependency_consumer_can_share_only_its_pinned_version_with_author(team_env):
    """Return a readable dependency reference without gaining later versions.

    Args:
        team_env: Public services using real SQLite and registered runtime tools.
    """
    env = team_env
    ready, release = asyncio.Event(), asyncio.Event()
    observed = {}

    async def script(tools, binding):
        """Dispatch an author and its dependent reviewer through coordinator tools.

        Args:
            tools: The coordinator's authorized runtime tools.
            binding: Current coordinator execution identity.
        """
        try:
            members = [json.loads(await tools["team_manage"].call(None,
                action="create_member", request_id=name, name=name,
                role_id=env.writer["role_id"])) for name in ["Author", "Reviewer"]]
            source = json.loads(await tools["team_manage"].call(None, action="dispatch",
                request_id="source", member_id=members[0]["member_id"],
                name="Evidence", task="Produce evidence"))
            await tools["team_manage"].call(None, action="wait", request_id="source-wait",
                task_ids=[source["task_id"]])
            ref = (await env.service.get_run("alice", source["run_id"]))["result_ref"]
            env.member_started, env.member_release = asyncio.Event(), asyncio.Event()
            review = json.loads(await tools["team_manage"].call(None, action="dispatch",
                request_id="review", member_id=members[1]["member_id"], name="Review",
                task="Review evidence", dependencies=[source["task_id"]]))
            waiting = asyncio.create_task(tools["team_manage"].call(None, action="wait",
                request_id="review-wait", task_ids=[review["task_id"]]))
            await asyncio.wait_for(env.member_started.wait(), 5)
            caller = await env.service.get_run("alice", review["run_id"])
            attempt, execution = caller["state"]["attempt_id"], caller["state"]["execution_id"]
            content = await env.service.read_artifact("alice", ref["artifact_id"], 1,
                caller["run_id"], attempt, execution)
            assert content["content"]["text"].endswith("COMPLETE_END")
            assert caller["snapshot"]["input_artifacts"] == [ref]
            receipt = await env.service.communicate_member("alice", caller["run_id"],
                attempt, execution, {"action": "send", "request_id": "review-feedback",
                    "recipient_member_id": members[0]["member_id"],
                    "text": "Please review this exact dependency version", "artifact_refs": [ref]})
            later = await env.service.revise_artifact("alice", ref["artifact_id"], {
                "expected_version": 1, "request_id": "later", "content": {"text": "Private later version"}})
            for operation in [
                env.service.read_artifact("alice", later["artifact_id"], 2, caller["run_id"], attempt, execution),
                env.service.communicate_member("alice", caller["run_id"], attempt, execution, {
                    "action": "send", "request_id": "later-feedback", "recipient_member_id": members[0]["member_id"],
                    "text": "Cannot forward a newer version", "artifact_refs": [later]}),
            ]:
                with pytest.raises(RoleAssetError) as denied:
                    await operation
                assert denied.value.status == 403
            observed.update(receipt=receipt, ref=ref)
            env.member_release.set()
            await waiting
        except RoleAssetError as exc:
            observed["rejection"] = {"status": exc.status, "error": str(exc)}
        finally:
            ready.set()
            await release.wait()

    env.script = script
    run = await env.service.start_run("alice", env.asset["asset_id"],
        {"version": 1, "inputs": {"goal": "Review pinned evidence"}})
    try:
        await asyncio.wait_for(ready.wait(), 10)
        assert env.errors == []
        assert "receipt" in observed, observed
        current = await env.service.get_run("alice", run["run_id"])
        assert len(current["messages"]) == 1
        assert current["messages"][0]["message_id"] == observed["receipt"]["message_id"]
        assert current["messages"][0]["artifact_refs"] == [observed["ref"]]
        assert len(current["members"]) == 3
        assert len(current["tasks"]) == 3
    finally:
        release.set()


@pytest.mark.asyncio
async def test_message_delivery_is_durable_deduplicated_and_does_not_dispatch(team_env):
    """Send one correlated message without acquiring coordinator task authority.

    Args:
        team_env: Real SQLite services and the controlled execution port.
    """
    env = team_env
    delivered, release = asyncio.Event(), asyncio.Event()
    receipts = []

    async def script(tools, binding):
        """Keep the authorized coordinator active while delivery is observed.

        Args:
            tools: Runtime capabilities provided to this coordinator.
            binding: Actual attempt identity from the execution port.
        """
        member = json.loads(
            await tools["team_manage"].call(
                None,
                action="create_member",
                request_id="writer",
                name="Writer",
                role_id=env.writer["role_id"],
            )
        )
        payload = dict(
            action="send",
            request_id="question-one",
            recipient_member_id=member["member_id"],
            text="Please check the complete source. 中文 {literal}",
        )
        receipts.append(
            json.loads(await tools["team_communicate"].call(None, **payload))
        )
        receipts.append(
            json.loads(await tools["team_communicate"].call(None, **payload))
        )
        delivered.set()
        await release.wait()

    env.script = script
    run = await env.service.start_run(
        "alice", env.asset["asset_id"], {"version": 1, "inputs": {"goal": "Research"}}
    )
    await asyncio.wait_for(delivered.wait(), 5)
    observed = await env.service.get_run("alice", run["run_id"])
    assert receipts[0] == receipts[1]
    assert len(observed["tasks"]) == 1
    assert len(observed["messages"]) == 1
    message = observed["messages"][0]
    assert message["message_id"] == receipts[0]["message_id"]
    assert (
        message["sender_member_id"]
        == observed["tasks"][0]["snapshot"]["member"]["member_id"]
    )
    assert message["sender_task_run_id"] == observed["tasks"][0]["run_id"]
    assert message["scope_id"] == observed["state"]["scope_id"]
    assert message["text"] == "Please check the complete source. 中文 {literal}"
    assert message["consumption"] is None
    assert observed["tasks"][0]["messages"] == [message]
    reopened = type(env.service)(env.db, env.roles)
    assert (await reopened.get_run("alice", run["run_id"]))["messages"] == observed[
        "messages"
    ]
    release.set()


@pytest.mark.asyncio
async def test_member_scope_versions_and_late_authority_are_enforced(team_env):
    """Keep identities, visible versions and management authority separate.

    Args:
        team_env: Public services with real SQLite and independently bound callers.
    """
    env = team_env
    ready, release = asyncio.Event(), asyncio.Event()
    callers = []

    async def script(tools, binding):
        """Hold two independently authorized callers at the public execution seam.

        Args:
            tools: Current coordinator's runtime capabilities.
            binding: Actual member and attempt identity.
        """
        callers.append((tools, binding))
        if len(callers) == 2:
            ready.set()
        await release.wait()

    env.script = script
    runs = [await env.service.start_run("alice", env.asset["asset_id"],
             {"version": 1, "inputs": {"goal": goal}}) for goal in ["First", "Other"]]
    await asyncio.wait_for(ready.wait(), 5)
    tools, binding = callers[0]
    other_tools, _ = callers[1]
    members = [json.loads(await tools["team_manage"].call(None,
        action="create_member", request_id=name, name=name,
        role_id=env.writer["role_id"])) for name in ["Writer A", "Writer B"]]
    stranger = json.loads(await other_tools["team_manage"].call(None,
        action="create_member", request_id="other", name="Other instance",
        role_id=env.writer["role_id"]))
    forbidden = await tools["team_communicate"].call(None, action="send",
        request_id="cross-run", recipient_member_id=stranger["member_id"], text="Private")
    assert "Error:" in forbidden
    first = json.loads(await tools["team_manage"].call(None, action="dispatch",
        request_id="first-work", member_id=members[0]["member_id"], name="Evidence", task="Write"))
    await tools["team_manage"].call(None, action="wait", request_id="wait", task_ids=[first["task_id"]])
    other_source = json.loads(await other_tools["team_manage"].call(None, action="dispatch",
        request_id="other-source", member_id=stranger["member_id"], name="Other evidence", task="Write private evidence"))
    await other_tools["team_manage"].call(None, action="wait", request_id="other-wait", task_ids=[other_source["task_id"]])
    other_ref = (await env.service.get_run("alice", other_source["run_id"]))["result_ref"]
    revised_text = "Complete revised evidence. " * 4000 + "VERSION_TWO_END"
    ref = await env.service.revise_artifact("alice", first["run_id"], {
        "expected_version": 1, "request_id": "revision", "content": {"text": revised_text},
    })
    env.member_started, env.member_release = asyncio.Event(), asyncio.Event()
    recipient = json.loads(await tools["team_manage"].call(None, action="dispatch",
        request_id="recipient", member_id=members[1]["member_id"], name="Review", task="Review"))
    waiting = asyncio.create_task(tools["team_manage"].call(None, action="wait",
        request_id="wait-review", task_ids=[recipient["task_id"]]))
    await asyncio.wait_for(env.member_started.wait(), 5)
    receiver = await env.service.get_run("alice", recipient["run_id"])
    with pytest.raises(RoleAssetError) as denied:
        await env.service.read_artifact("alice", ref["artifact_id"], 2, receiver["run_id"],
            receiver["state"]["attempt_id"], receiver["state"]["execution_id"])
    assert denied.value.status == 403
    delivery = json.loads(await tools["team_communicate"].call(None,
        action="send", request_id="version-two", recipient_member_id=members[1]["member_id"],
        text="Read the reviewed version", artifact_refs=[ref]))
    content = await env.service.read_artifact("alice", ref["artifact_id"], 2,
        receiver["run_id"], receiver["state"]["attempt_id"], receiver["state"]["execution_id"])
    assert content["content"]["text"] == revised_text
    with pytest.raises(RoleAssetError) as denied_old:
        await env.service.read_artifact("alice", ref["artifact_id"], 1, receiver["run_id"],
            receiver["state"]["attempt_id"], receiver["state"]["execution_id"])
    assert denied_old.value.status == 403
    attack = await env.service.communicate_member("alice", receiver["run_id"],
        receiver["state"]["attempt_id"], receiver["state"]["execution_id"], {
            "action": "send", "request_id": "forward", "recipient_member_id": members[0]["member_id"],
            "text": "Same reviewed version", "artifact_refs": [ref],
        })
    assert attack["message_id"] != delivery["message_id"]
    with pytest.raises(RoleAssetError) as transfer:
        await env.service.communicate_member("alice", receiver["run_id"],
            receiver["state"]["attempt_id"], receiver["state"]["execution_id"], {
                "action": "send", "request_id": "hidden-version", "recipient_member_id": members[0]["member_id"],
                "text": "Hidden", "artifact_refs": [{"artifact_id": ref["artifact_id"], "version": 1}],
            })
    assert transfer.value.status == 403
    with pytest.raises(RoleAssetError) as cross_scope:
        await env.service.communicate_member("alice", receiver["run_id"],
            receiver["state"]["attempt_id"], receiver["state"]["execution_id"], {
                "action": "send", "request_id": "cross-scope-artifact", "recipient_member_id": members[0]["member_id"],
                "text": "Foreign scope remains private", "artifact_refs": [other_ref],
            })
    assert cross_scope.value.status == 403
    caller = await env.service.get_run("alice", binding.run_id)
    for attempt, owner in [("old-attempt", caller["state"]["execution_id"]),
                           (caller["state"]["attempt_id"], "old-executor")]:
        with pytest.raises(RoleAssetError) as stale:
            await env.service.communicate_member("alice", binding.run_id, attempt, owner, {
                "action": "send", "request_id": "version-two", "recipient_member_id": members[1]["member_id"],
                "text": "Read the reviewed version", "artifact_refs": [ref],
            })
        assert stale.value.status == 409
    member_binding = next(b for b, _ in env.observations if b.run_id == receiver["run_id"])
    from astrbot.core.agent_team_tools import AgentTeamToolRegistry
    member_tools = {tool.name for tool in AgentTeamToolRegistry.get_tools(member_binding.umo)}
    assert "team_communicate" in member_tools
    assert "team_manage" not in member_tools
    env.member_release.set()
    await waiting
    await env.service.control_run("alice", runs[0]["run_id"], {"action": "cancel", "request_id": "stop"})
    with pytest.raises(RoleAssetError) as cancelled:
        await env.service.communicate_member("alice", binding.run_id, caller["state"]["attempt_id"],
            caller["state"]["execution_id"], {"action": "send", "request_id": "version-two",
                "recipient_member_id": members[1]["member_id"], "text": "Read the reviewed version", "artifact_refs": [ref]})
    assert cancelled.value.status == 409
    release.set()
