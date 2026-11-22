"""Workflow definitions and fixed expectations for the release browser exercise."""

from pathlib import Path

SAMPLES = (
    Path(__file__).resolve().parents[2] / "docs/public/examples/multi-document-report"
)
MODEL = "report-model"
REPORT = "Multi-document field report"
QUESTION = "Does the report identify both sources?"
FEEDBACK = "Add a Source index to the attachment with headings for both documents."
FACTS = {
    "a": {"source": "DOC-A", "heading": "Survey results", "value": 37},
    "b": {"source": "DOC-B", "heading": "Planning target", "value": 40},
}
TEXT = {"type": "string"}
REPORT_FIELDS = {
    "text": TEXT,
    "sources": {"type": "array", "items": TEXT},
    "files": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"file_id": TEXT},
            "required": ["file_id"],
        },
    },
}


def contract(fields, optional=()):
    """Declare required properties except those with an explicit default."""
    return {
        "type": "object",
        "properties": fields,
        "required": [key for key in fields if key not in optional],
    }


def definitions(child_ref):
    """Return the native report renderer and the parent graph for a fixed child.

    Args:
        child_ref: Installed child asset identity and fixed version.

    Returns:
        Child and parent workflow definitions.
    """
    rows = {"type": "array"}
    child_inputs = contract(
        {"results": rows, "feedback": {**TEXT, "default": "None"}}, ("feedback",)
    )
    child = {
        "name": "Write the sourced report",
        "port_contract_version": 2,
        "input_contract": child_inputs,
        "entry_task_ids": ["render"],
        "result_task_id": "report",
        "tasks": [
            {
                "id": "render",
                "name": "Render facts as JSON text",
                "position": {"x": 365, "y": 100},
                "transform": "json_text",
                "inputs": {"value": {"input": "results"}},
            },
            {
                "id": "report",
                "name": "Write report.md",
                "position": {"x": 680, "y": 100},
                "member": "reporter",
                "role": {
                    "name": "Reporter",
                    "prompt": "Use only the inline input. Never call read_task_artifact. Follow the requested tool sequence. When submit_task_result returns Result accepted, reply Done.",
                    "tools": [],
                    "skills": [],
                },
                "installation": {"profile_id": "default"},
                "task": (
                    "ATN180 REPORT\nExtracted facts: {facts}\nReviewer feedback: {feedback}\n"
                    "Call store_task_file now. Set filename to report.md. Write each factual finding on its own line using the format DOC-ID: exact heading, NUMBER households. "
                    "Copy both numbers from the extracted facts. "
                    "When reviewer feedback is None, omit the Source index. Otherwise follow the feedback. "
                    ""
                    "Wait for the file tool response. Then call submit_task_result with result_json containing "
                    "text (the short title Field report), sources (two source IDs in order), and files "
                    "(an array with an object containing the returned file_id)."
                ),
                "inputs": {
                    "facts": {"task_id": "render", "field": "text"},
                    "feedback": {"input": "feedback"},
                },
                "output_contract": contract(REPORT_FIELDS),
            },
        ],
        "edges": [{"source": "render", "target": "report"}],
    }
    tasks = []
    for key, y in (("a", 35), ("b", 275)):
        tasks.extend(
            (
                {
                    "id": f"read_{key}",
                    "name": f"Read DOC-{key.upper()}",
                    "position": {"x": 335, "y": y},
                    "transform": "read_document_v1",
                    "inputs": {"file": {"input": f"document_{key}"}},
                },
                {
                    "id": f"extract_{key}",
                    "name": f"Extract DOC-{key.upper()}",
                    "position": {"x": 615, "y": y},
                    "member": f"extractor_{key}",
                    "role": {
                        "name": "Document extractor",
                        "prompt": "Use only the inline input. Never call read_task_artifact. Follow the requested tool sequence. When submit_task_result returns Result accepted, reply Done.",
                        "tools": [],
                        "skills": [],
                    },
                    "installation": {"profile_id": "default"},
                    "task": (
                        "Read the following document. Call submit_task_result with a JSON "
                        "object with source (the bare DOC identifier without the Markdown #), heading (the section "
                        "heading without Markdown markup), and value (integer households). "
                        "Use only this Markdown document. Do not look for an artifact ID.\n{document}\n\n"
                        f"Exercise label: ATN180 EXTRACT {key.upper()}"
                    ),
                    "inputs": {"document": {"task_id": f"read_{key}", "field": "text"}},
                    "output_contract": contract(
                        {"source": TEXT, "heading": TEXT, "value": {"type": "integer"}}
                    ),
                },
            )
        )
    tasks.extend(
        (
            {
                "id": "join",
                "name": "Collect results in source order",
                "position": {"x": 900, "y": 155},
                "control": {"kind": "join"},
            },
            {
                "id": "call",
                "name": "Write the sourced report · v1",
                "position": {"x": 1190, "y": 155},
                "workflow": child_ref,
                "input_contract": child_inputs,
                "output_contract": contract(REPORT_FIELDS),
                "inputs": {"results": {"task_id": "join", "field": "results"}},
            },
            {
                "id": "review",
                "name": "Review exact report",
                "position": {"x": 1480, "y": 155},
                "human": {
                    "kind": "approval",
                    "question": QUESTION,
                    "artifact_task_id": "call",
                },
            },
        )
    )
    parent = {
        "name": REPORT,
        "port_contract_version": 2,
        "input_contract": contract(
            {
                f"document_{key}": {"type": "object", "format": "astrbot-file"}
                for key in FACTS
            }
        ),
        "entry_task_ids": ["read_a", "read_b"],
        "result_task_id": "call",
        "max_parallel": 2,
        "tasks": tasks,
        "edges": [
            {"source": source, "target": target}
            for source, target in (
                ("read_a", "extract_a"),
                ("read_b", "extract_b"),
                ("extract_a", "join"),
                ("extract_b", "join"),
                ("join", "call"),
                ("call", "review"),
            )
        ],
        "loops": [
            {
                "id": "review_report",
                "name": "Review and revise",
                "body": ["call", "review"],
                "max_iterations": 2,
                "exit_task_id": "review",
                "exit_condition": {"field": "approved", "value": True},
                "feedback": [
                    {
                        "source": "review",
                        "field": "text",
                        "target": "call",
                        "input": "feedback",
                    }
                ],
            }
        ],
    }
    return child, parent
