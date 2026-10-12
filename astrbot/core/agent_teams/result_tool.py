"""Contracted result submission and complete reads of pinned task artifacts."""

import base64
import binascii
import json
from collections.abc import Awaitable, Callable

from jsonschema import Draft202012Validator

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent_teams.assets import RoleAssetError


def validate_result(contract: dict | None, payload: dict) -> list[dict]:
    """Return contract field errors shared by submission and revision.

    Args:
        contract: The immutable output schema, or the default text contract.
        payload: Complete task result to validate.

    Returns:
        Specific field errors; an empty list means the result is consumable.
    """
    fields = []
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError):
        return [
            {"path": "result", "code": "INVALID", "message": "Submit finite JSON data"}
        ]
    if contract is not None:
        for issue in Draft202012Validator(contract).iter_errors(payload):
            path = ["result", *map(str, issue.absolute_path)]
            if issue.validator == "required":
                for name in issue.validator_value:
                    field = {
                        "path": ".".join([*path, name]),
                        "code": "required",
                        "message": f"'{name}' is a required property",
                    }
                    if name not in issue.instance and field not in fields:
                        fields.append(field)
                continue
            fields.append(
                {
                    "path": ".".join(path),
                    "code": str(issue.validator),
                    "message": issue.message,
                }
            )
    else:
        if not isinstance(payload.get("text"), str) or not payload["text"].strip():
            fields.append(
                {
                    "path": "result.text",
                    "code": "REQUIRED",
                    "message": "Provide non-empty text",
                }
            )
        fields.extend(
            {
                "path": f"result.{name}",
                "code": "UNKNOWN",
                "message": "Field is not declared in the text contract",
            }
            for name in payload
            if name != "text"
        )
    if "files" in payload:
        files = payload["files"]
        if not isinstance(files, list):
            fields.append(
                {
                    "path": "result.files",
                    "code": "INVALID",
                    "message": "Use an array of stable file references",
                }
            )
        else:
            for index, ref in enumerate(files):
                if (
                    not isinstance(ref, dict)
                    or set(ref) != {"file_id"}
                    or not isinstance(ref["file_id"], str)
                ):
                    fields.append(
                        {
                            "path": f"result.files.{index}.file_id",
                            "code": "INVALID",
                            "message": "Use a stable file_id returned by store_task_file",
                        }
                    )
    return fields


class ReadArtifactTool(FunctionTool):
    """Read full immutable inputs within the current task's pinned capability."""

    def __init__(self, read: Callable[[str, int, str | None], Awaitable[dict]]):
        """Bind reads to the current consumer attempt.

        Args:
            read: Public service read with authorization and durable read recording.
        """
        super().__init__(
            name="read_task_artifact",
            description="Read full content of a pinned input artifact. Supply file_id to read a referenced file as UTF-8 text or base64 bytes.",
            parameters={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "version": {"type": "integer", "minimum": 1},
                    "file_id": {"type": "string"},
                },
                "required": ["artifact_id", "version"],
                "additionalProperties": False,
            },
        )
        self.read = read

    async def call(
        self,
        context: ContextWrapper,
        artifact_id: str = "",
        version: int = 0,
        file_id: str | None = None,
        **kwargs,
    ) -> str:
        """Return full content without truncation.

        Args:
            context: Existing Agent context.
            artifact_id: Declared input artifact identity.
            version: Exact version fixed when this task started.
            file_id: Optional file belonging to that input version.
            **kwargs: Extra tool arguments rejected by the Agent schema.

        Returns:
            Complete artifact JSON or an explicit capability error.
        """
        try:
            return json.dumps(
                await self.read(artifact_id, version, file_id), ensure_ascii=False
            )
        except RoleAssetError as exc:
            return f"Error: {exc}; fields={exc.fields}"


class StoreTaskFileTool(FunctionTool):
    """Publish complete file content without adding host filesystem privileges."""

    def __init__(self, store: Callable[[str, bytes], Awaitable[dict]]):
        """Bind file writes to the current task attempt.

        Args:
            store: Public service operation accepting a filename and complete bytes.
        """
        super().__init__(
            name="store_task_file",
            description="Store complete UTF-8 or base64 file content and return a stable file_id for result.files. Use existing authorized tools to read generated files. Submit only after storage succeeds.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string", "enum": ["utf-8", "base64"]},
                },
                "required": ["filename", "content"],
                "additionalProperties": False,
            },
        )
        self.store = store

    async def call(
        self,
        context: ContextWrapper,
        filename: str = "",
        content: str = "",
        encoding: str = "utf-8",
        **kwargs,
    ) -> str:
        """Publish ready bytes before returning a durable reference.

        Args:
            context: Existing Agent context.
            filename: Display name without directories.
            content: Complete file content, obtained through authorized task tools.
            encoding: UTF-8 text or base64 bytes.
            **kwargs: Extra tool arguments rejected by the Agent schema.

        Returns:
            Stable file metadata or a specific readiness error.
        """
        try:
            if kwargs or encoding not in {"utf-8", "base64"}:
                return "Error: Provide filename, complete content and utf-8 or base64 encoding."
            if len(content) > 70 * 1024 * 1024:
                return "Error: Files must be at most 50 MiB."
            data = (
                content.encode("utf-8")
                if encoding == "utf-8"
                else base64.b64decode(content, validate=True)
            )
            return json.dumps(await self.store(filename, data))
        except (TypeError, ValueError, binascii.Error, RoleAssetError) as exc:
            return f"Error: {exc}"


class SubmitTaskResultTool(FunctionTool):
    """Accept a durable submission intent for the currently bound attempt."""

    def __init__(
        self, accept: Callable[[dict], Awaitable[None]], contract: dict | None = None
    ):
        """Bind this tool to one authorized task attempt.

        Args:
            accept: Public service operation validating and persisting the intent.
            contract: Pinned task output schema, or the default text contract.
        """
        super().__init__(
            name="submit_task_result",
            description="Submit the final task result according to its output contract. Correct field errors within the remaining execution budget.",
            parameters=contract
            or {
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )
        self.accept = accept
        self.contract = contract

    async def call(self, context: ContextWrapper, **kwargs) -> str:
        """Validate and persist; the runtime completes only after all tools finish.

        Args:
            context: Existing Agent context.
            **kwargs: Complete result fields validated against the frozen contract.

        Returns:
            Acceptance or field errors for correction within the current budget.
        """
        try:
            await self.accept(kwargs)
        except RoleAssetError as exc:
            return f"Error: {exc}; fields={exc.fields}"
        return "Result accepted."
