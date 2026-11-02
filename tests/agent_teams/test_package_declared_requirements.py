"""Resource declarations exactly cover explicit source identities."""

import copy

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["missing", "extra", "duplicate", "missing_capability", "legacy_alias"]
)
async def test_tampered_requirements_are_rejected_without_installation(
    workflow_env, change
):
    """Requirements cannot omit source references or inject old alias fields.

    Args:
        workflow_env: Public asset services and resource catalog.
        change: Invalid declaration mutation.
    """
    env = workflow_env
    package = await env.service.export_asset("alice", env.asset["asset_id"], 1)
    if change == "missing":
        package["requirements"].pop()
    elif change == "extra":
        package["requirements"].append({"kind": "tools", "source_id": "unreferenced"})
    elif change == "duplicate":
        package["requirements"].append(copy.deepcopy(package["requirements"][0]))
    elif change == "missing_capability":
        next(item for item in package["requirements"] if item["kind"] == "profiles")[
            "capabilities"
        ] = []
    else:
        package["requirements"][0]["logical_slots"] = ["old"]
    with pytest.raises(RoleAssetError):
        await env.service.preview_import("bob", {"package": package})
    assert (await env.service.list_assets("bob"))["assets"] == []
    assert (await env.roles.list_roles("bob"))["roles"] == []


@pytest.mark.asyncio
async def test_explicit_overrides_are_rewritten_without_materializing_inheritance(
    workflow_env,
):
    """Distinct override references map once while null and empty stay distinct.

    Args:
        workflow_env: Public asset services and resource catalog.
    """
    env = workflow_env
    env.inventory["personas"] = [{"id": "source-persona"}, {"id": "local-persona"}]
    env.inventory["tools"] = [{"id": "source-tool"}, {"id": "local-tool"}]
    definition = copy.deepcopy(env.asset["definition"])
    definition["tasks"][0]["overrides"] = {
        "persona_id": "source-persona",
        "tools": ["source-tool"],
        "skills": None,
        "knowledge_bases": [],
    }
    asset = await env.service.save_asset("alice", {"definition": definition})
    package = await env.service.export_asset("alice", asset["asset_id"], 1)
    selections = [
        {
            "kind": item["kind"],
            "source_id": item["source_id"],
            "target_id": {"personas": "local-persona", "tools": "local-tool"}.get(
                item["kind"], item["source_id"]
            ),
        }
        for item in package["requirements"]
    ]
    installed = await env.service.import_asset(
        "bob", {"package": package, "selections": selections}
    )
    assert installed["asset"]["definition"]["tasks"][0]["overrides"] == {
        "persona_id": "local-persona",
        "tools": ["local-tool"],
        "skills": None,
        "knowledge_bases": [],
    }
