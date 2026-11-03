"""Coordinator capabilities bound to the existing durable task attempt."""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolRecovery
from astrbot.core.agent_teams.assets import RoleAssetError


class TeamOperationRequest(BaseModel):
    """An idempotent coordinator decision within one frozen team run."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    action: Literal["create_member", "dispatch", "call_workflow", "wait", "inspect"]
    request_id: str = Field(min_length=1, max_length=128)
    preset_id: str | None = Field(default=None, min_length=1, max_length=128)
    member_id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    task: str | None = Field(default=None, min_length=1, max_length=100_000)
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    task_ids: list[str] = Field(default_factory=list, max_length=100)
    output_contract: dict | None = None
    asset_id: str | None = Field(default=None, min_length=1, max_length=128)
    version: int | None = Field(default=None, ge=1)
    inputs: dict[str, Annotated[str, StringConstraints(strip_whitespace=False)]] = (
        Field(default_factory=dict, max_length=30)
    )
    input_artifacts: list[dict] = Field(default_factory=list, max_length=30)


class TeamManageTool(FunctionTool):
    """Expose dynamic task sourcing without a second execution lifecycle."""

    def __init__(
        self,
        manage: Callable[[dict], Awaitable[dict]],
        allowed_roles: dict,
        allowed_workflows: dict | None = None,
    ):
        """Bind a coordinator tool to a fenced task attempt.

        Args:
            manage: Public runtime operation validating authority and constraints.
            allowed_roles: Frozen permitted presets, shown to the coordinator.
            allowed_workflows: Explicit pinned workflow inputs and return contracts.
        """
        super().__init__(
            name="team_manage",
            description=(
                "Create allowed preset instances, dispatch tasks, wait for selected task_ids, "
                "or inspect members, tasks and complete results. Use a unique request_id "
                "per decision; repeating the same decision is safe. create_member requires "
                "preset_id and name; dispatch requires member_id, name and task, with optional "
                "dependencies and output_contract. wait requires nonempty task_ids and "
                "releases execution capacity while members work. inspect returns full "
                "validated outputs, never just summaries. Finish with submit_task_result "
                "only after all member tasks have valid results. Allowed presets: "
                + json.dumps(
                    {
                        identity: frozen["role"]["definition"]["name"]
                        for identity, frozen in allowed_roles.items()
                    }
                )
                + ". call_workflow requires an allowed asset_id, exact version, name, "
                "and all declared text inputs; optional input_artifacts must be visible "
                "exact versions and record provenance only. Child tasks receive the "
                "declared text inputs, not permission to read these artifacts or files. "
                "Read selected content first and pass it explicitly in inputs. "
                "It returns task_id; wait for that task before reading "
                "its result. Allowed workflows: "
                + json.dumps(allowed_workflows or {}, ensure_ascii=False)
            ),
            parameters=TeamOperationRequest.model_json_schema(),
            recovery=ToolRecovery(safe_retry=True),
        )
        self.manage = manage

    async def call(self, context: ContextWrapper, **kwargs) -> str:
        """Return persisted effects or actionable field errors.

        Args:
            context: Current main-pipeline context.
            **kwargs: Explicit coordinator operation and request identity.

        Returns:
            Complete operation response or a runtime rejection.
        """
        try:
            return json.dumps(await self.manage(kwargs), ensure_ascii=False)
        except RoleAssetError as exc:
            return f"Error: {exc}; fields={exc.fields}"


class MemberCommunicationRequest(BaseModel):
    """A scoped message identity, literal content and exact version references."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    action: Literal["inspect", "send"]
    request_id: str = Field(min_length=1, max_length=128)
    recipient_member_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=100_000)
    artifact_refs: list[dict] = Field(default_factory=list, max_length=30)


class TeamCommunicateTool(FunctionTool):
    """Allow member questions and evidence sharing without task-management authority."""

    def __init__(self, communicate: Callable[[dict], Awaitable[dict]]):
        """Bind communication to the current fenced execution.

        Args:
            communicate: Public runtime operation validating recipients and artifacts.
        """
        super().__init__(
            name="team_communicate",
            description=(
                "inspect lists members in your execution scope. send delivers literal text "
                "to recipient_member_id with optional artifact_refs containing artifact_id "
                "and exact version. Use a unique request_id; duplicate sends are safe. "
                "Only visible artifacts can be shared. Messages are consumed at the "
                "recipient's next safe model boundary, never create tasks or complete work. "
                "Read complete referenced content with read_task_artifact."
            ),
            parameters=MemberCommunicationRequest.model_json_schema(),
            recovery=ToolRecovery(safe_retry=True),
        )
        self.communicate = communicate

    async def call(self, context: ContextWrapper, **kwargs) -> str:
        """Return the durable receipt or an explicit authorization rejection.

        Args:
            context: Current main-pipeline context.
            **kwargs: Message operation with a stable request identity.

        Returns:
            JSON receipt or actionable rejection text.
        """
        try:
            return json.dumps(await self.communicate(kwargs), ensure_ascii=False)
        except RoleAssetError as exc:
            return f"Error: {exc}; fields={exc.fields}"
