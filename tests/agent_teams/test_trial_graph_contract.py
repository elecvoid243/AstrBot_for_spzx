"""Trusted boundary contracts for compiled trial graphs."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.graph import GraphDefinition, GraphTask, validate_graph


def test_external_field_contract_is_only_available_to_trusted_compilation():
    source = GraphTask.model_validate(
        {
            "id": "upstream",
            "name": "Upstream",
            "member": "upstream",
            "role_id": "role",
            "role_version": 1,
            "task": "Produce",
            "output_contract": {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
            },
        }
    )
    graph = GraphDefinition.model_validate(
        {
            "name": "Trial",
            "tasks": [
                {
                    "id": "condition",
                    "name": "Condition",
                    "control": {"kind": "condition", "operator": "gt", "value": 3},
                    "inputs": {"value": {"task_id": "upstream", "field": "score"}},
                }
            ],
        }
    )
    with pytest.raises(RoleAssetError):
        validate_graph(graph)
    validate_graph(graph, external_sources={"upstream": source})
    graph.tasks[0].inputs["value"].field = "missing"
    with pytest.raises(RoleAssetError):
        validate_graph(graph, external_sources={"upstream": source})
