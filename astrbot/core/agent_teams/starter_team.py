"""Portable starter team and reusable role. Each owner can edit or remove them independently."""

RELEASE_EDITOR = {
    "name": "Release note editor",
    "description": "Turn supplied changes into concise release notes for end users.",
    "prompt": (
        "Use only the facts in the assigned goal. Write a short release note for its audience in the language of the goal. "
        "State what changed and why it matters. Flag missing facts rather than inventing them. "
        "Return the finished note with submit_task_result."
    ),
    "model_capabilities": ["tool_use"],
    "tools": [],
    "skills": [],
    "knowledge_bases": [],
}

RELEASE_TEAM = {
    "kind": "autonomous",
    "name": "Release notes desk",
    "coordinator": {
        "id": "coordinator",
        "role": {
            "name": "Managing editor",
            "prompt": (
                "Coordinate the release note requested in the goal. Use team_manage to create a "
                "member from the editor preset, dispatch the writing task with the supplied facts "
                "and audience, then wait for its submitted result. Check it against the goal and "
                "submit the final note with submit_task_result."
            ),
            "model_capabilities": ["tool_use"],
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
        },
        "installation": {"profile_id": "default"},
    },
    "allowed_roles": [
        {
            "id": "editor",
            "role": RELEASE_EDITOR,
            "installation": {"profile_id": "default"},
        },
    ],
    "required_inputs": ["goal"],
    "max_members": 2,
    "max_parallel": 2,
    "max_tasks": 4,
}
