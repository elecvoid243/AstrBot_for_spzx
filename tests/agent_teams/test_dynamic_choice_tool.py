"""Team choices use trusted invocation bindings and fail closed without recovery."""

from unittest.mock import AsyncMock

import pytest

from astrbot.builtin_stars.ask_user_choice.ask_user_choice_tool import AskUserChoiceTool
from astrbot.core.agent.tool import current_tool_invocation
from astrbot.core.agent_team_execution import (
    CheckpointRecoveryError,
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.tools.interactive_choice_registry import registry


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["request_human", "save_checkpoint", "record_id"])
async def test_team_choice_cannot_fall_back_to_an_ordinary_chat_wait(missing):
    """Reject an executor without the original durable tool boundary.

    Args:
        missing: Required execution capability to omit.
    """
    binding = NodeExecutionBinding(
        run_id="task",
        team_id="asset",
        member_id="member",
        node_id="node",
        umo="webchat:FriendMessage:member",
        owner_username="alice",
        config_id="default",
        profile_snapshot={},
        request_human=AsyncMock(),
        save_checkpoint=AsyncMock(),
    )
    if missing != "record_id":
        setattr(binding, missing, None)
    execution_token = current_team_execution.set(binding)
    call_token = current_tool_invocation.set(
        None if missing == "record_id" else "trusted-call"
    )
    pending_before = dict(registry._pending)
    try:
        with pytest.raises(CheckpointRecoveryError, match="durably resume"):
            await AskUserChoiceTool().call(
                None,
                prompt="Choose",
                options=[{"id": "A", "label": "First"}, {"id": "B", "label": "Second"}],
            )
        assert isinstance(binding.recovery_error, CheckpointRecoveryError)
        assert registry._pending == pending_before
    finally:
        current_tool_invocation.reset(call_token)
        current_team_execution.reset(execution_token)


@pytest.mark.asyncio
async def test_team_choice_ignores_model_supplied_execution_identities():
    """Only the runner's context binding can identify a persistent question."""
    received = []

    async def request(record_id, spec):
        """Capture the public tool decision callback.

        Args:
            record_id: Runner-owned tool record.
            spec: Validated user-visible question.

        Returns:
            The selected option in the ordinary tool result protocol.
        """
        received.append((record_id, spec))
        return {"choice_id": "B", "free_text": "Approved details"}

    binding = NodeExecutionBinding(
        run_id="task",
        team_id="asset",
        member_id="member",
        node_id="node",
        umo="webchat:FriendMessage:member",
        owner_username="alice",
        config_id="default",
        profile_snapshot={},
        request_human=request,
        save_checkpoint=AsyncMock(),
    )
    execution_token = current_team_execution.set(binding)
    call_token = current_tool_invocation.set("trusted-call")
    tool = AskUserChoiceTool()
    try:
        result = await tool.call(
            None,
            prompt="Choose",
            options=[{"id": "A", "label": "First"}, {"id": "B", "label": "Second"}],
            run_id="another-task",
            tool_record_id="forged",
            request_id="forged",
            recommended_choice_id="B",
            recommendation_reason="More evidence",
        )
        assert (
            result == "User selected: Second (id=B)\nAdditional note: Approved details"
        )
        assert received[0][0] == "trusted-call"
        assert not {"run_id", "tool_record_id", "request_id"} & received[0][1].keys()
        assert received[0][1]["recommendation"] == {
            "value": "B",
            "reason": "More evidence",
        }
        assert tool.recovery is None
        assert "recommended_choice_id" not in tool.parameters["properties"]
        for invalid in ["__free_text__", "x" * 129]:
            error = await tool.call(
                None,
                prompt="Choose",
                options=[
                    {"id": invalid, "label": "First"},
                    {"id": "B", "label": "Second"},
                ],
            )
            assert error.startswith("Error:")
        assert len(received) == 1
    finally:
        current_tool_invocation.reset(call_token)
        current_team_execution.reset(execution_token)
