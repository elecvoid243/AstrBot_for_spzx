"""Fixed workflow definitions for the report debug acceptance."""

from tests.agent_teams.report_release_fixture import TEXT, contract

FILE = {"type": "object", "format": "astrbot-file"}
BOOLEAN = {"type": "boolean"}
SIMULATED_RECEIPT = {"record": {"count": 192}}
DELIVERY_TARGET = "team/reports"
ARCHIVE_TARGET = "team/archive"


def citation_review(good_pdf, component, binding):
    """Build the fixed citation workflow used by each review round.

    Args:
        good_pdf: Exact PDF accepted by the frozen citation output contract.
        component: Controlled connector registered in the disposable Dashboard.
        binding: Knowledge base bound to the connector on this installation.

    Returns:
        The fixed review workflow definition.
    """
    receipt = component["output_contract"]
    fields = {
        "draft": TEXT,
        "pdf": FILE,
        "next_pdf": FILE,
        "revised": BOOLEAN,
        "repeat": BOOLEAN,
        **receipt["properties"],
        "delivery_target": TEXT,
        "archive_target": TEXT,
    }
    check_contract = contract(fields)
    checked = contract({**fields, "pdf": {**FILE, "const": good_pdf}})
    delivery = {
        "transform": "plugin_component_v1",
        "component": component["reference"],
        "knowledge_base_id": binding,
        "input_contract": component["input_contract"],
        "output_contract": receipt,
    }
    return {
        "name": "Citation review",
        "port_contract_version": 2,
        "input_contract": check_contract,
        "entry_task_ids": ["prepare"],
        "result_task_id": "check",
        "tasks": [
            {
                "id": "prepare",
                "name": "Prepare citation evidence",
                "position": {"x": 350, "y": 100},
                "transform": "map_fields",
                "input_contract": check_contract,
                "output_contract": check_contract,
                "inputs": {name: {"input": name} for name in fields},
            },
            {
                "id": "check",
                "name": "Check citations",
                "position": {"x": 650, "y": 100},
                "transform": "map_fields",
                "input_contract": check_contract,
                "output_contract": checked,
                "inputs": {
                    name: {"task_id": "prepare", "field": name} for name in fields
                },
            },
            {
                "id": "unused",
                "name": "Export an optional appendix",
                "position": {"x": 650, "y": 375},
                "transform": "json_text",
                "inputs": {"value": {"task_id": "prepare", "field": "draft"}},
            },
            {
                "id": "approve",
                "name": "Approve the exact report",
                "position": {"x": 960, "y": 100},
                "human": {
                    "kind": "approval",
                    "question": "Approve the checked report and citations?",
                    "artifact_task_id": "check",
                },
            },
            {
                "id": "send-gate",
                "name": "Send only a revised report",
                "position": {"x": 1270, "y": 100},
                "control": {"kind": "condition", "value": True},
                "inputs": {"value": {"task_id": "check", "field": "revised"}},
            },
            {
                **delivery,
                "id": "deliver",
                "name": "Deliver approved report",
                "position": {"x": 1580, "y": 100},
                "inputs": {
                    "record": {"task_id": "check", "field": "record"},
                    "target": {"task_id": "check", "field": "delivery_target"},
                },
            },
            {
                "id": "archive-record",
                "name": "Record receipt for archive",
                "position": {"x": 1880, "y": 100},
                "transform": "map_fields",
                "input_contract": receipt,
                "output_contract": receipt,
                "inputs": {"record": {"task_id": "deliver", "field": "record"}},
            },
            {
                **delivery,
                "id": "archive-write",
                "name": "Send rehearsal receipt to archive",
                "position": {"x": 2180, "y": 100},
                "inputs": {
                    "record": {"task_id": "archive-record", "field": "record"},
                    "target": {"task_id": "check", "field": "archive_target"},
                },
            },
        ],
        "edges": [
            {"source": "prepare", "target": "check"},
            {
                "source": "prepare",
                "target": "unused",
                "condition": {"field": "draft", "value": "Include appendix"},
            },
            {"source": "check", "target": "approve"},
            {
                "source": "approve",
                "target": "send-gate",
                "condition": {"field": "approved", "value": True},
            },
            {
                "source": "send-gate",
                "target": "deliver",
                "condition": {"field": "matched", "value": True},
            },
            {"source": "deliver", "target": "archive-record"},
            {
                "source": "archive-record",
                "target": "archive-write",
                "condition": {"field": "record.count", "value": 192},
            },
        ],
    }


def report_with_review(report, child, child_id):
    """Add fixed citation review rounds after the report's one initial draft.

    Args:
        report: Base multi-document report definition with its renderer pinned.
        child: Frozen citation review definition.
        child_id: Published citation review asset identity.

    Returns:
        The parent definition that feeds the next PDF into the second call.
    """
    inputs = child["input_contract"]
    checked = next(
        task["output_contract"] for task in child["tasks"] if task["id"] == "check"
    )
    original_review = next(task for task in report["tasks"] if task["id"] == "review")
    review_inputs = {
        **{name: {"input": name} for name in inputs["properties"] if name != "draft"},
        "draft": {"task_id": "call", "field": "text"},
    }
    return {
        **report,
        "input_contract": contract(
            {
                **report["input_contract"]["properties"],
                **{
                    name: inputs["properties"][name]
                    for name in review_inputs
                    if name != "draft"
                },
            }
        ),
        "result_task_id": "review",
        "tasks": [
            *(task for task in report["tasks"] if task["id"] != "review"),
            {
                "id": "review",
                "name": "Review report citations · v3",
                "position": original_review["position"],
                "workflow": {"asset_id": child_id, "version": 3},
                "input_contract": inputs,
                "output_contract": checked,
                "inputs": review_inputs,
            },
        ],
        "loops": [
            {
                "id": "citation-rounds",
                "name": "Citation rounds",
                "body": ["review"],
                "max_iterations": 2,
                "exit_task_id": "review",
                "exit_condition": {"field": "revised", "value": True},
                "feedback": [
                    {
                        "source": "review",
                        "field": field,
                        "target": "review",
                        "input": name,
                    }
                    for field, name in (("next_pdf", "pdf"), ("repeat", "revised"))
                ],
            }
        ],
    }
