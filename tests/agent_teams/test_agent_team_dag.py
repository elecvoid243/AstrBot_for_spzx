# tests/agent_teams/test_agent_team_dag.py
"""Unit tests for pure DAG helpers."""

import pytest

from astrbot.dashboard.services.agent_team_dag import (
    TeamDAGError,
    downstream_of,
    render_task,
    topological_layers,
    validate_dag,
)

NODES = [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}, {"id": "n4"}]
EDGES = [
    {"from": "n1", "to": "n3"},
    {"from": "n2", "to": "n3"},
    {"from": "n3", "to": "n4"},
]


def test_layers_and_adjacency():
    adj = validate_dag(NODES, EDGES)
    assert adj["n1"] == ["n3"] and adj["n3"] == ["n4"]
    layers = topological_layers(NODES, EDGES)
    assert layers == [["n1", "n2"], ["n3"], ["n4"]]


def test_cycle_rejected():
    edges = EDGES + [{"from": "n4", "to": "n1"}]
    with pytest.raises(TeamDAGError, match="cycle"):
        validate_dag(NODES, edges)


def test_dangling_and_duplicate_rejected():
    with pytest.raises(TeamDAGError, match="unknown node"):
        validate_dag(NODES, [{"from": "nx", "to": "n1"}])
    with pytest.raises(TeamDAGError, match="duplicate"):
        validate_dag([{"id": "n1"}, {"id": "n1"}], [])


def test_downstream_transitive():
    assert sorted(downstream_of("n1", EDGES)) == ["n3", "n4"]
    assert downstream_of("n4", EDGES) == []


def test_render_task():
    out = render_task(
        "目标: {{input}}\n前驱结论: {{n1}}", "写诗", {"n1": "春天来了"}, max_length=100
    )
    assert out == "目标: 写诗\n前驱结论: 春天来了"


def test_render_unknown_placeholder():
    with pytest.raises(TeamDAGError, match="unknown placeholder"):
        render_task("{{nope}}", "x", {"n1": "y"}, max_length=10)


def test_render_truncates_tail():
    out = render_task("{{n1}}", "x", {"n1": "0123456789"}, max_length=4)
    assert out.endswith("6789") and "截断" in out
