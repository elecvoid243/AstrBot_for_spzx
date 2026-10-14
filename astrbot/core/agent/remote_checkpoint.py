"""Durable remote identities within the existing team Attempt checkpoint."""

import asyncio
import copy
import uuid
from collections.abc import Awaitable, Callable

from astrbot.core.agent.call_checkpoint import dump_call_response, load_call_response
from astrbot.core.agent_team_execution import (
    CheckpointRecoveryError,
    NodeExecutionBinding,
)
from astrbot.core.config.agent_runner import get_agent_runner_capabilities
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


class RemoteAgentCheckpoint:
    """Save external call boundaries without claiming remote internal recovery."""

    def __init__(
        self, binding: NodeExecutionBinding, runner: dict, request: ProviderRequest
    ):
        """Bind remote work to its existing durable execution and effective input.

        Args:
            binding: Current task and Attempt with fenced persistence callbacks.
            runner: Effective runner configuration; credentials are not retained.
            request: Effective request after the normal plugin hook.
        """
        self.binding = binding
        self.capabilities = get_agent_runner_capabilities(runner)
        self.timeout = float(runner.get("config", {}).get("timeout", 60))
        self.data = (
            copy.deepcopy(binding.checkpoint)
            if binding.checkpoint
            else {
                "format_version": 1,
                "invocation_id": uuid.uuid4().hex,
                "phase": "request",
                "messages": [],
                "tool_calls": [],
                "started_tools": [],
                "steps_used": 0,
                "step_budget_used": 0,
                "model_calls": 0,
                "remote": {
                    "runner": self.capabilities,
                    "identity": None,
                    "receipt_id": None,
                },
                "request": {
                    "prompt": request.prompt,
                    "system_prompt": request.system_prompt,
                    "contexts": request.contexts,
                    "image_urls": request.image_urls,
                    "audio_urls": request.audio_urls,
                    "extra_user_content_parts": [
                        part.model_dump_for_context()
                        if hasattr(part, "model_dump_for_context")
                        else part
                        for part in request.extra_user_content_parts
                    ],
                },
            }
        )

    async def save(self) -> None:
        """Persist before any dependent remote operation or result processing.

        Raises:
            CheckpointRecoveryError: The checkpoint was not durably stored.
        """
        assert self.binding.save_checkpoint is not None
        try:
            await self.binding.save_checkpoint(copy.deepcopy(self.data))
        except Exception as exc:
            error = CheckpointRecoveryError(
                "Remote execution checkpoint could not be saved; result requires confirmation"
            )
            self.binding.recovery_error = error
            raise error from exc

    async def before_request(self) -> None:
        """Save intent and mark possible external creation before delivering it.

        Raises:
            CheckpointRecoveryError: A previously started call cannot be replayed.
        """
        if self.data["phase"] != "request":
            error = CheckpointRecoveryError(
                "Remote execution has already started; result requires confirmation"
            )
            self.binding.recovery_error = error
            raise error
        await self.save()
        if self.binding.admit_work:
            await self.binding.admit_work()
        receipt_id = uuid.uuid4().hex
        if self.binding.begin_usage:
            await self.binding.begin_usage(
                {
                    "receipt_id": receipt_id,
                    "invocation_id": self.data["invocation_id"],
                    "kind": "model",
                    "name": self.capabilities["label"],
                }
            )
        self.data["remote"]["receipt_id"] = receipt_id
        self.data.update(
            phase="remote_started", model_calls=1, steps_used=1, step_budget_used=1
        )
        await self.save()

    async def save_identity(self, identity: dict) -> None:
        """Save exact run identity before consuming dependent remote events.

        Args:
            identity: Provider run identity; a conversation ID alone is insufficient.

        Raises:
            CheckpointRecoveryError: The stream changed identity within one call.
        """
        previous = self.data["remote"]["identity"]
        if not identity or any(
            not isinstance(value, str) or not value.strip()
            for value in identity.values()
        ):
            error = CheckpointRecoveryError(
                "Remote stream did not return a reliable run identity"
            )
            self.binding.recovery_error = error
            raise error
        if previous and previous != identity:
            error = CheckpointRecoveryError(
                "Remote stream changed its original run identity"
            )
            self.binding.recovery_error = error
            raise error
        self.data["remote"]["identity"] = identity
        self.data["phase"] = "remote_running"
        await self.save()

    async def complete(self, response: LLMResponse, raw: dict | str) -> None:
        """Save the complete original response and its unmodified remote evidence.

        Args:
            response: Effective response before plugin completion hooks.
            raw: Original result from the remote service, not a display summary.
        """
        self.data["response"] = dump_call_response(response)
        self.data["remote"]["result"] = raw
        self.data["phase"] = "remote_result"
        # Consumption belongs to the original delivery even if a newer executor
        # now owns the task. Reporting it never grants checkpoint ownership.
        if self.binding.report_usage and self.data["remote"].get("receipt_id"):
            usage = {}
            if isinstance(raw, dict):
                data = raw.get("data", raw.get("chat", raw.get("run", {})))
                if self.capabilities["type"] == "dify" and isinstance(
                    data.get("total_tokens"), int
                ):
                    usage = {"total_tokens": data["total_tokens"]}
                elif self.capabilities["type"] == "coze":
                    tokens = data.get("usage") or {}
                    usage = {
                        target: tokens[source]
                        for source, target in (
                            ("input_count", "input_tokens"),
                            ("output_count", "output_tokens"),
                            ("token_count", "total_tokens"),
                        )
                        if isinstance(tokens.get(source), int)
                    }
                elif self.capabilities["type"] == "deerflow":
                    usage = {
                        target: data[source]
                        for source, target in (
                            ("total_input_tokens", "input_tokens"),
                            ("total_output_tokens", "output_tokens"),
                            ("total_tokens", "total_tokens"),
                        )
                        if isinstance(data.get(source), int)
                    }
            await self.binding.report_usage(self.data["remote"]["receipt_id"], usage)
        await self.save()
        if self.binding.record_process:
            await self.binding.record_process(
                self.data["invocation_id"] + ":response",
                "model_response",
                {
                    **self.data["response"],
                    "remote_identity": self.data["remote"]["identity"],
                    "remote_result": raw,
                },
            )

    async def restore(self, client) -> tuple[LLMResponse | None, dict | None]:
        """Reuse a saved response or query the original remote operation once.

        Args:
            client: Existing provider-specific HTTP client with exact-run queries.

        Returns:
            A saved effective response or the queried complete raw remote result.

        Raises:
            CheckpointRecoveryError: The original operation cannot be confirmed.
        """
        if self.data["phase"] == "request":
            return None, None
        if self.data["phase"] == "remote_result":
            response = load_call_response(self.data["response"])
            await self.complete(response, self.data["remote"]["result"])
            return response, None
        identity = self.data["remote"].get("identity")
        try:
            if not identity or "remote_query" not in self.capabilities["capabilities"]:
                raise ValueError("No reliable remote run identity/query is available")
            if self.binding.admit_work:
                await self.binding.admit_work()
            if self.capabilities["type"] == "deerflow":
                raw = await self.query(client.get_run, identity)
                if any(raw.get(key) != value for key, value in identity.items()):
                    raise ValueError(
                        "DeerFlow query did not confirm the original run identity"
                    )
                if raw.get("status") != "success":
                    raise ValueError(
                        f"DeerFlow run status is {raw.get('status', 'unknown')}"
                    )
                before_seq = None
                messages = []
                while True:
                    page = await self.query(
                        client.get_run_messages, identity, before_seq
                    )
                    rows = page.get("data")
                    if not isinstance(rows, list) or not rows:
                        raise ValueError(
                            "DeerFlow completed run messages are unavailable"
                        )
                    for row in rows:
                        if any(
                            row.get(key) != value for key, value in identity.items()
                        ):
                            raise ValueError(
                                "DeerFlow returned messages from another run"
                            )
                        if (
                            not isinstance(row.get("seq"), int)
                            or before_seq is not None
                            and row["seq"] >= before_seq
                        ):
                            raise ValueError("DeerFlow message cursor does not advance")
                    messages = rows + messages
                    if page.get("has_more") is False:
                        break
                    if page.get("has_more") is not True:
                        raise ValueError(
                            "DeerFlow message pagination cannot be confirmed"
                        )
                    before_seq = min(row["seq"] for row in rows)
                return None, {
                    "run": raw,
                    "messages": sorted(messages, key=lambda row: row["seq"]),
                }
            if self.capabilities["type"] == "coze":
                queried = await self.query(client.get_chat_run, identity)
                raw = queried.get("data", {})
                if (
                    queried.get("code") != 0
                    or raw.get("id") != identity["chat_id"]
                    or any(
                        raw.get(key) != identity[key]
                        for key in ("conversation_id", "bot_id")
                    )
                ):
                    raise ValueError(
                        "Coze query did not confirm the original chat identity"
                    )
                if raw.get("status") != "completed":
                    raise ValueError(
                        f"Coze chat status is {raw.get('status', 'unknown')}"
                    )
                messages = await self.query(client.get_chat_run_messages, identity)
                if messages.get("code") != 0 or not isinstance(
                    messages.get("data"), list
                ):
                    raise ValueError("Coze completed chat messages are unavailable")
                for message in messages["data"]:
                    if (
                        message.get("chat_id") not in (None, identity["chat_id"])
                        or message.get("conversation_id") != identity["conversation_id"]
                    ):
                        raise ValueError("Coze returned a message from another chat")
                return None, {"chat": raw, "messages": messages["data"]}
            raw = await self.query(client.get_workflow_run, identity["workflow_run_id"])
            if raw.get("id") != identity["workflow_run_id"]:
                raise ValueError(
                    "Remote query did not confirm the original run identity"
                )
            if raw.get("status") != "succeeded":
                raise ValueError(
                    f"Remote run status is {raw.get('status', 'unknown')}; no complete result is available"
                )
            return None, raw
        except Exception as exc:
            error = CheckpointRecoveryError(
                f"Remote result requires confirmation: {exc}"
            )
            self.binding.recovery_error = error
            raise error from exc

    async def query(self, operation: Callable[..., Awaitable[dict]], *args) -> dict:
        """Account for each actual remote status/output HTTP query separately.

        Args:
            operation: Provider-specific read operation, never remote creation.
            args: Exact original run identity or pagination cursor.

        Returns:
            Unmodified remote response.
        """
        if self.binding.admit_work:
            await self.binding.admit_work()
        receipt_id = uuid.uuid4().hex
        if self.binding.begin_usage:
            await self.binding.begin_usage(
                {
                    "receipt_id": receipt_id,
                    "invocation_id": self.data["invocation_id"],
                    "kind": "query",
                    "name": self.capabilities["label"] + ":query",
                }
            )
        try:
            return await asyncio.wait_for(operation(*args), timeout=self.timeout)
        finally:
            if self.binding.report_usage:
                await self.binding.report_usage(receipt_id, {})
