"""Accounting through the real pipeline and independent process termination."""

import json
from collections import Counter

import pytest

from tests.agent_teams.test_team_workflow_restart import (
    test_reverse_workflow_survives_actual_process_loss as run_reverse_scenario,
)
from tests.agent_teams.test_workflow_checkpoint_recovery import (
    test_saved_model_plan_survives_forced_process_termination as run_crash_scenario,
)
from tests.agent_teams.test_workflow_loop_restart import (
    test_iteration_boundary_survives_process_termination as run_loop_scenario,
)
from tests.agent_teams.test_workflow_team_restart import (
    test_workflow_child_scope_survives_real_process_loss as run_child_scenario,
)


@pytest.mark.parametrize(
    "scenario", ["response", "request", "parameters", "tool_result", "started"]
)
def test_usage_retains_actual_deliveries_across_process_reconstruction(
    tmp_path, scenario
):
    """Saved and uncertain consumption survives and matches the external ledger.

    Args:
        tmp_path: Real SQLite and external ledger shared by independent processes.
        scenario: Actual model or tool boundary at which the process is killed.
    """
    run_crash_scenario(tmp_path, scenario)
    evidence = json.loads((tmp_path / "recovered.json").read_text(encoding="utf-8"))
    external = json.loads(
        (tmp_path / "external-ledger.json").read_text(encoding="utf-8")
    )
    before = evidence["before"]["usage"]["ledger"]
    after = evidence["after"]["usage"]["ledger"]
    assert before
    assert {r["receipt_id"] for r in before} <= {r["receipt_id"] for r in after}
    assert len({r["receipt_id"] for r in after}) == len(after)
    assert sum(r["kind"] == "model" for r in after) == len(external["requests"])
    assert sum(
        r["kind"] == "tool" and r["name"] == "external_evidence" for r in after
    ) == len(external["effects"])
    assert {r["attempt_id"] for r in after} == {
        evidence["before"]["state"]["attempt_id"]
    }
    if scenario == "parameters":
        assert evidence["after"]["usage"]["tokens"]["reported"] == 60
    if scenario in {"request", "started"}:
        assert evidence["after"]["usage"]["tokens"]["unknown_calls"] > 0


@pytest.mark.parametrize(
    "scenario",
    [
        "child_pipeline",
        "child_unknown",
        "reverse_pipeline",
        "reverse_unknown",
        "loop_post",
    ],
)
def test_root_ledger_includes_coordinator_nested_scopes_and_historical_rounds(
    tmp_path, scenario
):
    """Each real model delivery appears once in the root and its own subtree.

    Args:
        tmp_path: Durable database shared with independently terminated workers.
        scenario: Team, reverse workflow or historical loop iteration boundary.
    """
    if scenario.startswith("child_"):
        run_child_scenario(tmp_path, scenario)
    elif scenario.startswith("reverse_"):
        run_reverse_scenario(tmp_path, scenario)
    else:
        run_loop_scenario(tmp_path, scenario)
    evidence = json.loads(
        (
            tmp_path
            / ("completed.json" if scenario.endswith("pipeline") else "recovered.json")
        ).read_text(encoding="utf-8")
    )
    root = evidence["after"]
    ledger = root["usage"]["ledger"]
    assert len({r["receipt_id"] for r in ledger}) == len(ledger)
    if not scenario.startswith("loop_"):
        external = json.loads(
            (tmp_path / "external-ledger.json").read_text(encoding="utf-8")
        )
        # Explicit inputs may be produced by a prior independent root run.
        # Reading its artifact does not transfer that run's historical billing.
        input_producers = {
            ref["artifact_id"] for ref in root["snapshot"].get("input_artifacts", [])
        }
        deliveries = Counter(
            request["run_id"]
            for request in external["requests"]
            if request["run_id"] not in input_producers
        )
        assert (
            Counter(r["run_id"] for r in ledger if r["kind"] == "model") == deliveries
        )
        public_runs = [
            root,
            *root["scopes"],
            *root["tasks"],
            *[task for scope in root["scopes"] for task in scope["tasks"]],
        ]
        for scope in public_runs:
            subtree = {scope["run_id"]}
            # Follow public parent references, independent of the accounting ledger.
            while (
                descendants := {
                    run["run_id"]
                    for run in public_runs
                    if run["snapshot"].get("parent_run_id") in subtree
                }
                - subtree
            ):
                subtree.update(descendants)
            expected = Counter(
                {key: count for key, count in deliveries.items() if key in subtree}
            )
            scope_ledger = scope["usage"]["ledger"]
            assert len({r["receipt_id"] for r in scope_ledger}) == len(scope_ledger)
            assert (
                Counter(r["run_id"] for r in scope_ledger if r["kind"] == "model")
                == expected
            )
            assert scope["usage"]["model_calls"] == sum(expected.values())
            assert all(r in ledger for r in scope_ledger)
    else:
        assert root["usage"]["model_calls"] == 12
        rounds = root["loops"][0]["iterations"]
        assert [
            [task["usage"]["model_calls"] for task in iteration["tasks"]]
            for iteration in rounds
        ] == [[3, 3], [3, 3]]
        assert len({r["attempt_id"] for r in ledger}) == 4
