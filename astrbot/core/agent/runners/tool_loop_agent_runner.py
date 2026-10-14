import asyncio
import copy
import json
import sys
import time
import traceback
import typing as T
import uuid
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path

from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from astrbot import logger
from astrbot.core.agent.call_checkpoint import (
    dump_call_messages,
    dump_call_response,
    load_call_response,
)
from astrbot.core.agent.message import ContentPart, ImageURLPart, TextPart, ThinkPart
from astrbot.core.agent.tool import (
    FunctionTool,
    ToolRecoveryResult,
    ToolSet,
    current_tool_invocation,
)
from astrbot.core.agent.tool_image_cache import CachedImage, tool_image_cache
from astrbot.core.agent_team_execution import (
    CheckpointRecoveryError,
    ProcessPersistenceError,
    current_team_execution,
    model_delivery,
)
from astrbot.core.exceptions import EmptyModelOutputError
from astrbot.core.message.components import Json
from astrbot.core.message.message_event_result import (
    MessageChain,
)
from astrbot.core.persona_error_reply import (
    extract_persona_custom_error_message_from_event,
)
from astrbot.core.pipeline.llm_request_trace import read_injections
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderRequest,
    TokenUsage,
    ToolCallsResult,
)
from astrbot.core.provider.modalities import (
    log_context_sanitize_stats,
    sanitize_contexts_by_modalities,
)
from astrbot.core.provider.provider import Provider
from astrbot.core.utils.async_generator import iterate_in_task
from astrbot.core.utils.prefix_diagnostics import record_session_request

from ..context.compressor import ContextCompressor
from ..context.config import ContextConfig
from ..context.manager import ContextManager
from ..context.token_counter import EstimateTokenCounter, TokenCounter
from ..hooks import BaseAgentRunHooks
from ..message import (
    AssistantMessageSegment,
    Message,
    ToolCallMessageSegment,
    UserMessageSegment,
    bind_checkpoint_messages,
)
from ..response import AgentResponseData, AgentStats
from ..run_context import ContextWrapper, TContext
from ..tool_executor import BaseFunctionToolExecutor
from .base import AgentResponse, AgentState, BaseAgentRunner

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override


@dataclass(slots=True)
class _HandleFunctionToolsResult:
    kind: T.Literal["message_chain", "tool_call_result_blocks", "cached_image"]
    message_chain: MessageChain | None = None
    tool_call_result_blocks: list[ToolCallMessageSegment] | None = None
    cached_image: T.Any = None

    @classmethod
    def from_message_chain(cls, chain: MessageChain) -> "_HandleFunctionToolsResult":
        return cls(kind="message_chain", message_chain=chain)

    @classmethod
    def from_tool_call_result_blocks(
        cls, blocks: list[ToolCallMessageSegment]
    ) -> "_HandleFunctionToolsResult":
        return cls(kind="tool_call_result_blocks", tool_call_result_blocks=blocks)

    @classmethod
    def from_cached_image(cls, image: T.Any) -> "_HandleFunctionToolsResult":
        return cls(kind="cached_image", cached_image=image)


@dataclass(slots=True)
class FollowUpTicket:
    seq: int
    text: str
    consumed: bool = False
    resolved: asyncio.Event = field(default_factory=asyncio.Event)


class _ToolExecutionInterrupted(Exception):
    """Raised when a running tool call is interrupted by a stop request."""


ToolExecutorResultT = T.TypeVar("ToolExecutorResultT")
AwaitableResultT = T.TypeVar("AwaitableResultT")


class ToolLoopAgentRunner(BaseAgentRunner[TContext]):
    TOOL_RESULT_MAX_ESTIMATED_TOKENS = 27_500
    TOOL_RESULT_PREVIEW_MAX_ESTIMATED_TOKENS = 7000
    EMPTY_OUTPUT_RETRY_ATTEMPTS = 3
    EMPTY_OUTPUT_RETRY_WAIT_MIN_S = 1
    EMPTY_OUTPUT_RETRY_WAIT_MAX_S = 4
    USER_INTERRUPTION_REQUEST = "Stop output."
    USER_INTERRUPTION_MESSAGE = "Output stopped."
    FOLLOW_UP_NOTICE_TEMPLATE = (
        "\n\n[SYSTEM NOTICE] User sent follow-up messages while tool execution "
        "was in progress. Prioritize these follow-up instructions in your next "
        "actions. In your very next action, briefly acknowledge to the user "
        "that their follow-up message(s) were received before continuing.\n"
        "{follow_up_lines}"
    )
    MAX_STEPS_REACHED_PROMPT = (
        "[SYSTEM NOTICE: Agent step budget exhausted] "
        "Remaining steps: 0. Stop using tools; summarize results and unfinished work. "
        "If unfinished, tell the user they can simply ask you to continue with a fresh "
        "step budget. Optionally, they can raise the tool-call round limit in "
        "AstrBot WebUI to allow longer runs."
    )
    MAX_STEP_DENIAL_RECOVERY_ROUNDS = 2
    """Extra steps granted after the forced final round when the model keeps
    calling denied tools. Each denial returns a tool-result error the model
    can see; these rounds let it fold that feedback into the final summary.
    Every round is a pure payload append, so the prefix cache is unaffected."""
    SKILLS_LIKE_REQUERY_INSTRUCTION_TEMPLATE = (
        "You have decided to call tool(s): {tool_names}. Now call the tool(s) "
        "with required arguments using the tool schema, and follow the existing "
        "tool-use rules."
    )
    SKILLS_LIKE_REQUERY_REPAIR_INSTRUCTION = (
        "This is the second-stage tool execution step. "
        "You must do exactly one of the following: "
        "1. Call one of the selected tools using the provided tool schema. "
        "2. If calling a tool is no longer possible or appropriate, reply to the user "
        "with a brief explanation of why. "
        "Do not return an empty response. "
        "Do not ignore the selected tools without explanation."
    )
    REPEATED_TOOL_NOTICE_DEFAULT_THRESHOLD = 3
    REPEATED_TOOL_NOTICE_L1_TEMPLATE = (
        "\n\n[SYSTEM NOTICE] By the way, you have executed the same tool "
        "`{tool_name}` with the same arguments {streak} times consecutively. "
        "Double-check whether another tool, different arguments, or a summary would "
        "move the task forward better."
    )
    REPEATED_TOOL_NOTICE_L2_TEMPLATE = (
        "\n\n[SYSTEM NOTICE] Important: you have executed the same tool "
        "`{tool_name}` with the same arguments {streak} times consecutively. "
        "Unless this repetition is clearly necessary, stop repeating the same action "
        "and either switch tools, refine parameters, or summarize what is still "
        "missing."
    )
    REPEATED_TOOL_NOTICE_L3_TEMPLATE = (
        "\n\n[SYSTEM NOTICE] Important: you have executed the same tool "
        "`{tool_name}` with the same arguments {streak} times consecutively. "
        "Repetition is now very high. Continue only if each call is clearly producing "
        "new information. Otherwise, change strategy, adjust arguments, or explain "
        "the limitation to the user."
    )
    TOOL_RESULT_OVERFLOW_NOTICE_TEMPLATE = (
        "Truncated tool output preview shown above. "
        "The tool output was too large to include directly and was written to "
        "`{overflow_path}`. Use {read_tool_hint} to inspect it. "
        "Use a narrower window when reading large files."
    )
    TOOL_IMAGE_NOTICE_TEMPLATE = (
        "Image returned and cached at path='{file_path}'. "
        "Review the image below. Use send_message_to_user to send it to the user if satisfied, "
        "with type='image' and path='{file_path}'."
    )

    def _get_persona_custom_error_message(self) -> str | None:
        """Read persona-level custom error message from event extras when available."""
        event = getattr(self.run_context.context, "event", None)
        return extract_persona_custom_error_message_from_event(event)

    async def _complete_with_assistant_response(self, llm_resp: LLMResponse) -> None:
        """Finalize the current step as a plain assistant response with no tool calls."""
        self.final_llm_resp = llm_resp
        self._transition_state(AgentState.DONE)
        self.stats.end_time = time.time()

        parts = []
        if llm_resp.reasoning_content is not None or llm_resp.reasoning_signature:
            parts.append(
                ThinkPart(
                    think=llm_resp.reasoning_content or "",
                    encrypted=llm_resp.reasoning_signature,
                )
            )
        if llm_resp.completion_text:
            parts.append(TextPart(text=llm_resp.completion_text))
        if len(parts) == 0:
            logger.warning("LLM returned empty assistant message with no tool calls.")
        self.run_context.messages.append(Message(role="assistant", content=parts))

        try:
            await self.agent_hooks.on_agent_done(self.run_context, llm_resp)
        except Exception as e:
            logger.error(f"Error in on_agent_done hook: {e}", exc_info=True)
        self._resolve_unconsumed_follow_ups()

    async def _build_file_changes_response(self) -> AgentResponse | None:
        """Build the end-of-turn file change summary event.

        Reads the entries that file tools appended to
        ``AstrAgentContext.extra["changed_files"]`` during this run and
        aggregates them into a per-file net diff via the edit-history
        backup engine. Imported lazily: the runner is agent-generic
        machinery and must not import computer tools at module load.

        Returns:
            The ``file_changes`` AgentResponse, or None when the run
            touched no files or summary computation failed.
        """
        agent_ctx = getattr(self.run_context, "context", None)
        extra = getattr(agent_ctx, "extra", None) or {}
        entries = extra.get("changed_files") or []
        if not entries:
            return None
        try:
            from astrbot.core.tools.computer_tools.edit_history import (
                build_turn_change_summary,
            )

            files = await build_turn_change_summary(
                entries, since_ts=self.stats.start_time
            )
        except Exception:
            logger.warning("Failed to build file change summary", exc_info=True)
            return None
        if not files:
            return None
        return AgentResponse(
            type="file_changes",
            data=AgentResponseData(
                chain=MessageChain(
                    type="file_changes",
                    chain=[Json(data={"files": files})],
                )
            ),
        )

    def _injection_trace_response(self) -> AgentResponse | None:
        """Build the per-turn plugin context-injection trace event.

        Reads the records the ``call_event_hook`` observation left on the
        event extras for ``on_llm_request`` handlers.

        Returns:
            The ``llm_request_injections`` AgentResponse, or None when no
            handler injected anything this turn.
        """
        run_context = getattr(self, "run_context", None)
        event = getattr(getattr(run_context, "context", None), "event", None)
        items = read_injections(event)
        if not items:
            return None
        return AgentResponse(
            type="llm_request_injections",
            data=AgentResponseData(
                chain=MessageChain(
                    type="llm_request_injections",
                    chain=[Json(data={"items": items})],
                )
            ),
        )

    @override
    async def reset(
        self,
        provider: Provider,
        request: ProviderRequest,
        run_context: ContextWrapper[TContext],
        tool_executor: BaseFunctionToolExecutor[TContext],
        agent_hooks: BaseAgentRunHooks[TContext],
        streaming: bool = False,
        # enforce max turns, will discard older turns when exceeded BEFORE compression
        # -1 means no limit
        enforce_max_turns: int = -1,
        # llm compressor
        llm_compress_instruction: str | None = None,
        llm_compress_keep_recent_ratio: float = 0.15,
        llm_compress_provider: Provider | None = None,
        # truncate by turns compressor
        truncate_turns: int = 1,
        truncate_target_usage_ratio: float = 0.4,
        # customize
        custom_token_counter: TokenCounter | None = None,
        custom_compressor: ContextCompressor | None = None,
        tool_schema_mode: str | None = "full",
        repeated_tool_notice_enabled: bool = True,
        repeated_tool_notice_threshold: int = REPEATED_TOOL_NOTICE_DEFAULT_THRESHOLD,
        fallback_providers: list[Provider] | None = None,
        request_max_retries: int | None = None,
        tool_result_overflow_dir: str | None = None,
        read_tool: FunctionTool | None = None,
        **kwargs: T.Any,
    ) -> None:
        self.req = request
        self.streaming = streaming
        self.enforce_max_turns = enforce_max_turns
        self.llm_compress_instruction = llm_compress_instruction
        self.llm_compress_keep_recent_ratio = llm_compress_keep_recent_ratio
        self.llm_compress_provider = llm_compress_provider
        self.truncate_turns = truncate_turns
        self.truncate_target_usage_ratio = truncate_target_usage_ratio
        self.custom_token_counter = custom_token_counter
        self.custom_compressor = custom_compressor
        self.request_max_retries = request_max_retries
        self.tool_result_overflow_dir = tool_result_overflow_dir
        self.read_tool = read_tool
        self._tool_result_token_counter = EstimateTokenCounter()
        max_context_tokens = provider.provider_config.get("max_context_tokens", 0)
        # Provider-level absolute compression trigger in tokens (e.g. 256000).
        # A threshold above the model window can never be reached before the
        # request overflows, so it is treated as misconfigured and the
        # compressors' ratio-based trigger takes over (0).
        compress_threshold_tokens = int(
            provider.provider_config.get("compress_threshold_tokens", 0) or 0
        )
        if (
            compress_threshold_tokens > 0
            and max_context_tokens > 0
            and compress_threshold_tokens > max_context_tokens
        ):
            logger.warning(
                "Provider %s: compress_threshold_tokens=%s exceeds "
                "max_context_tokens=%s; falling back to the ratio-based "
                "compression trigger.",
                provider.provider_config.get("id", "unknown"),
                compress_threshold_tokens,
                max_context_tokens,
            )
            compress_threshold_tokens = 0

        self.request_context_manager_config = ContextConfig(
            # <=0 disables token-based guarding.
            max_context_tokens=max_context_tokens,
            # <=0 keeps the compressors' ratio-based trigger.
            compress_threshold_tokens=compress_threshold_tokens,
            # Enforce max turns before token-based guarding.
            enforce_max_turns=self.enforce_max_turns,
            truncate_turns=self.truncate_turns,
            truncate_target_usage_ratio=self.truncate_target_usage_ratio,
            llm_compress_instruction=self.llm_compress_instruction,
            llm_compress_keep_recent_ratio=self.llm_compress_keep_recent_ratio,
            llm_compress_provider=self.llm_compress_provider,
            custom_token_counter=self.custom_token_counter,
            custom_compressor=self.custom_compressor,
            # Same per-request LLM params the chat requests send, so the
            # summary request's provider fields (e.g. reasoning_effort) stay
            # byte-identical and the provider prefix cache still hits.
            llm_params=self.req.llm_params,
        )
        self.request_context_manager = ContextManager(
            self.request_context_manager_config
        )

        self.provider = provider
        self.fallback_providers: list[Provider] = []
        seen_provider_ids: set[str] = {str(provider.provider_config.get("id", ""))}
        for fallback_provider in fallback_providers or []:
            fallback_id = str(fallback_provider.provider_config.get("id", ""))
            if fallback_provider is provider:
                continue
            if fallback_id and fallback_id in seen_provider_ids:
                continue
            self.fallback_providers.append(fallback_provider)
            if fallback_id:
                seen_provider_ids.add(fallback_id)
        self.final_llm_resp = None
        self._state = AgentState.IDLE
        self.tool_executor = tool_executor
        self.agent_hooks = agent_hooks
        self.run_context = run_context
        self._aborted = False
        self._abort_signal = asyncio.Event()
        self._pending_follow_ups: list[FollowUpTicket] = []
        self._pending_system_notices: list[str] = []
        """Dashboard-originated notices (e.g. user shell-session actions),
        merged into the next tool result with their own [SYSTEM NOTICE]
        framing — deliberately separate from user follow-up messages."""
        self._unconsumed_follow_ups: list[tuple[int, str]] = []
        """(seq, text) snapshot of follow-ups resolved without being consumed."""
        self._all_follow_ups: list[tuple[int, str]] = []
        """(seq, text) of every follow-up accepted during this run."""
        self._follow_up_seq = 0
        self._last_tool_name: str | None = None
        self._last_tool_args: dict[str, T.Any] | None = None
        self._same_tool_streak = 0
        self.repeated_tool_notice_enabled = bool(repeated_tool_notice_enabled)
        self.repeated_tool_notice_threshold = (
            self._normalize_repeated_tool_notice_threshold(
                repeated_tool_notice_threshold
            )
        )
        self._step_budget_max: int | None = None
        self._step_budget_used = 0
        self._step_budget_notified: set[int] = set()

        # These two are used for tool schema mode handling
        # We now have two modes:
        # - "full": use full tool schema for LLM calls, default.
        # - "skills_like": use light tool schema for LLM calls, and re-query with param-only schema when needed.
        #   Light tool schema does not include tool parameters.
        #   This can reduce token usage when tools have large descriptions.
        # See #4681
        self.tool_schema_mode = tool_schema_mode
        self._tool_schema_param_set = None
        self._skill_like_raw_tool_set = None
        if tool_schema_mode == "skills_like":
            tool_set = self.req.func_tool
            if not tool_set:
                return
            self._skill_like_raw_tool_set = tool_set
            light_set = tool_set.get_light_tool_set()
            self._tool_schema_param_set = tool_set.get_param_only_tool_set()
            # MODIFIE the req.func_tool to use light tool schemas
            self.req.func_tool = light_set

        # append existing messages in the run context
        messages = bind_checkpoint_messages(request.contexts or [])
        if (
            request.prompt is not None
            or request.image_urls
            or request.audio_urls
            or request.extra_user_content_parts
        ):
            m = await self._assemble_request_context_for_provider(request)
            messages.append(Message.model_validate(m))
        if request.system_prompt:
            messages.insert(
                0,
                Message(role="system", content=request.system_prompt),
            )
        self.run_context.messages = messages

        self.stats = AgentStats()
        self.stats.start_time = time.time()
        execution = current_team_execution.get()
        self._checkpoint_enabled = bool(
            execution
            and execution.save_checkpoint
            and not (getattr(self.run_context.context, "extra", None) or {}).get(
                "is_subagent"
            )
        )
        self._call_checkpoint: dict | None = None
        self._member_message_receipts: list[dict] = []
        self._resume_requery: dict | None = None
        self._restored_call = False
        self._resumed_attempt = False
        if self._checkpoint_enabled and execution and execution.checkpoint:
            saved = copy.deepcopy(execution.checkpoint)
            if saved["started_tools"] and "tool_calls" not in saved:
                execution.recovery_error = CheckpointRecoveryError(
                    "Started tools require confirmation before recovery"
                )
                raise execution.recovery_error
            available = self.effective_raw_tool_set
            if saved["tools"] != [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    **(
                        {"recovery": tool.recovery.declaration} if tool.recovery else {}
                    ),
                }
                for tool in (available.tools if available else [])
            ]:
                execution.recovery_error = CheckpointRecoveryError(
                    "Saved tool capabilities have changed; recovery is blocked"
                )
                raise execution.recovery_error
            try:
                await self._restore_tool_result_files(saved)
            except (OSError, ValueError) as exc:
                execution.recovery_error = CheckpointRecoveryError(
                    f"Failed to restore saved tool output: {exc}"
                )
                raise execution.recovery_error from exc
            self.run_context.messages = bind_checkpoint_messages(saved["messages"])
            self.req.model = saved["model"]
            self.req.llm_params = saved["llm_params"]
            self.req.denied_tools = set(saved["denied_tools"])
            self.req.extra_user_content_parts = [
                ContentPart.model_validate(part)
                for part in saved["extra_user_content_parts"]
            ]
            self._step_budget_max = saved["step_budget_max"]
            self._step_budget_used = saved["step_budget_used"]
            self._step_budget_notified = set(saved["step_budget_notified"])
            self._last_tool_name = saved["last_tool_name"]
            self._last_tool_args = saved["last_tool_args"]
            self._same_tool_streak = saved["same_tool_streak"]
            stats = dict(saved["stats"])
            stats["token_usage"] = TokenUsage(**stats["token_usage"])
            self.stats = AgentStats(**stats)
            self._call_checkpoint = saved
            self._resumed_attempt = True
            if (
                saved.get("stage", "main") != "main"
                and saved["phase"] != "tools_applied"
            ):
                self._resume_requery = saved
            self._restored_call = saved["phase"] != "tools_applied"
            self._transition_state(AgentState.RUNNING)

    async def _save_call_checkpoint(
        self,
        phase: str,
        *,
        response: LLMResponse | None = None,
        contexts: list[Message] | list[dict] | None = None,
        stage: str = "main",
        parent_response: LLMResponse | None = None,
        previous_response: LLMResponse | None = None,
    ) -> None:
        """Persist the effective runner state on all model and tool boundaries.

        Args:
            phase: Model, per-tool result, or context application boundary.
            response: Complete provider response when that boundary has been reached.
            contexts: Actual provider-facing messages for a new model request.
            stage: Main generation, parameter resolution, or parameter repair.
            parent_response: Original light-schema plan for parameter resolution.
            previous_response: Saved unsuccessful parameter response before repair.

        Raises:
            ProcessPersistenceError: Dependent execution must stop if saving fails.
        """
        execution = current_team_execution.get()
        if (
            not execution
            or not execution.save_checkpoint
            or not self._checkpoint_enabled
        ):
            return
        if phase == "request":
            available = self.effective_raw_tool_set
            model_calls = (self._call_checkpoint or {}).get("model_calls", 0) + 1
            self._call_checkpoint = {
                "format_version": 2,
                "invocation_id": uuid.uuid4().hex,
                "messages": dump_call_messages(self.run_context.messages),
                "member_messages": self._member_message_receipts,
                "input_contexts": dump_call_messages(
                    contexts or self.run_context.messages
                ),
                "model": self.req.model or self.provider.get_model(),
                "model_calls": model_calls,
                "stage": stage,
                "parent_response": dump_call_response(parent_response)
                if parent_response
                else None,
                "previous_response": dump_call_response(previous_response)
                if previous_response
                else None,
                "provider_id": self.provider.provider_config.get("id"),
                "config_id": execution.config_id,
                "run_id": execution.run_id,
                "attempt_id": execution.turn_id,
                "conversation_id": execution.conversation_id,
                "context_position": len(self.run_context.messages),
                "llm_params": self.req.llm_params,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        **(
                            {"recovery": tool.recovery.declaration}
                            if tool.recovery
                            else {}
                        ),
                    }
                    for tool in (available.tools if available else [])
                ],
                "denied_tools": sorted(self.req.denied_tools),
                "extra_user_content_parts": [
                    part if isinstance(part, dict) else part.model_dump_for_context()
                    for part in self.req.extra_user_content_parts
                ],
                "step_budget_max": self._step_budget_max,
                "step_budget_used": self._step_budget_used,
                "step_budget_notified": sorted(self._step_budget_notified),
                "last_tool_name": self._last_tool_name,
                "last_tool_args": self._last_tool_args,
                "same_tool_streak": self._same_tool_streak,
                "stats": self.stats.to_dict(),
                "started_tools": [],
                "tool_calls": [],
                "tool_result_files": dict(
                    (self._call_checkpoint or {}).get("tool_result_files", {})
                ),
                "response": None,
            }
        if self._call_checkpoint is None:
            raise ProcessPersistenceError(
                "Model request has no saved invocation identity"
            )
        self._call_checkpoint["format_version"] = 2
        self._call_checkpoint["phase"] = phase
        if response is not None:
            self._call_checkpoint["response"] = dump_call_response(response)
            if (
                self.tool_schema_mode != "skills_like"
                or stage != "main"
                or self._call_checkpoint.get("stage", "main") != "main"
            ):
                self._call_checkpoint["tool_calls"] = [
                    {
                        "record_id": uuid.uuid4().hex,
                        "call_id": call_id,
                        "name": name,
                        "args": args or {},
                        "status": "intent",
                        "raw_results": [],
                        "protocol_results": [],
                        "process_records": [],
                        "context_position": None,
                    }
                    for call_id, name, args in zip(
                        response.tools_call_ids,
                        response.tools_call_name,
                        response.tools_call_args,
                    )
                ]
        if phase.startswith("tool"):
            self._call_checkpoint.update(
                {
                    "stats": self.stats.to_dict(),
                    "last_tool_name": self._last_tool_name,
                    "last_tool_args": self._last_tool_args,
                    "same_tool_streak": self._same_tool_streak,
                    "step_budget_notified": sorted(self._step_budget_notified),
                }
            )
        await execution.save_checkpoint(copy.deepcopy(self._call_checkpoint))
        if phase == "request":
            # Parameter and fallback requests carry this context, but the delivery
            # belongs to the first committed request that included it.
            self._member_message_receipts = []

    async def _begin_metered_call(
        self,
        kind: str,
        invocation_id: str,
        *,
        name: str | None = None,
        contexts: list | None = None,
        tools: ToolSet | None = None,
    ) -> dict | None:
        """Admit one actual delivery, shared by model, parameter and tool calls.

        Args:
            kind: Model, tool execution or tool status query.
            invocation_id: Original checkpoint or tool-call identity.
            name: Provider or tool name for the durable detail.
            contexts: Actual provider-facing messages used for estimation.
            tools: Actual provider-facing schemas used for estimation.

        Returns:
            Committed receipt with the permitted output cap, or None outside v2.
        """
        execution = current_team_execution.get()
        if not execution or not execution.begin_usage:
            return None
        payload = {
            "receipt_id": uuid.uuid4().hex,
            "invocation_id": invocation_id,
            "kind": kind,
            "name": name,
        }
        if kind == "model":
            messages = bind_checkpoint_messages(
                dump_call_messages(contexts or self.run_context.messages)
            )
            payload["input_tokens"] = (
                self._tool_result_token_counter.count_tokens(messages)
                + (
                    len(
                        json.dumps(
                            [tool.parameters for tool in tools.tools],
                            ensure_ascii=False,
                        )
                    )
                    + 3
                )
                // 4
                if tools
                else self._tool_result_token_counter.count_tokens(messages)
            )
            payload["max_output_tokens"] = (self.req.llm_params or {}).get(
                "max_completion_tokens"
            ) or (self.req.llm_params or {}).get("max_tokens")
        return await execution.begin_usage(payload)

    async def _report_metered_call(
        self, receipt: dict | None, response: LLMResponse | None = None
    ) -> None:
        """Persist complete reports or conservative uncertainty for a delivery.

        Args:
            receipt: Original admitted delivery, absent for ordinary chat.
            response: Complete provider response; missing usage remains marked.
        """
        execution = current_team_execution.get()
        if receipt is None or not execution or not execution.report_usage:
            return
        report = {}
        if response and response.usage:
            report = {
                "input_tokens": response.usage.input,
                "output_tokens": response.usage.output,
            }
        elif response and response.role != "err":
            report = {
                "estimated_tokens": receipt["request"]["input_tokens"]
                + (
                    len(json.dumps(dump_call_response(response), ensure_ascii=False))
                    + 3
                )
                // 4
            }
        await execution.report_usage(receipt["receipt_id"], report)

    def _read_tool_hint(self) -> str:
        if self.read_tool is not None:
            return f"`{self.read_tool.name}`"
        return "the available file-read tool"

    async def _assemble_request_context_for_provider(
        self,
        request: ProviderRequest,
    ) -> dict[str, T.Any]:
        modalities = self.provider.provider_config.get("modalities", None)
        if not modalities:  # Unconfigured (None or empty list) defaults to support all modalities for backward compatibility
            return await request.assemble_context()

        supports_image = "image" in modalities
        supports_audio = "audio" in modalities
        if supports_image and supports_audio:
            return await request.assemble_context()

        adjusted_request = replace(
            request,
            image_urls=request.image_urls if supports_image else [],
            audio_urls=request.audio_urls if supports_audio else [],
        )
        context = await adjusted_request.assemble_context()
        content = context.get("content")
        if isinstance(content, str):
            content_blocks: list[dict[str, T.Any]] = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            content_blocks = content
        else:
            content_blocks = []

        if not supports_image:
            for _ in request.image_urls:
                content_blocks.append({"type": "text", "text": "[Image]"})
        if not supports_audio:
            for _ in request.audio_urls:
                content_blocks.append({"type": "text", "text": "[Audio]"})

        return {"role": "user", "content": content_blocks}

    async def _restore_tool_result_files(self, saved: dict[str, T.Any]) -> None:
        """Restore complete files while associating raw outputs with their messages.

        Args:
            saved: Durable model context and original per-call results.

        Returns:
            None after rebuilding required files and retaining their full contents.

        Raises:
            ValueError: A required protocol reference has no matching source.
            OSError: A complete saved file cannot be restored.
        """
        files = dict(saved.get("tool_result_files", {}))
        legacy = "tool_result_files" not in saved
        literal_protocols = set()
        notice_prefix = self.TOOL_RESULT_OVERFLOW_NOTICE_TEMPLATE.partition(
            "{overflow_path}"
        )[0]
        if legacy:
            for call in [
                *saved.get("tool_calls", []),
                *saved.get("tool_result_sources", []),
            ]:
                if call["status"] not in {"result_saved", "applied"}:
                    continue
                protocols = call["protocol_results"]
                if not protocols:
                    continue
                cursor = 0
                matched = set()
                for raw in call["raw_results"]:
                    result = CallToolResult.model_validate(raw)
                    parts = [] if result.content else ["The tool returned no content."]
                    for index, item in enumerate(result.content):
                        if isinstance(item, TextContent):
                            parts.append(item.text)
                        elif isinstance(item, EmbeddedResource) and isinstance(
                            item.resource, TextResourceContents
                        ):
                            parts.append(item.resource.text)
                        else:
                            cached = self._cache_tool_image(
                                item, call["call_id"], call["name"], index
                            )
                            if cached:
                                parts.append(
                                    self.TOOL_IMAGE_NOTICE_TEMPLATE.format(
                                        file_path=cached.file_path
                                    )
                                )
                            elif isinstance(item, EmbeddedResource):
                                parts.append(
                                    "The tool has returned a data type that is not supported."
                                )
                    # Resource links emit no text protocol in the live executor.
                    if not parts:
                        continue
                    content = "\n\n".join(parts)
                    oversized = (
                        self._tool_result_token_counter.count_tokens(
                            [
                                Message(
                                    role="tool",
                                    content=content,
                                    tool_call_id=call["call_id"],
                                )
                            ]
                        )
                        > self.TOOL_RESULT_MAX_ESTIMATED_TOKENS
                    )
                    expected = content
                    if oversized:
                        preview = self._truncate_tool_result_preview(
                            content, tool_call_id=call["call_id"]
                        )
                        expected = (preview + "\n\n" if preview else "") + notice_prefix
                    index = next(
                        (
                            index
                            for index in range(cursor, len(protocols))
                            if protocols[index]["content"].startswith(expected)
                        ),
                        None,
                    )
                    if index is None:
                        if oversized:
                            raise ValueError(
                                "Saved overflow result has no matching protocol"
                            )
                        continue
                    matched.add(index)
                    cursor = index + 1
                    if oversized:
                        path = protocols[index]["content"][len(expected) :].partition(
                            "`"
                        )[0]
                        files[path] = content
                    else:
                        literal_protocols.add(protocols[index]["content"])
                for index, protocol in enumerate(protocols):
                    if index in matched or notice_prefix not in protocol["content"]:
                        continue
                    if any(
                        record["data"].get("result")
                        and protocol["content"].startswith(record["data"]["result"])
                        for record in call.get("process_records", [])
                    ):
                        literal_protocols.add(protocol["content"])
                        continue
                    raise ValueError("Saved overflow reference has no complete source")
        for path, content in files.items():
            await self._write_tool_result_overflow_file(
                tool_call_id=Path(path).stem, content=content, overflow_path=Path(path)
            )
        if legacy:
            for message in [
                *saved.get("messages", []),
                *saved.get("input_contexts", []),
            ]:
                content = message.get("content")
                if (
                    message["role"] != "tool"
                    or not isinstance(content, str)
                    or content in literal_protocols
                ):
                    continue
                _, separator, notice = content.rpartition(notice_prefix)
                if separator and not Path(notice.partition("`")[0]).is_file():
                    raise ValueError("Required saved overflow reference is unavailable")
        saved["tool_result_files"] = files

    async def _write_tool_result_overflow_file(
        self,
        *,
        tool_call_id: str,
        content: str,
        overflow_path: Path | None = None,
    ) -> str:
        """Write complete output at a new or previously saved temporary path.

        Args:
            tool_call_id: Tool identity used when naming a new file.
            content: Complete output before preview truncation.
            overflow_path: Original saved path when rebuilding required content.

        Returns:
            Absolute path to the complete readable output.

        Raises:
            ValueError: No output directory exists or the path escapes it.
            OSError: The complete file cannot be written.
        """
        if self.tool_result_overflow_dir is None:
            raise ValueError("tool_result_overflow_dir is not configured")

        overflow_dir = Path(self.tool_result_overflow_dir).resolve(strict=False)
        if overflow_path is None:
            safe_tool_call_id = (
                "".join(
                    ch if ch.isalnum() or ch in {"-", "_", "."} else "_"
                    for ch in tool_call_id
                ).strip("._")
                or "tool_call"
            )
            file_name = f"{safe_tool_call_id}_{uuid.uuid4().hex[:8]}.txt"
            overflow_path = overflow_dir / file_name
        elif overflow_path.resolve(strict=False).parent != overflow_dir:
            raise ValueError("Saved tool output is outside its temporary directory")
        output_path = overflow_path

        def _run() -> str:
            overflow_dir.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            return str(output_path)

        return await asyncio.to_thread(_run)

    async def _materialize_large_tool_result(
        self,
        *,
        tool_call_id: str,
        content: str,
    ) -> str:
        if self.tool_result_overflow_dir is None or self.read_tool is None:
            return content

        estimated_tokens = self._tool_result_token_counter.count_tokens(
            [Message(role="tool", content=content, tool_call_id=tool_call_id)]
        )
        if estimated_tokens <= self.TOOL_RESULT_MAX_ESTIMATED_TOKENS:
            return content

        preview = self._truncate_tool_result_preview(content, tool_call_id=tool_call_id)
        try:
            overflow_path = await self._write_tool_result_overflow_file(
                tool_call_id=tool_call_id,
                content=content,
            )
            if self._checkpoint_enabled and self._call_checkpoint is not None:
                self._call_checkpoint.setdefault("tool_result_files", {})[
                    overflow_path
                ] = content
        except Exception as exc:
            logger.warning(
                "Failed to spill oversized tool result for %s: %s",
                tool_call_id,
                exc,
                exc_info=True,
            )
            error_notice = (
                "Tool output exceeded the inline result limit "
                f"({estimated_tokens} estimated tokens > "
                f"{self.TOOL_RESULT_MAX_ESTIMATED_TOKENS}) and could not be written "
                f"to `{self.tool_result_overflow_dir}`: {exc}"
            )
            if not preview:
                return error_notice
            return f"{preview}\n\n{error_notice}"

        notice = self.TOOL_RESULT_OVERFLOW_NOTICE_TEMPLATE.format(
            overflow_path=overflow_path,
            read_tool_hint=self._read_tool_hint(),
        )
        if not preview:
            return notice
        return f"{preview}\n\n{notice}"

    def _truncate_tool_result_preview(
        self,
        content: str,
        *,
        tool_call_id: str,
    ) -> str:
        preview = content
        while preview:
            estimated_tokens = self._tool_result_token_counter.count_tokens(
                [Message(role="tool", content=preview, tool_call_id=tool_call_id)]
            )
            if estimated_tokens <= self.TOOL_RESULT_PREVIEW_MAX_ESTIMATED_TOKENS:
                return preview
            next_len = len(preview) // 2
            if next_len <= 0:
                break
            preview = preview[:next_len]
        return preview

    async def _await_or_stop(
        self,
        awaitable: T.Awaitable[AwaitableResultT],
    ) -> AwaitableResultT | None:
        """Await work while allowing a stop request to cancel it immediately.

        Args:
            awaitable: Provider or context-processing operation to execute.

        Returns:
            The operation result, or None when user cancellation wins the race.

        Raises:
            asyncio.CancelledError: If the outer Agent task is cancelled.
            Exception: Any exception raised by the awaited operation.
        """
        operation_task = asyncio.create_task(awaitable)
        abort_task = asyncio.create_task(self._abort_signal.wait())
        try:
            done, _ = await asyncio.wait(
                {operation_task, abort_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if abort_task in done:
                if not operation_task.done():
                    operation_task.cancel()
                await asyncio.gather(operation_task, return_exceptions=True)
                return None
            return operation_task.result()
        except asyncio.CancelledError:
            if not operation_task.done():
                operation_task.cancel()
            await asyncio.gather(operation_task, return_exceptions=True)
            raise
        finally:
            if not abort_task.done():
                abort_task.cancel()
            await asyncio.gather(abort_task, return_exceptions=True)

    async def _iter_llm_responses(
        self, *, include_model: bool = True
    ) -> T.AsyncGenerator[LLMResponse, None]:
        """Yields chunks *and* a final LLMResponse."""
        restored_request = self._restored_call
        message_context_position = len(self.run_context.messages)
        execution = current_team_execution.get()
        if (
            not restored_request
            and execution
            and execution.pending_messages
            and self._checkpoint_enabled
        ):
            if execution.admit_work:
                await execution.admit_work()
            pending = await execution.pending_messages()
            self._member_message_receipts = []
            for message in pending:
                self._member_message_receipts.append(
                    {
                        "message_id": message["message_id"],
                        "context_position": len(self.run_context.messages),
                    }
                )
                self.run_context.messages.append(
                    UserMessageSegment(
                        content=[
                            TextPart(
                                text="Team member message (JSON):\n"
                                + json.dumps(message, ensure_ascii=False)
                            )
                        ]
                    )
                )
        contexts = self._sanitize_contexts_for_provider(self.run_context.messages)
        if restored_request:
            assert self._call_checkpoint is not None
            contexts = bind_checkpoint_messages(self._call_checkpoint["input_contexts"])
            self._restored_call = False
        func_tool = self._func_tool_for_provider()
        # Fingerprint the payload-stage request so same-prefix flows (e.g. the
        # compact plugin's summary request) can diff against what the chat
        # requests actually sent when a provider prefix-cache miss shows up.
        record_session_request(
            self.req.session_id or "",
            model=self.req.model,
            func_tool=func_tool,
            messages=contexts,
            is_subagent=bool(
                (getattr(self.run_context.context, "extra", None) or {}).get(
                    "is_subagent"
                )
            ),
        )
        payload = {
            "contexts": contexts,
            "func_tool": func_tool,
            "session_id": self.req.session_id,
            "conversation_id": (
                self.req.conversation.cid if self.req.conversation else None
            ),
            "extra_user_content_parts": self.req.extra_user_content_parts,  # list[ContentPart]
            "abort_signal": self._abort_signal,
            "request_max_retries": self.request_max_retries,
            "llm_params": self.req.llm_params,
        }
        if include_model:
            # For primary provider we keep explicit model selection if provided.
            payload["model"] = self.req.model
        if not restored_request:
            try:
                await self._save_call_checkpoint("request", contexts=contexts)
            except (Exception, asyncio.CancelledError):
                # Failed admission/persistence must not leak an uncommitted delivery
                # into normal error-history cleanup and then inject it again on retry.
                if self._member_message_receipts:
                    del self.run_context.messages[message_context_position:]
                    self._member_message_receipts = []
                raise
        execution = current_team_execution.get()
        if execution and execution.admit_work:
            await execution.admit_work()
        receipt = await self._begin_metered_call(
            "model",
            (self._call_checkpoint or {}).get("invocation_id") or uuid.uuid4().hex,
            name=self.provider.provider_config.get("id"),
            contexts=contexts,
            tools=func_tool,
        )
        if receipt:
            params = dict(self.req.llm_params or {})
            params[
                "max_completion_tokens"
                if "max_completion_tokens" in params
                else "max_tokens"
            ] = receipt["max_output_tokens"]
            payload["llm_params"] = params
        reported = False
        try:
            if self.streaming:
                stream = self.provider.text_chat_stream(**payload)
                try:
                    while True:
                        try:
                            with model_delivery(receipt):
                                resp = await self._await_or_stop(anext(stream))  # type: ignore
                        except StopAsyncIteration:
                            return
                        if resp is None:
                            return
                        if not resp.is_chunk:
                            await self._report_metered_call(receipt, resp)
                            reported = True
                        yield resp
                finally:
                    await self._close_executor(stream)
            else:
                with model_delivery(receipt):
                    resp = await self._await_or_stop(self.provider.text_chat(**payload))
                if resp is not None:
                    await self._report_metered_call(receipt, resp)
                    reported = True
                    yield resp
        finally:
            if not reported:
                await self._report_metered_call(receipt)

    async def _iter_llm_responses_with_fallback(
        self,
    ) -> T.AsyncGenerator[LLMResponse, None]:
        """Wrap _iter_llm_responses with provider fallback handling."""
        if self._restored_call and self._resume_requery is not None:
            self._restored_call = False
            self._reused_response = True
            self._reused_parent_response = True
            yield load_call_response(self._resume_requery["parent_response"])
            return
        if (
            self._restored_call
            and self._call_checkpoint is not None
            and self._call_checkpoint["response"] is not None
        ):
            self._restored_call = False
            self._reused_response = True
            yield load_call_response(self._call_checkpoint["response"])
            return
        if not self.run_context.messages:
            logger.warning(
                "Skipping LLM request because no messages remain after agent/request "
                "hooks and context processing."
            )
            yield LLMResponse(
                role="err",
                completion_text="No messages remain for the LLM request.",
            )
            return

        candidates = [self.provider, *self.fallback_providers]
        total_candidates = len(candidates)
        last_exception: Exception | None = None
        last_err_response: LLMResponse | None = None

        for idx, candidate in enumerate(candidates):
            if self._is_stop_requested():
                return
            candidate_id = candidate.provider_config.get("id", "<unknown>")
            is_last_candidate = idx == total_candidates - 1
            if idx > 0:
                logger.warning(
                    "Switched from %s to fallback chat provider: %s",
                    self.provider.provider_config.get("id", "<unknown>"),
                    candidate_id,
                )
            self.provider = candidate
            try:
                retrying = AsyncRetrying(
                    retry=retry_if_exception_type(EmptyModelOutputError),
                    stop=stop_after_attempt(self.EMPTY_OUTPUT_RETRY_ATTEMPTS),
                    wait=wait_exponential(
                        multiplier=1,
                        min=self.EMPTY_OUTPUT_RETRY_WAIT_MIN_S,
                        max=self.EMPTY_OUTPUT_RETRY_WAIT_MAX_S,
                    ),
                    reraise=True,
                )

                async for attempt in retrying:
                    if self._is_stop_requested():
                        return
                    has_stream_output = False
                    with attempt:
                        try:
                            async for resp in self._iter_llm_responses(
                                include_model=idx == 0
                            ):
                                if resp.is_chunk:
                                    has_stream_output = True
                                    yield resp
                                    continue

                                if (
                                    resp.role == "err"
                                    and not has_stream_output
                                    and (not is_last_candidate)
                                ):
                                    last_err_response = resp
                                    logger.warning(
                                        "Chat Model %s returns error response, trying fallback to next provider.",
                                        candidate_id,
                                    )
                                    break

                                self._sanitize_malformed_tool_calls(resp)
                                yield resp
                                return

                            if has_stream_output:
                                return
                        except EmptyModelOutputError:
                            if has_stream_output:
                                logger.warning(
                                    "Chat Model %s returned empty output after streaming started; skipping empty-output retry.",
                                    candidate_id,
                                )
                            else:
                                logger.warning(
                                    "Chat Model %s returned empty output on attempt %s/%s.",
                                    candidate_id,
                                    attempt.retry_state.attempt_number,
                                    self.EMPTY_OUTPUT_RETRY_ATTEMPTS,
                                )
                            raise
                    if self._is_stop_requested():
                        return
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, ProcessPersistenceError):
                    raise
                last_exception = exc
                logger.warning(
                    "Chat Model %s request error: %s",
                    candidate_id,
                    exc,
                    exc_info=True,
                )
                continue

        if last_err_response:
            yield last_err_response
            return
        if last_exception:
            yield LLMResponse(
                role="err",
                completion_text=(
                    "All chat models failed: "
                    f"{type(last_exception).__name__}: {last_exception}"
                ),
            )
            return
        yield LLMResponse(
            role="err",
            completion_text="All available chat models are unavailable.",
        )

    def _sanitize_contexts_for_provider(
        self,
        contexts: list[Message] | list[dict[str, T.Any]],
    ) -> list[Message] | list[dict[str, T.Any]]:
        modalities = self.provider.provider_config.get("modalities", None)
        if (
            not modalities
        ):  # Unconfigured (None or empty list) defaults to support all modalities
            return contexts
        sanitized_contexts, stats = sanitize_contexts_by_modalities(
            contexts,
            self.provider.provider_config.get("modalities", None),
        )
        log_context_sanitize_stats(stats)
        return sanitized_contexts

    def _func_tool_for_provider(self) -> ToolSet | None:
        if not self.req.func_tool:
            return None
        modalities = self.provider.provider_config.get("modalities", None)
        if isinstance(modalities, list) and modalities and "tool_use" not in modalities:
            logger.debug(
                "Provider %s does not support tool_use, clearing tools for request.",
                self.provider,
            )
            return None
        return self.req.func_tool

    def _simple_print_message_role(self, tag: str, messages: list):
        roles = [m.role for m in messages]
        n = len(roles)
        if n > 10:
            summary = ",".join(roles[:4]) + ",...," + ",".join(roles[-4:])
        else:
            summary = ",".join(roles)
        logger.debug(f"{tag} messages -> [{n}] {summary}")

    def follow_up(
        self,
        *,
        message_text: str,
    ) -> FollowUpTicket | None:
        """Queue a follow-up message for the next tool result."""
        if self.done() or self._is_stop_requested():
            return None
        text = (message_text or "").strip()
        if not text:
            return None
        ticket = FollowUpTicket(seq=self._follow_up_seq, text=text)
        self._follow_up_seq += 1
        self._pending_follow_ups.append(ticket)
        self._all_follow_ups.append((ticket.seq, text))
        return ticket

    def inject_system_notice(self, text: str) -> bool:
        """Queue a system notice for the next tool result.

        Used for dashboard-originated events (e.g. the user terminating a
        managed shell session) that the agent must learn about mid-run.
        Rendered with a plain ``[SYSTEM NOTICE]`` prefix rather than the
        follow-up template, which is framed for user chat messages.

        Args:
            text: Notice body; blank text is ignored.

        Returns:
            False when the run is done/stopped or the text is blank.
        """
        if self.done() or self._is_stop_requested():
            return False
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        self._pending_system_notices.append(cleaned)
        return True

    def _resolve_unconsumed_follow_ups(self) -> None:
        if not self._pending_follow_ups:
            return
        follow_ups = self._pending_follow_ups
        self._pending_follow_ups = []
        # Snapshot texts of tickets that were never consumed so post-run
        # consumers (e.g. the subagent handoff relay) can still see them.
        self._unconsumed_follow_ups.extend(
            (t.seq, t.text) for t in follow_ups if not t.consumed
        )
        for ticket in follow_ups:
            ticket.resolved.set()

    def unconsumed_follow_up_texts(self) -> list[tuple[int, str]]:
        """Return (seq, text) for every follow-up never consumed.

        Covers both tickets still pending and tickets already resolved
        (e.g. after the run ended) without having been injected. Read-only:
        this method neither resolves nor consumes tickets.

        Returns:
            List of (seq, text) tuples in arrival order.
        """
        return [
            *self._unconsumed_follow_ups,
            *((t.seq, t.text) for t in self._pending_follow_ups),
        ]

    def all_follow_up_texts(self) -> list[tuple[int, str]]:
        """Return (seq, text) for every follow-up accepted during this run.

        Unlike `unconsumed_follow_up_texts`, this includes follow-ups that
        were consumed (injected into a tool result), so callers such as the
        subagent handoff can report user interventions to the main agent.

        Returns:
            List of (seq, text) tuples in arrival order.
        """
        return list(self._all_follow_ups)

    def _consume_follow_up_notice(self) -> str:
        if not self._pending_follow_ups:
            return ""
        follow_ups = self._pending_follow_ups
        self._pending_follow_ups = []
        for ticket in follow_ups:
            ticket.consumed = True
            ticket.resolved.set()
        follow_up_lines = "\n".join(
            f"{idx}. {ticket.text}" for idx, ticket in enumerate(follow_ups, start=1)
        )
        return self.FOLLOW_UP_NOTICE_TEMPLATE.format(
            follow_up_lines=follow_up_lines,
        )

    def _merge_follow_up_notice(self, content: str) -> str:
        if self._pending_system_notices:
            notices = self._pending_system_notices
            self._pending_system_notices = []
            content += "".join(f"\n\n[SYSTEM NOTICE] {notice}" for notice in notices)
        notice = self._consume_follow_up_notice()
        if not notice:
            return content
        return f"{content}{notice}"

    def _track_tool_call_streak(
        self,
        tool_name: str,
        tool_args: dict[str, T.Any] | None,
    ) -> int:
        """Track consecutive tool calls with the same name and arguments.

        Args:
            tool_name: Name of the called tool.
            tool_args: Arguments passed to the tool.

        Returns:
            Number of consecutive calls with the same name and arguments.
        """
        normalized_args = {} if tool_args is None else tool_args
        if (
            tool_name == self._last_tool_name
            and normalized_args == self._last_tool_args
        ):
            self._same_tool_streak += 1
        else:
            self._last_tool_name = tool_name
            self._last_tool_args = copy.deepcopy(normalized_args)
            self._same_tool_streak = 1
        return self._same_tool_streak

    @classmethod
    def _normalize_repeated_tool_notice_threshold(cls, value: T.Any) -> int:
        if isinstance(value, bool):
            return cls.REPEATED_TOOL_NOTICE_DEFAULT_THRESHOLD
        try:
            threshold = int(value)
        except (TypeError, ValueError):
            return cls.REPEATED_TOOL_NOTICE_DEFAULT_THRESHOLD
        return max(1, threshold)

    def _build_repeated_tool_call_guidance(self, tool_name: str, streak: int) -> str:
        if not self.repeated_tool_notice_enabled:
            return ""

        l1_threshold = self.repeated_tool_notice_threshold
        l2_threshold = l1_threshold + 1
        l3_threshold = l1_threshold + 2

        if streak < l1_threshold:
            return ""

        if streak >= l3_threshold:
            return self.REPEATED_TOOL_NOTICE_L3_TEMPLATE.format(
                tool_name=tool_name,
                streak=streak,
            )

        if streak >= l2_threshold:
            return self.REPEATED_TOOL_NOTICE_L2_TEMPLATE.format(
                tool_name=tool_name,
                streak=streak,
            )

        return self.REPEATED_TOOL_NOTICE_L1_TEMPLATE.format(
            tool_name=tool_name,
            streak=streak,
        )

    def _sanitize_malformed_tool_calls(
        self,
        llm_resp: LLMResponse,
    ) -> None:
        """Normalize malformed tool call names.

        Args:
            llm_resp: The LLM response whose tool call lists should be sanitized.
        """
        llm_resp.tools_call_name = [
            self.MALFORMED_TOOL_NAME_PLACEHOLDER
            if tool_name is None or tool_name.strip() == ""
            else tool_name
            for tool_name in llm_resp.tools_call_name
        ]

    async def _record_model_response(self, response: LLMResponse) -> None:
        """Save complete provider output on all three model response paths.

        Args:
            response: Complete provider response before display or tool execution.
        """

        if getattr(self, "_reused_response", False):
            self._reused_response = False
            if getattr(self, "_reused_parent_response", False):
                return
            if self._call_checkpoint and "tool_calls" not in self._call_checkpoint:
                # Upgrade an original T07 response without replanning its calls.
                self._call_checkpoint["format_version"] = 2
                await self._save_call_checkpoint("response", response=response)
        else:
            await self._save_call_checkpoint("response", response=response)
        execution = current_team_execution.get()
        if execution and execution.record_process:
            await execution.record_process(
                self._call_checkpoint["invocation_id"]
                if self._call_checkpoint
                else uuid.uuid4().hex,
                "model_response",
                {
                    "id": response.id,
                    "role": response.role,
                    "text": response.completion_text,
                    "parts": [
                        component.toDict() for component in response.result_chain.chain
                    ]
                    if response.result_chain
                    else [],
                    "reasoning_content": response.reasoning_content,
                    "reasoning_signature": response.reasoning_signature,
                    "tool_calls": [
                        {"id": call_id, "name": name, "args": args}
                        for call_id, name, args in zip(
                            response.tools_call_ids,
                            response.tools_call_name,
                            response.tools_call_args,
                        )
                    ],
                    "tools_call_extra_content": response.tools_call_extra_content,
                    "raw_completion": response.raw_completion.model_dump(mode="json")
                    if response.raw_completion
                    else None,
                },
            )

    @override
    async def step(self):
        """Process a single step of the agent.
        This method should return the result of the step.
        """
        if not self.req:
            raise ValueError("Request is not set. Please call reset() first.")

        if self._step_budget_max is not None and not self._restored_call:
            self._step_budget_used += 1

        if self._state == AgentState.IDLE:
            try:
                await self.agent_hooks.on_agent_begin(self.run_context)
            except Exception as e:
                logger.error(f"Error in on_agent_begin hook: {e}", exc_info=True)

        # 开始处理，转换到运行状态
        self._transition_state(AgentState.RUNNING)
        llm_resp_result = None

        if not self._restored_call:
            # Process request-time context before sending it to the provider.
            token_usage = (
                self.req.conversation.token_usage if self.req.conversation else 0
            )
            self._simple_print_message_role("[BefCompact]", self.run_context.messages)
            processed_messages = await self._await_or_stop(
                self.request_context_manager.process(
                    self.run_context.messages,
                    trusted_token_usage=token_usage,
                    # Same tool set the chat requests send, so the summary
                    # request's payload prefix (tools + system + history) stays
                    # byte-identical and hits the provider prefix cache. The
                    # summary is a one-shot call: the schema is visible but
                    # nothing executes the tools.
                    func_tool=self._func_tool_for_provider(),
                )
            )
            if processed_messages is None:
                yield await self._finalize_aborted_step()
                return
            self.run_context.messages = processed_messages
            self._simple_print_message_role("[AftCompact]", self.run_context.messages)

            # Announce a compression that just happened: webchat turns it into a
            # transient notice. Emitted before the LLM request so the user learns
            # why this turn's context (and possibly its answer) changed shape while
            # still waiting for the reply.
            compression_report = self.request_context_manager.last_compression
            if compression_report is not None:
                yield AgentResponse(
                    type="context_compression",
                    data=AgentResponseData(
                        chain=MessageChain(
                            type="context_compression",
                            chain=[Json(data=compression_report)],
                        )
                    ),
                )

        async for llm_response in self._iter_llm_responses_with_fallback():
            if llm_response.is_chunk:
                if self.stats.time_to_first_token == 0:
                    self.stats.time_to_first_token = time.time() - self.stats.start_time

                if llm_response.reasoning_content:
                    yield AgentResponse(
                        type="streaming_delta",
                        data=AgentResponseData(
                            chain=MessageChain(type="reasoning").message(
                                llm_response.reasoning_content,
                            ),
                        ),
                    )
                if llm_response.result_chain:
                    yield AgentResponse(
                        type="streaming_delta",
                        data=AgentResponseData(chain=llm_response.result_chain),
                    )
                elif llm_response.completion_text:
                    yield AgentResponse(
                        type="streaming_delta",
                        data=AgentResponseData(
                            chain=MessageChain().message(llm_response.completion_text),
                        ),
                    )
                if self._is_stop_requested():
                    break
                continue
            llm_resp_result = llm_response
            await self._record_model_response(llm_response)

            # Chunk responses have already continued above. A missing usage report
            # means the latest context occupancy is unknown.
            usage_saved = bool(
                self._call_checkpoint
                and self._call_checkpoint["phase"].startswith("tool")
            )
            if not usage_saved:
                self.stats.current_context_tokens = 0
            if (
                llm_response.usage
                and not getattr(self, "_reused_parent_response", False)
                and not usage_saved
            ):
                # Keep cumulative usage for billing and expose the latest request
                # input separately for context-window occupancy displays.
                self.stats.token_usage += llm_response.usage
                self.stats.current_context_tokens = llm_response.usage.input
                if self.req.conversation:
                    self.req.conversation.token_usage = llm_response.usage.total
            self._reused_parent_response = False
            # end_time must be set before the yield serializes to_dict().
            self.stats.end_time = time.time()
            yield AgentResponse(
                type="agent_stats",
                data=AgentResponseData(
                    chain=MessageChain(
                        type="agent_stats",
                        chain=[Json(data=self.stats.to_dict())],
                    )
                ),
            )
            injection_resp = self._injection_trace_response()
            if injection_resp is not None:
                yield injection_resp
            break  # got final response

        if self._is_stop_requested():
            yield await self._finalize_aborted_step()
            return

        if not llm_resp_result:
            return

        # 处理 LLM 响应
        llm_resp = llm_resp_result

        if llm_resp.role == "err":
            # 如果 LLM 响应错误，转换到错误状态
            self.final_llm_resp = llm_resp
            self.stats.end_time = time.time()
            self._transition_state(AgentState.ERROR)
            self._resolve_unconsumed_follow_ups()
            custom_error_message = self._get_persona_custom_error_message()
            error_text = custom_error_message or (
                f"LLM 响应错误: {llm_resp.completion_text or '未知错误'}"
            )
            yield AgentResponse(
                type="err",
                data=AgentResponseData(
                    chain=MessageChain().message(error_text),
                ),
            )
            return

        if not llm_resp.tools_call_name:
            await self._complete_with_assistant_response(llm_resp)

        # 返回 LLM 结果
        if llm_resp.reasoning_content:
            yield AgentResponse(
                type="llm_result",
                data=AgentResponseData(
                    chain=MessageChain(type="reasoning").message(
                        llm_resp.reasoning_content,
                    ),
                ),
            )
        if llm_resp.result_chain:
            yield AgentResponse(
                type="llm_result",
                data=AgentResponseData(chain=llm_resp.result_chain),
            )
        elif llm_resp.completion_text:
            yield AgentResponse(
                type="llm_result",
                data=AgentResponseData(
                    chain=MessageChain().message(llm_resp.completion_text),
                ),
            )

        if not llm_resp.tools_call_name:
            file_changes_resp = await self._build_file_changes_response()
            if file_changes_resp:
                yield file_changes_resp

        # 如果有工具调用，还需处理工具调用
        if llm_resp.tools_call_name:
            if self.tool_schema_mode == "skills_like":
                requery_resp, _ = await self._resolve_tool_exec(llm_resp)
                if self._is_stop_requested():
                    yield await self._finalize_aborted_step()
                    return
                if not requery_resp.tools_call_name:
                    llm_resp = requery_resp
                    logger.warning(
                        "skills_like tool re-query returned no tool calls; fallback to assistant response."
                    )
                    if llm_resp.reasoning_content:
                        yield AgentResponse(
                            type="llm_result",
                            data=AgentResponseData(
                                chain=MessageChain(type="reasoning").message(
                                    llm_resp.reasoning_content,
                                ),
                            ),
                        )
                    if llm_resp.result_chain:
                        yield AgentResponse(
                            type="llm_result",
                            data=AgentResponseData(chain=llm_resp.result_chain),
                        )
                    elif llm_resp.completion_text:
                        yield AgentResponse(
                            type="llm_result",
                            data=AgentResponseData(
                                chain=MessageChain().message(llm_resp.completion_text),
                            ),
                        )

                    await self._complete_with_assistant_response(llm_resp)
                    # Re-query uses text_chat(), so its reply has no stream chunks.
                    # Supply them after hooks without changing llm_result ordering.
                    if self.streaming:
                        if llm_resp.reasoning_content:
                            yield AgentResponse(
                                type="streaming_delta",
                                data=AgentResponseData(
                                    chain=MessageChain(type="reasoning").message(
                                        llm_resp.reasoning_content,
                                    ),
                                ),
                            )
                        chain = llm_resp.result_chain
                        if not chain and llm_resp.completion_text:
                            chain = MessageChain().message(llm_resp.completion_text)
                        if chain:
                            yield AgentResponse(
                                type="streaming_delta",
                                data=AgentResponseData(chain=chain),
                            )
                    file_changes_resp = await self._build_file_changes_response()
                    if file_changes_resp:
                        yield file_changes_resp
                    return
                else:
                    llm_resp = requery_resp

            tool_call_result_blocks = []
            cached_images = []  # Collect cached images for LLM visibility
            try:
                async for result in self._handle_function_tools(self.req, llm_resp):
                    if result.kind == "tool_call_result_blocks":
                        if result.tool_call_result_blocks is not None:
                            tool_call_result_blocks = result.tool_call_result_blocks
                    elif result.kind == "cached_image":
                        if result.cached_image is not None:
                            # Collect cached image info
                            cached_images.append(result.cached_image)
                    elif result.kind == "message_chain":
                        chain = result.message_chain
                        if chain is None or chain.type is None:
                            # should not happen
                            continue
                        if chain.type == "tool_direct_result":
                            ar_type = "tool_call_result"
                        else:
                            ar_type = chain.type
                        yield AgentResponse(
                            type=ar_type,
                            data=AgentResponseData(chain=chain),
                        )
            except _ToolExecutionInterrupted:
                yield await self._finalize_aborted_step()
                return

            # Keep budget state outside messages so compression cannot reset it.
            if tool_call_result_blocks and self._step_budget_max is not None:
                notices = []
                max_steps = self._step_budget_max
                used_steps = self._step_budget_used
                remaining = max(0, max_steps - used_steps)
                if max_steps >= 16:
                    for percent, guidance in (
                        (80, "Focus on the core goal; avoid new exploration."),
                        (
                            90,
                            "Wrap up; prioritize essential fixes and verification.",
                        ),
                        (
                            95,
                            "Finish verification and reply; report unfinished or unverified work.",
                        ),
                    ):
                        if (
                            used_steps * 100 >= max_steps * percent
                            and percent not in self._step_budget_notified
                        ):
                            self._step_budget_notified.add(percent)
                            notices.append(
                                f"[SYSTEM NOTICE: Agent step budget {percent}%] "
                                f"Steps used: {used_steps}/{max_steps}. Remaining steps: {remaining}. "
                                f"{guidance}"
                            )
                if used_steps >= max_steps and 100 not in self._step_budget_notified:
                    self._step_budget_notified.add(100)
                    notices.append(self.MAX_STEPS_REACHED_PROMPT)
                if notices:
                    # Annotate only this round's final result, preserving tool-call IDs.
                    notice = "\n\n" + "\n".join(notices)
                    last_result = tool_call_result_blocks[-1]
                    if isinstance(last_result.content, str):
                        last_result.content += notice
                    else:
                        last_result.content = [
                            *(last_result.content or []),
                            TextPart(text=notice),
                        ]

            # 将结果添加到上下文中
            parts = []
            if llm_resp.reasoning_content is not None or llm_resp.reasoning_signature:
                parts.append(
                    ThinkPart(
                        think=llm_resp.reasoning_content or "",
                        encrypted=llm_resp.reasoning_signature,
                    )
                )
            if llm_resp.completion_text:
                parts.append(TextPart(text=llm_resp.completion_text))
            if len(parts) == 0:
                parts = None
            tool_calls_result = ToolCallsResult(
                tool_calls_info=AssistantMessageSegment(
                    tool_calls=llm_resp.to_openai_tool_calls_model(),
                    content=parts,
                ),
                tool_calls_result=tool_call_result_blocks,
            )
            # record the assistant message with tool calls
            self.run_context.messages.extend(
                tool_calls_result.to_openai_messages_model()
            )

            # If there are cached images and the model supports image input,
            # append a user message with images so LLM can see them
            if cached_images:
                modalities = self.provider.provider_config.get("modalities", [])
                supports_image = (
                    not modalities or "image" in modalities
                )  # Empty list is treated as unconfigured for backward compatibility
                if supports_image:
                    # Build user message with images for LLM to review
                    image_parts = []
                    for cached_img in cached_images:
                        img_data = tool_image_cache.get_image_base64_by_path(
                            cached_img.file_path, cached_img.mime_type
                        )
                        if img_data:
                            base64_data, mime_type = img_data
                            image_parts.append(
                                TextPart(
                                    text=f"[Image from tool '{cached_img.tool_name}', path='{cached_img.file_path}']"
                                )
                            )
                            image_parts.append(
                                ImageURLPart(
                                    image_url=ImageURLPart.ImageURL(
                                        url=f"data:{mime_type};base64,{base64_data}",
                                        id=cached_img.file_path,
                                    )
                                )
                            )
                    if image_parts:
                        self.run_context.messages.append(
                            Message(role="user", content=image_parts)
                        )
                        logger.debug(
                            f"Appended {len(cached_images)} cached image(s) to context for LLM review"
                        )

            self.req.append_tool_calls_result(tool_calls_result)
            if self._checkpoint_enabled and self._call_checkpoint:
                position = self._call_checkpoint["context_position"] + 1
                for call in self._call_checkpoint["tool_calls"]:
                    call["status"] = "applied"
                    call["context_position"] = position
                    position += len(call["protocol_results"])
                self._call_checkpoint["messages"] = dump_call_messages(
                    self.run_context.messages
                )
                await self._save_call_checkpoint("tools_applied")

    async def step_until_done(
        self, max_step: int
    ) -> T.AsyncGenerator[AgentResponse, None]:
        """Process steps until the agent is done."""
        if not self._resumed_attempt:
            self._step_budget_max = max_step
            self._step_budget_used = 0
            self._step_budget_notified = set()
        step_count = max(0, self._step_budget_used - int(self._restored_call))
        while not self.done() and step_count < max_step:
            step_count += 1
            async for resp in self.step():
                yield resp

        #  如果循环结束了但是 agent 还没有完成，说明是达到了 max_step
        if not self.done():
            logger.warning(
                f"Agent reached max steps ({max_step}), forcing a final response."
            )
            # 拔掉所有工具的执行权限，但不在 payload 中移除工具 schema：
            # 工具段保持字节不变，provider 侧前缀缓存不被破坏；模型若仍
            # 尝试调用，会收到一条可感知的拒绝错误。
            if self.req and self.req.func_tool:
                self.req.denied_tools = set(self.req.func_tool.names())
            # 注入提示词
            self.run_context.messages.append(
                Message(
                    role="user",
                    content=self.MAX_STEPS_REACHED_PROMPT,
                )
            )
            # 再执行最后一步
            async for resp in self.step():
                yield resp
            # 若模型在收尾轮仍尝试调用已禁用的工具（每轮都会收到拒绝错误），
            # 给它有限的额外轮次基于拒绝反馈完成总结。
            recovery = 0
            while not self.done() and recovery < self.MAX_STEP_DENIAL_RECOVERY_ROUNDS:
                recovery += 1
                async for resp in self.step():
                    yield resp

    @staticmethod
    def _cache_tool_image(
        content_item: T.Any, tool_call_id: str, tool_name: str, index: int
    ) -> CachedImage | None:
        """Materialize image bytes identically for live and saved tool results.

        Args:
            content_item: Original MCP image or embedded image resource.
            tool_call_id: Call identity used by the existing image cache.
            tool_name: Actual tool that produced this image.
            index: Content position within the original result.

        Returns:
            A cached image, or None for non-image content.
        """
        if isinstance(content_item, ImageContent):
            data, mime_type = content_item.data, content_item.mimeType
        elif (
            isinstance(content_item, EmbeddedResource)
            and isinstance(content_item.resource, BlobResourceContents)
            and content_item.resource.mimeType
            and content_item.resource.mimeType.startswith("image/")
        ):
            data, mime_type = content_item.resource.blob, content_item.resource.mimeType
        else:
            return None
        return tool_image_cache.save_image(
            base64_data=data,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            index=index,
            mime_type=mime_type or "image/png",
        )

    async def _reconcile_tool_call(self, call: dict, tool: FunctionTool | None) -> bool:
        """Resolve uncertainty only through the adapter's original guarantees.

        Args:
            call: Original per-tool checkpoint, including partial output evidence.
            tool: Currently authorized adapter rebuilt by the normal pipeline.

        Returns:
            Whether the call has a proven result or can safely execute again.

        Raises:
            ProcessPersistenceError: A reconciliation fact cannot be saved.
        """
        metadata = call.setdefault("recovery", {})
        declaration = metadata.get("declaration")
        if (
            tool is None
            or tool.recovery is None
            or declaration != tool.recovery.declaration
        ):
            return False
        contract = tool.recovery
        if contract.query is not None:
            execution = current_team_execution.get()
            if execution and execution.admit_work:
                await execution.admit_work()
            receipt = await self._begin_metered_call(
                "query", call["record_id"], name=call["name"]
            )
            try:
                queried = await self._await_or_stop(
                    asyncio.wait_for(
                        contract.query(
                            self.run_context,
                            call["record_id"],
                            copy.deepcopy(call["args"]),
                        ),
                        timeout=self.run_context.tool_call_timeout,
                    )
                )
                result = ToolRecoveryResult.model_validate(queried)
            except _ToolExecutionInterrupted:
                raise
            except Exception as exc:
                result = ToolRecoveryResult(
                    status="unknown",
                    evidence=f"Status query failed: {type(exc).__name__}: {exc}",
                )
            finally:
                await self._report_metered_call(receipt)
            metadata.setdefault("queries", []).append(
                result.model_dump(
                    mode="json",
                    exclude={"results"}
                    if result.status in {"succeeded", "failed"}
                    else None,
                )
            )
            if result.status in {"succeeded", "failed"}:
                complete = [raw.model_dump(mode="json") for raw in result.results]
                known = [
                    CallToolResult.model_validate(raw).model_dump(mode="json")
                    for raw in call["raw_results"]
                ]
                if complete[: len(known)] != known:
                    metadata["queries"][-1].update(
                        {
                            "accepted": False,
                            "reason": "The complete query result conflicts with saved partial output.",
                            "results": complete,
                        }
                    )
                    await self._save_call_checkpoint("tool_progress")
                    return False
                # The query returns the complete original sequence. Saved partial
                # output stays intact and contributes to context exactly once.
                for data in complete[len(known) :]:
                    index = len(call["raw_results"])
                    call["raw_results"].append(data)
                    call["process_records"].append(
                        {
                            "id": call["record_id"] + f":raw:{index}",
                            "data": {
                                "id": call["call_id"],
                                "name": call["name"],
                                "raw_result": data,
                            },
                        }
                    )
                call["protocol_results"] = []
                call["status"] = "result_saved"
                await self._save_call_checkpoint("tool_result")
                return True
            # An ambiguous query cannot justify replay, even if another declared
            # capability exists. The operator can request a subsequent query.
            await self._save_call_checkpoint("tool_progress")
            if result.status != "not_executed":
                return False
        elif not (contract.idempotency_key_arg or contract.safe_retry):
            return False
        metadata.setdefault("retries", []).append(
            {
                "identity": call["record_id"],
                "reason": "not_executed"
                if contract.query
                else "idempotency_key"
                if contract.idempotency_key_arg
                else "safe_retry",
                "protocol_before": copy.deepcopy(call["protocol_results"]),
            }
        )
        # Retain the old protocol as evidence while rebuilding it from the
        # verified live sequence. Raw output and process identities stay intact.
        call["protocol_results"] = []
        await self._save_call_checkpoint("tool_progress")
        return True

    async def _handle_function_tools(
        self,
        req: ProviderRequest,
        llm_response: LLMResponse,
    ) -> T.AsyncGenerator[_HandleFunctionToolsResult, None]:
        """Execute or reuse individual calls before applying the complete batch.

        Args:
            req: Effective request with the current permission restrictions.
            llm_response: Original saved plan, including provider tool signatures.

        Yields:
            Existing display events and complete tool protocol results.

        Raises:
            ProcessPersistenceError: A required result could not be saved.
            CheckpointRecoveryError: An external effect has no saved result.
        """
        tool_call_result_blocks: list[ToolCallMessageSegment] = []
        logger.info(f"Agent 使用工具: {llm_response.tools_call_name}")

        async def _append_tool_call_result(
            tool_call_id: str, content: str, *, raw_result_saved: bool = False
        ) -> None:
            """Save distinct protocol results before appending model context.

            Args:
                tool_call_id: Actual requested tool call identity.
                content: Complete protocol result before follow-up notices.
                raw_result_saved: This content represents an already saved raw result.
            """
            execution = current_team_execution.get()
            block = ToolCallMessageSegment(
                role="tool",
                tool_call_id=tool_call_id,
                content=self._merge_follow_up_notice(content),
            )
            record_id = (
                call_state["record_id"] + ":protocol"
                if call_state
                else uuid.uuid4().hex
            )
            record_data = {
                "id": tool_call_id,
                "name": func_tool_name,
                "raw_result": None,
                "result": content,
            }
            if call_state is not None:
                call_state["protocol_results"].append(block.model_dump(mode="json"))
                if not raw_result_saved:
                    call_state["process_records"].append(
                        {"id": record_id, "data": record_data}
                    )
                if call_state["status"] != "started":
                    call_state["status"] = "result_saved"
                await self._save_call_checkpoint(
                    "tool_progress"
                    if call_state["status"] == "started"
                    else "tool_result"
                )
            if (
                execution
                and execution.record_process
                and not raw_result_saved
                and (call_state is None or call_state["status"] == "result_saved")
            ):
                await execution.record_process(
                    record_id,
                    "tool_result",
                    record_data,
                )
            tool_call_result_blocks.append(block)

        # 获取 trace span：优先使用 subagent trace_span，否则回退到 event.trace
        _agent_ctx = getattr(self.run_context, "context", None)
        _trace = getattr(_agent_ctx, "trace_span", None)
        if _trace is None and _agent_ctx is not None:
            _event = getattr(_agent_ctx, "event", None)
            _trace = getattr(_event, "trace", None)

        # 合并两个作用域的工具禁用集合：请求级（max_step 强制收尾轮、插件
        # plan 模式通过 req.denied_tools 设置）与代理级（subagent 通过
        # AstrAgentContext.extra 播种编排类工具）。被禁工具的 schema 仍然
        # 序列化进 payload（前缀缓存友好），仅在执行时拒绝。
        denied_tools: set[str] = set(req.denied_tools or ())
        _context_extra = getattr(_agent_ctx, "extra", None) or {}
        denied_tools.update(_context_extra.get("denied_tools") or ())

        # 执行函数调用
        unresolved = []
        for call_index, (func_tool_name, func_tool_args, func_tool_id) in enumerate(
            zip(
                llm_response.tools_call_name,
                llm_response.tools_call_args,
                llm_response.tools_call_ids,
            )
        ):
            call_state = (
                self._call_checkpoint["tool_calls"][call_index]
                if self._checkpoint_enabled and self._call_checkpoint
                else None
            )
            reused = call_state is not None and call_state["status"] == "result_saved"
            retry_prefix: list[dict] | None = None
            if call_state is not None and call_state["status"] in {
                "started",
                "unknown",
            }:
                available = self.effective_raw_tool_set
                tool = available.get_tool(func_tool_name) if available else None
                if (
                    func_tool_name in denied_tools
                    or not await self._reconcile_tool_call(call_state, tool)
                ):
                    unresolved.append(func_tool_id)
                    continue
                reused = call_state["status"] == "result_saved"
                if not reused:
                    retry_prefix = [
                        CallToolResult.model_validate(raw).model_dump(mode="json")
                        for raw in call_state["raw_results"]
                    ]
            if reused and call_state and call_state["protocol_results"]:
                execution = current_team_execution.get()
                if execution and execution.record_process:
                    for record in call_state.get("process_records", []):
                        await execution.record_process(
                            record["id"], "tool_result", record["data"]
                        )
                # Restore image evidence from durable bytes even after temporary
                # caches were cleared while the worker was stopped.
                for raw in call_state["raw_results"]:
                    for index, item in enumerate(
                        CallToolResult.model_validate(raw).content
                    ):
                        cached_image = self._cache_tool_image(
                            item, func_tool_id, func_tool_name, index
                        )
                        if cached_image:
                            yield _HandleFunctionToolsResult.from_cached_image(
                                cached_image
                            )
                tool_call_result_blocks.extend(
                    ToolCallMessageSegment.model_validate(block)
                    for block in call_state["protocol_results"]
                )
                continue
            tool_result_blocks_start = len(tool_call_result_blocks)
            tool_call_streak = (
                self._same_tool_streak
                if reused
                else self._track_tool_call_streak(
                    func_tool_name,
                    func_tool_args,
                )
            )
            yield _HandleFunctionToolsResult.from_message_chain(
                MessageChain(
                    type="tool_call",
                    chain=[
                        Json(
                            data={
                                "id": func_tool_id,
                                "name": func_tool_name,
                                "args": func_tool_args,
                                "ts": time.time(),
                            }
                        )
                    ],
                )
            )
            # 记录工具调用追踪
            if _trace:
                _trace.record("agent_tool_call", tool_name=func_tool_name)
            # 工具对 LLM 可见但执行被禁：返回一条可感知的错误结果，让模型
            # 知道它已无法再使用该工具。放在 func_tool 判空之前，即使工具
            # 段不存在，幻觉出的调用也能拿到协议完整的错误结果。
            if func_tool_name in denied_tools:
                logger.info(f"[Permission] 工具 {func_tool_name} 已被禁用，拒绝调用。")
                await _append_tool_call_result(
                    func_tool_id,
                    f"error: Permission denied. Tool `{func_tool_name}` is no "
                    "longer available to you in the current state. Do not retry "
                    "it; continue your task with the information you already "
                    "have.",
                )
                continue
            tool_failed = False
            invocation_token = None
            usage_receipt = None
            try:
                if not req.func_tool:
                    return

                # Resolve tool from regular tool sets
                if (
                    self.tool_schema_mode == "skills_like"
                    and self._skill_like_raw_tool_set
                ):
                    # in 'skills_like' mode, raw.func_tool is light schema, does not have handler
                    # so we need to get the tool from the raw tool set
                    func_tool = self._skill_like_raw_tool_set.get_tool(func_tool_name)
                else:
                    func_tool = req.func_tool.get_tool(func_tool_name)
                if (
                    self.tool_schema_mode == "skills_like"
                    and self._skill_like_raw_tool_set
                ):
                    # in 'skills_like' mode, raw.func_tool is light schema, does not have handler
                    # so we need to get the tool from the raw tool set
                    func_tool = self._skill_like_raw_tool_set.get_tool(func_tool_name)
                    available_tools = self._skill_like_raw_tool_set.names()
                else:
                    func_tool = req.func_tool.get_tool(func_tool_name)
                    available_tools = req.func_tool.names()

                #  Some API may return None for tools with no parameters
                if func_tool_args is None:
                    func_tool_args = {}
                logger.info(f"使用工具：{func_tool_name}，参数：{func_tool_args}")

                if not func_tool:
                    logger.warning(f"未找到指定的工具: {func_tool_name}，将跳过。")
                    await _append_tool_call_result(
                        func_tool_id,
                        f"error: Tool {func_tool_name} not found. Available tools are: {', '.join(available_tools)}",
                    )
                    continue

                valid_params = {}  # 参数过滤：只传递函数实际需要的参数

                # 获取实际的 handler 函数
                if func_tool.handler:
                    logger.debug(
                        f"工具 {func_tool_name} 期望的参数: {func_tool.parameters}",
                    )
                    if func_tool.parameters and func_tool.parameters.get("properties"):
                        expected_params = set(func_tool.parameters["properties"].keys())

                        valid_params = {
                            k: v
                            for k, v in func_tool_args.items()
                            if k in expected_params
                        }

                    # 记录被忽略的参数
                    ignored_params = set(func_tool_args.keys()) - set(
                        valid_params.keys(),
                    )
                    if ignored_params:
                        logger.warning(
                            f"工具 {func_tool_name} 忽略非期望参数: {ignored_params}",
                        )
                else:
                    # 如果没有 handler（如 MCP 工具），使用所有参数
                    valid_params = func_tool_args

                execution = current_team_execution.get()
                if execution and execution.admit_work:
                    await execution.admit_work()
                if not reused:
                    usage_receipt = await self._begin_metered_call(
                        "tool",
                        call_state["record_id"] if call_state else uuid.uuid4().hex,
                        name=func_tool_name,
                    )
                if call_state is not None and not reused:
                    assert self._call_checkpoint is not None
                    if func_tool_id not in self._call_checkpoint["started_tools"]:
                        self._call_checkpoint["started_tools"].append(func_tool_id)
                    metadata = call_state.setdefault("recovery", {})
                    metadata["declaration"] = (
                        func_tool.recovery.declaration if func_tool.recovery else None
                    )
                    metadata["identity"] = call_state["record_id"]
                    if func_tool.recovery and func_tool.recovery.idempotency_key_arg:
                        valid_params = {
                            **valid_params,
                            func_tool.recovery.idempotency_key_arg: call_state[
                                "record_id"
                            ],
                        }
                        metadata["idempotency_key"] = call_state["record_id"]
                    call_state["status"] = "started"
                    await self._save_call_checkpoint("tools_started")

                invocation_token = current_tool_invocation.set(
                    call_state["record_id"] if call_state else uuid.uuid4().hex
                )

                try:
                    if not reused:
                        await self.agent_hooks.on_tool_start(
                            self.run_context,
                            func_tool,
                            valid_params,
                        )
                except Exception as e:
                    logger.error(f"Error in on_tool_start hook: {e}", exc_info=True)

                executor = (
                    None
                    if reused
                    else self.tool_executor.execute(
                        tool=func_tool,
                        run_context=self.run_context,
                        **valid_params,  # 只传递有效的参数
                    )
                )

                _final_resp: CallToolResult | None = None
                replay_index = 0
                async for resp in self._iter_tool_executor_results(
                    T.cast(T.AsyncGenerator[T.Any, None] | None, executor),
                    saved_results=call_state["raw_results"]
                    if reused and call_state
                    else None,
                ):  # type: ignore
                    known_result = retry_prefix is not None and replay_index < len(
                        retry_prefix
                    )
                    if (
                        retry_prefix is not None
                        and known_result
                        and (
                            not isinstance(resp, CallToolResult)
                            or resp.model_dump(mode="json")
                            != retry_prefix[replay_index]
                        )
                    ):
                        assert call_state is not None
                        call_state["recovery"]["retries"][-1]["conflict"] = {
                            "index": replay_index,
                            "reason": "Live retry output conflicts with saved partial output.",
                            "result": resp.model_dump(mode="json")
                            if isinstance(resp, CallToolResult)
                            else repr(resp),
                        }
                        tool_failed = True
                        break
                    if isinstance(resp, CallToolResult):
                        raw_index = (
                            len(call_state["raw_results"])
                            if call_state and not reused and not known_result
                            else replay_index
                        )
                        replay_index += 1
                        if call_state is not None and not reused and not known_result:
                            call_state["raw_results"].append(
                                resp.model_dump(mode="json")
                            )
                            call_state["process_records"].append(
                                {
                                    "id": call_state["record_id"] + f":raw:{raw_index}",
                                    "data": {
                                        "id": func_tool_id,
                                        "name": func_tool_name,
                                        "raw_result": resp.model_dump(mode="json"),
                                    },
                                }
                            )
                            # A generator may yield more effects and results.
                            # Only exhaustion below proves that this call finished.
                            await self._save_call_checkpoint("tool_progress")
                        execution = current_team_execution.get()
                        if (
                            execution
                            and execution.record_process
                            and call_state is None
                        ):
                            # Keep the original result before overflow materialization
                            # or display filtering can shorten its content.
                            await execution.record_process(
                                call_state["record_id"] + f":raw:{raw_index}"
                                if call_state
                                else uuid.uuid4().hex,
                                "tool_result",
                                {
                                    "id": func_tool_id,
                                    "name": func_tool_name,
                                    "raw_result": resp.model_dump(mode="json"),
                                },
                            )
                        res = resp
                        _final_resp = resp
                        if not res.content:
                            await _append_tool_call_result(
                                func_tool_id,
                                "The tool returned no content.",
                                raw_result_saved=True,
                            )
                            continue

                        result_parts: list[str] = []
                        for index, content_item in enumerate(res.content):
                            if isinstance(content_item, TextContent):
                                result_parts.append(content_item.text)
                            elif isinstance(content_item, ImageContent):
                                # Cache the image instead of sending directly
                                cached_img = self._cache_tool_image(
                                    content_item, func_tool_id, func_tool_name, index
                                )
                                assert cached_img is not None
                                result_parts.append(
                                    self.TOOL_IMAGE_NOTICE_TEMPLATE.format(
                                        file_path=cached_img.file_path
                                    )
                                )
                                # Yield image info for LLM visibility (will be handled in step())
                                yield _HandleFunctionToolsResult.from_cached_image(
                                    cached_img
                                )
                            elif isinstance(content_item, EmbeddedResource):
                                resource = content_item.resource
                                if isinstance(resource, TextResourceContents):
                                    result_parts.append(resource.text)
                                elif (
                                    isinstance(resource, BlobResourceContents)
                                    and resource.mimeType
                                    and resource.mimeType.startswith("image/")
                                ):
                                    # Cache the image instead of sending directly
                                    cached_img = self._cache_tool_image(
                                        content_item,
                                        func_tool_id,
                                        func_tool_name,
                                        index,
                                    )
                                    assert cached_img is not None
                                    result_parts.append(
                                        self.TOOL_IMAGE_NOTICE_TEMPLATE.format(
                                            file_path=cached_img.file_path
                                        )
                                    )
                                    # Yield image info for LLM visibility
                                    yield _HandleFunctionToolsResult.from_cached_image(
                                        cached_img
                                    )
                                else:
                                    result_parts.append(
                                        "The tool has returned a data type that is not supported."
                                    )
                        if result_parts:
                            inline_result = "\n\n".join(result_parts)
                            inline_result = await self._materialize_large_tool_result(
                                tool_call_id=func_tool_id,
                                content=inline_result,
                            )
                            await _append_tool_call_result(
                                func_tool_id,
                                inline_result
                                + self._build_repeated_tool_call_guidance(
                                    func_tool_name, tool_call_streak
                                ),
                                raw_result_saved=True,
                            )

                    elif resp is None:
                        # Tool 直接请求发送消息给用户
                        # 这里我们将直接结束 Agent Loop
                        # 发送消息逻辑在 ToolExecutor 中处理了
                        logger.warning(
                            f"{func_tool_name} 没有返回值，或者已将结果直接发送给用户。"
                        )
                        self._transition_state(AgentState.DONE)
                        self.stats.end_time = time.time()
                        await _append_tool_call_result(
                            func_tool_id,
                            "The tool has no return value, or has sent the result directly to the user."
                            + self._build_repeated_tool_call_guidance(
                                func_tool_name, tool_call_streak
                            ),
                        )
                    else:
                        # 不应该出现其他类型
                        logger.warning(
                            f"Tool 返回了不支持的类型: {type(resp)}。",
                        )
                        await _append_tool_call_result(
                            func_tool_id,
                            "*The tool has returned an unsupported type. Please tell the user to check the definition and implementation of this tool.*"
                            + self._build_repeated_tool_call_guidance(
                                func_tool_name, tool_call_streak
                            ),
                        )

                if (
                    retry_prefix is not None
                    and replay_index < len(retry_prefix)
                    and not tool_failed
                ):
                    assert call_state is not None
                    call_state["recovery"]["retries"][-1]["conflict"] = {
                        "index": replay_index,
                        "reason": "Live retry ended before reproducing saved partial output.",
                    }
                    tool_failed = True
                if (
                    call_state is not None
                    and not call_state["protocol_results"]
                    and not tool_failed
                ):
                    await _append_tool_call_result(
                        func_tool_id,
                        "The tool returned no content.",
                        raw_result_saved=True,
                    )
                try:
                    if not reused:
                        await self.agent_hooks.on_tool_end(
                            self.run_context,
                            func_tool,
                            func_tool_args,
                            _final_resp,
                        )
                except Exception as e:
                    logger.error(f"Error in on_tool_end hook: {e}", exc_info=True)
            except Exception as e:
                if isinstance(e, (_ToolExecutionInterrupted, ProcessPersistenceError)):
                    raise
                logger.warning(traceback.format_exc())
                tool_failed = (
                    call_state is not None and call_state["status"] == "started"
                )
                if retry_prefix is not None and call_state is not None:
                    call_state["recovery"]["retries"][-1]["error"] = str(e)
                else:
                    await _append_tool_call_result(
                        func_tool_id,
                        f"error: {e!s}"
                        + self._build_repeated_tool_call_guidance(
                            func_tool_name, tool_call_streak
                        ),
                    )
            finally:
                if invocation_token is not None:
                    current_tool_invocation.reset(invocation_token)
                await self._report_metered_call(usage_receipt)

            if call_state is not None and not reused:
                if tool_failed and retry_prefix is not None:
                    call_state["protocol_results"] = copy.deepcopy(
                        call_state["recovery"]["retries"][-1]["protocol_before"]
                    )
                call_state["status"] = "unknown" if tool_failed else "result_saved"
                await self._save_call_checkpoint(
                    "tool_progress" if tool_failed else "tool_result"
                )
                execution = current_team_execution.get()
                if execution and execution.record_process:
                    for record in call_state["process_records"]:
                        await execution.record_process(
                            record["id"], "tool_result", record["data"]
                        )
                if tool_failed:
                    unresolved.append(func_tool_id)

            if len(tool_call_result_blocks) > tool_result_blocks_start:
                tool_result_content = str(tool_call_result_blocks[-1].content)
                yield _HandleFunctionToolsResult.from_message_chain(
                    MessageChain(
                        type="tool_call_result",
                        chain=[
                            Json(
                                data={
                                    "id": func_tool_id,
                                    "ts": time.time(),
                                    "result": tool_result_content,
                                }
                            )
                        ],
                    )
                )
                logger.info(f"Tool `{func_tool_name}` Result: {tool_result_content}")
                # 记录工具结果追踪
                if _trace:
                    _trace.record(
                        "agent_tool_result",
                        tool_name=func_tool_name,
                        tool_result=tool_result_content[:500]
                        if tool_result_content
                        else None,
                    )

        if unresolved:
            execution = current_team_execution.get()
            error = CheckpointRecoveryError(
                "Tool results require confirmation: " + ", ".join(unresolved)
            )
            if execution:
                execution.recovery_error = error
            raise error

        # 处理函数调用响应
        if tool_call_result_blocks:
            yield _HandleFunctionToolsResult.from_tool_call_result_blocks(
                tool_call_result_blocks
            )

    def _build_tool_requery_context(
        self,
        tool_names: list[str],
        extra_instruction: str | None = None,
    ) -> list[dict[str, T.Any]]:
        """Build contexts for re-querying LLM with param-only tool schemas."""
        contexts: list[dict[str, T.Any]] = []
        for msg in self.run_context.messages:
            if hasattr(msg, "model_dump"):
                contexts.append(msg.model_dump())  # type: ignore[call-arg]
            elif isinstance(msg, dict):
                contexts.append(copy.deepcopy(msg))
        instruction = self.SKILLS_LIKE_REQUERY_INSTRUCTION_TEMPLATE.format(
            tool_names=", ".join(tool_names)
        )
        if extra_instruction:
            instruction = f"{instruction}\n{extra_instruction}"
        contexts.append({"role": "user", "content": instruction})
        return contexts

    @staticmethod
    def _has_meaningful_assistant_reply(llm_resp: LLMResponse) -> bool:
        text = (llm_resp.completion_text or "").strip()
        return bool(text)

    def _build_tool_subset(self, tool_set: ToolSet, tool_names: list[str]) -> ToolSet:
        """Build a subset of tools from the given tool set based on tool names."""
        subset = ToolSet()
        for name in tool_names:
            tool = tool_set.get_tool(name)
            if tool:
                subset.add_tool(tool)
        return subset

    async def _query_tool_parameters(
        self,
        parent: LLMResponse,
        contexts: list[Message] | list[dict],
        tools: ToolSet,
        stage: str,
        previous: LLMResponse | None = None,
    ) -> LLMResponse | None:
        """Resume parameter generation without repeating the original light plan.

        Args:
            parent: Complete original plan that selected the tools.
            contexts: Effective parameter or repair request messages.
            tools: Parameter schemas selected by the original plan.
            stage: Parameter generation or its existing single repair request.
            previous: Complete unsuccessful parameter response before repair.

        Returns:
            Original saved response or a newly persisted complete parameter response.

        Raises:
            ProcessPersistenceError: Saving the request or response failed.
        """
        response = None
        resumed = self._resume_requery
        if resumed is not None and resumed["stage"] != stage:
            return load_call_response(resumed["previous_response"])
        if resumed is not None:
            self._resume_requery = None
            if resumed["response"] is not None:
                response = load_call_response(resumed["response"])
                self._reused_response = True
            else:
                contexts = bind_checkpoint_messages(resumed["input_contexts"])
        else:
            contexts = self._sanitize_contexts_for_provider(contexts)
            await self._save_call_checkpoint(
                "request",
                contexts=contexts,
                stage=stage,
                parent_response=parent,
                previous_response=previous,
            )
        if resumed is None or resumed["response"] is None:
            receipt = await self._begin_metered_call(
                "model",
                (self._call_checkpoint or {}).get("invocation_id") or uuid.uuid4().hex,
                name=self.provider.provider_config.get("id"),
                contexts=contexts,
                tools=tools,
            )
            params = dict(self.req.llm_params or {})
            if receipt:
                params[
                    "max_completion_tokens"
                    if "max_completion_tokens" in params
                    else "max_tokens"
                ] = receipt["max_output_tokens"]
            response = None
            try:
                with model_delivery(receipt):
                    response = await self._await_or_stop(
                        self.provider.text_chat(
                            contexts=contexts,
                            func_tool=tools,
                            model=self.req.model,
                            session_id=self.req.session_id,
                            extra_user_content_parts=self.req.extra_user_content_parts,
                            abort_signal=self._abort_signal,
                            request_max_retries=self.request_max_retries,
                            llm_params=params,
                        )
                    )
            finally:
                await self._report_metered_call(receipt, response)
        if response:
            # Fill an observation commit gap with the original invocation identity.
            await self._record_model_response(response)
        if (
            response
            and response.usage
            and not (resumed and resumed["phase"].startswith("tool"))
        ):
            self.stats.token_usage += response.usage
            self.stats.current_context_tokens = response.usage.input
        return response

    async def _resolve_tool_exec(
        self,
        llm_resp: LLMResponse,
    ) -> tuple[LLMResponse, ToolSet | None]:
        """Used in 'skills_like' tool schema mode to re-query LLM with param-only tool schemas."""
        tool_names = llm_resp.tools_call_name
        if not tool_names:
            return llm_resp, self.req.func_tool
        full_tool_set = self.req.func_tool
        if not isinstance(full_tool_set, ToolSet):
            return llm_resp, self.req.func_tool

        subset = self._build_tool_subset(full_tool_set, tool_names)
        if not subset.tools:
            return llm_resp, full_tool_set

        if isinstance(self._tool_schema_param_set, ToolSet):
            param_subset = self._build_tool_subset(
                self._tool_schema_param_set, tool_names
            )
            if param_subset.tools and tool_names:
                contexts = self._build_tool_requery_context(tool_names)
                parent_response = llm_resp
                requery_resp = await self._query_tool_parameters(
                    parent_response, contexts, param_subset, "parameters"
                )
                if requery_resp:
                    llm_resp = requery_resp
                    self._sanitize_malformed_tool_calls(llm_resp)

                # If the re-query still returns no tool calls, and also does not have a meaningful assistant reply,
                # we consider it as a failure of the LLM to follow the tool-use instruction,
                # and we will retry once with a stronger instruction that explicitly requires the LLM to either call the tool or give an explanation.
                if (
                    not llm_resp.tools_call_name
                    and not self._has_meaningful_assistant_reply(llm_resp)
                ):
                    logger.warning(
                        "skills_like tool re-query returned no tool calls and no explanation; retrying with stronger instruction."
                    )
                    repair_contexts = self._build_tool_requery_context(
                        tool_names,
                        extra_instruction=self.SKILLS_LIKE_REQUERY_REPAIR_INSTRUCTION,
                    )
                    repair_resp = await self._query_tool_parameters(
                        parent_response,
                        repair_contexts,
                        param_subset,
                        "repair",
                        llm_resp,
                    )
                    if repair_resp:
                        llm_resp = repair_resp
                        self._sanitize_malformed_tool_calls(llm_resp)

        return llm_resp, subset

    def done(self) -> bool:
        """检查 Agent 是否已完成工作"""
        return self._state in (AgentState.DONE, AgentState.ERROR)

    def request_stop(self) -> None:
        self._abort_signal.set()

    def _is_stop_requested(self) -> bool:
        return self._abort_signal.is_set()

    def was_aborted(self) -> bool:
        return self._aborted

    def get_final_llm_resp(self) -> LLMResponse | None:
        return self.final_llm_resp

    @property
    def effective_raw_tool_set(self) -> ToolSet | None:
        """Return the full-schema tool set, even in skills_like mode.

        In skills_like mode ``req.func_tool`` is swapped for a light schema set
        without parameter definitions or handlers. Callers that need to inherit
        this runner's executable tool set (e.g. subagent fork mode) should use
        this property instead.

        Returns:
            The raw full-schema ToolSet, or None when not set.
        """
        raw_set = getattr(self, "_skill_like_raw_tool_set", None)
        if (
            getattr(self, "tool_schema_mode", None) == "skills_like"
            and raw_set is not None
        ):
            return raw_set
        req = getattr(self, "req", None)
        return req.func_tool if req else None

    async def _finalize_aborted_step(self) -> AgentResponse:
        logger.info("Agent execution was requested to stop by user.")

        self.run_context.messages.extend(
            [
                Message(
                    role="user",
                    content=[TextPart(text=self.USER_INTERRUPTION_REQUEST)],
                ),
                Message(
                    role="assistant",
                    content=[TextPart(text=self.USER_INTERRUPTION_MESSAGE)],
                ),
            ]
        )

        interrupted_resp = LLMResponse(
            role="assistant",
            completion_text=self.USER_INTERRUPTION_MESSAGE,
        )
        self.final_llm_resp = interrupted_resp
        self._aborted = True
        self._transition_state(AgentState.DONE)
        self.stats.end_time = time.time()

        try:
            await self.agent_hooks.on_agent_done(self.run_context, interrupted_resp)
        except Exception as e:
            logger.error(f"Error in on_agent_done hook: {e}", exc_info=True)

        self._resolve_unconsumed_follow_ups()
        return AgentResponse(
            type="aborted",
            data=AgentResponseData(chain=MessageChain(type="aborted")),
        )

    async def _close_executor(self, executor: T.Any) -> None:
        close_executor = getattr(executor, "aclose", None)
        if close_executor is None:
            return
        with suppress(asyncio.CancelledError, RuntimeError, StopAsyncIteration):
            await close_executor()

    async def _iter_tool_executor_results(
        self,
        executor: T.AsyncGenerator[ToolExecutorResultT, None] | None,
        *,
        saved_results: list[dict] | None = None,
    ) -> T.AsyncGenerator[ToolExecutorResultT, None]:
        """Read live results or rebuild saved results without starting a tool.

        Args:
            executor: Live tool generator, absent when reusing saved results.
            saved_results: Complete original results from a durable call boundary.

        Yields:
            Existing executor result types for the normal materialization path.

        Raises:
            _ToolExecutionInterrupted: Stop was requested during live execution.
        """
        if saved_results is not None:
            for raw in saved_results:
                yield T.cast(ToolExecutorResultT, CallToolResult.model_validate(raw))
            return
        assert executor is not None
        executor = iterate_in_task(executor)

        async def _next_executor_result() -> ToolExecutorResultT:
            return await anext(executor)

        try:
            while True:
                if self._is_stop_requested():
                    raise _ToolExecutionInterrupted(
                        "Tool execution interrupted before reading the next tool result."
                    )

                next_result_task = asyncio.create_task(_next_executor_result())
                abort_task = asyncio.create_task(self._abort_signal.wait())
                try:
                    done, _ = await asyncio.wait(
                        {next_result_task, abort_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if abort_task in done:
                        if not next_result_task.done():
                            next_result_task.cancel()
                            with suppress(asyncio.CancelledError, StopAsyncIteration):
                                await next_result_task
                        raise _ToolExecutionInterrupted(
                            "Tool execution interrupted by a stop request."
                        )

                    try:
                        yield next_result_task.result()
                    except StopAsyncIteration:
                        return
                except asyncio.CancelledError:
                    if not next_result_task.done():
                        next_result_task.cancel()
                    # The reader must finish before its async generator can be closed.
                    await asyncio.gather(next_result_task, return_exceptions=True)
                    raise
                finally:
                    if not abort_task.done():
                        abort_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await abort_task
        finally:
            await executor.aclose()
