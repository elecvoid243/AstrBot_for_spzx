"""Resource-independent first-run example using the native V2 graph format."""

_BRIEF = {
    "type": "object",
    "properties": {"project": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["project", "summary"],
}

RELEASE_BRIEF = {
    "name": "Release brief",
    "port_contract_version": 2,
    "input_contract": {
        **_BRIEF,
        "properties": {
            "project": {"type": "string", "default": "Agent Teams"},
            "summary": {"type": "string", "default": "Ready for release."},
        },
    },
    "entry_task_ids": ["prepare"],
    "result_task_id": "deliver",
    "tasks": [
        {
            "id": "prepare",
            "name": "Prepare brief",
            "transform": "map_fields",
            "input_contract": _BRIEF,
            "output_contract": _BRIEF,
            "inputs": {
                "project": {"input": "project"},
                "summary": {"input": "summary"},
            },
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "review",
            "name": "Review brief",
            "human": {
                "kind": "approval",
                "question": "Approve this release brief?",
                "artifact_task_id": "prepare",
            },
            "position": {"x": 320, "y": -190},
        },
        {
            "id": "deliver",
            "name": "Deliver brief",
            "transform": "map_fields",
            "input_contract": _BRIEF,
            "output_contract": _BRIEF,
            "inputs": {
                "project": {"task_id": "prepare", "field": "project"},
                "summary": {"task_id": "prepare", "field": "summary"},
            },
            "position": {"x": 640, "y": 0},
        },
    ],
    "edges": [
        {"source": "prepare", "target": "review"},
        {
            "source": "review",
            "target": "deliver",
            "condition": {"field": "approved", "value": True},
        },
    ],
}
