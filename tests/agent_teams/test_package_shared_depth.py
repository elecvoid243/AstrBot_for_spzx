"""Public package validation for a shared intermediate with unequal paths."""

import copy
import json

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from tests.agent_teams.test_nested_packages import nested_package as nested_package
from tests.agent_teams.test_workflow_assets import workflow_env as workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "short_first,leaf_limit,expected_ready",
    [(True, 4, False), (False, 4, False), (True, 5, True), (False, 5, True)],
)
async def test_shared_intermediate_checks_descendants_on_every_path(
    nested_package, tmp_path, short_first, leaf_limit, expected_ready
):
    """Check absolute descendant limits through every shared intermediate path.

    Args:
        nested_package: Public services and a complete portable closure.
        tmp_path: Owned observations directory.
        short_first: Traverse the tighter short path before the deeper path.
        leaf_limit: The terminal role and task depth limit.
        expected_ready: Independently specified validity of both root paths.
    """
    env, package = nested_package
    leaf = package["assets"][0]
    leaf_role = next(
        role
        for role in package["roles"]
        if role["role_id"] == leaf["definition"]["role_id"]
    )
    leaf_role["definition"]["max_depth"] = leaf_limit
    leaf["definition"]["overrides"]["max_depth"] = leaf_limit
    team_template = copy.deepcopy(package["definition"])
    team_template["max_depth"] = 6
    for context in [team_template["coordinator"], *team_template["allowed_roles"]]:
        context["overrides"]["max_depth"] = 6
    coordinator_id = team_template["coordinator"]["role_id"]
    next(role for role in package["roles"] if role["role_id"] == coordinator_id)[
        "definition"
    ]["max_depth"] = 6
    package["definition"] = copy.deepcopy(team_template)
    package["definition"]["allowed_workflows"] = [
        {"asset_id": "long-graph", "version": 1, "result_task_id": "team"},
        {"asset_id": "short-graph", "version": 1, "result_task_id": "team"},
    ]
    if not short_first:
        package["definition"]["allowed_workflows"].reverse()
    for team_id, child_id, result_task in [
        ("shared-team", leaf["asset_id"], "result"),
        ("bridge-team", "deep-graph", "team"),
    ]:
        definition = copy.deepcopy(team_template)
        definition["name"] = team_id
        definition["allowed_workflows"] = [
            {"asset_id": child_id, "version": 1, "result_task_id": result_task}
        ]
        package["assets"].append(
            {"asset_id": team_id, "version": 1, "definition": definition}
        )
    for graph_id, team_id, limit in [
        ("short-graph", "shared-team", 3),
        ("long-graph", "bridge-team", 6),
        ("deep-graph", "shared-team", 6),
    ]:
        package["assets"].append(
            {
                "asset_id": graph_id,
                "version": 1,
                "definition": {
                    "name": graph_id,
                    "required_inputs": ["goal"],
                    "max_depth": limit,
                    "tasks": [
                        {
                            "id": "team",
                            "name": "Team",
                            "team": {"asset_id": team_id, "version": 1},
                            "inputs": {"goal": {"input": "goal"}},
                        }
                    ],
                },
            }
        )
    payload = {"package": package}
    before_assets = await env.service.list_assets("bob")
    before_roles = await env.roles.list_roles("bob")
    observations = {"payload": payload}
    try:
        preview = await env.service.preview_import("bob", payload)
    except RoleAssetError as error:
        observations["rejected"] = {"message": str(error), "fields": error.fields}
        (tmp_path / "observations.json").write_text(
            json.dumps(observations, indent=2), encoding="utf-8"
        )
        assert not expected_ready, observations
        with pytest.raises(RoleAssetError):
            await env.service.import_asset("bob", payload)
        assert await env.service.list_assets("bob") == before_assets
        assert await env.roles.list_roles("bob") == before_roles
        return
    observations["preview"] = preview
    if preview["ready"]:
        installed = await env.service.import_asset("bob", payload)
        observations["installed"] = installed
        observations["actual_readiness"] = await env.service.check_readiness(
            "bob", installed["asset"]["asset_id"], installed["asset"]["version"]
        )
    observations["assets"] = await env.service.list_assets("bob")
    observations["roles"] = await env.roles.list_roles("bob")
    (tmp_path / "observations.json").write_text(
        json.dumps(observations, indent=2), encoding="utf-8"
    )
    assert preview["ready"] == expected_ready, observations
    if expected_ready:
        assert len(preview["assets"]) == 7
        assert observations["installed"]["ready"], observations
        assert observations["actual_readiness"]["ready"], observations
