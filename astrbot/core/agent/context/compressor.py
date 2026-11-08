import copy
import json
import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from astrbot.core.exceptions import ContextCapacityError, ToolProtocolIntegrityError

from ...provider.modalities import (
    log_context_sanitize_stats,
    sanitize_contexts_by_modalities,
)
from ..message import Message, bind_checkpoint_messages
from .compaction_region import CompactionRange
from .compaction_spec import CompactionSpec
from .token_counter import EstimateTokenCounter, TokenCounter

if TYPE_CHECKING:
    from astrbot import logger
else:
    try:
        from astrbot import logger
    except ImportError:
        import logging

        logger = logging.getLogger("astrbot")

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider

    from ..tool import ToolSet

from ..context.truncator import ContextTruncator

COMPACTION_INSTRUCTION = """You are now acting as a compaction engine for this AI coding assistant. Condense the conversation ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential context.

Output EXACTLY the Markdown structure below: keep every section, in order. Use terse bullets, not prose paragraphs. Write "(none)" for an empty section — never drop a section.

## Primary Request and Intent
- [the user's original and evolving goals; quote verbatim where the exact wording matters]

## Key Technical Concepts
- [technologies, frameworks, patterns, and conventions in play]

## Files and Code
- [exact path: why it matters, key changes or snippets]

## Errors and Fixes
- [error: how it was resolved, plus any related user feedback]

## Pending Jobs
- [explicitly requested work not yet completed]

## Current Work
- [precisely what was in progress at this checkpoint]

## Next Step
- [the single next action, directly in line with the most recent request, or "(none)"]

## Critical Context
- [decisions and their rationale, constraints, user preferences, open questions, data needed to continue]

Rules:
- Write concise English engineering prose. Preserve exact file paths, commands, error strings, identifiers, numeric values, function signatures, and syntax fragments.
- Capture user feedback and explicit instructions faithfully, especially corrections.
- Do NOT mention this summarization request or that the context was compacted.
- Output only the checkpoint text: do not call any tool or take any other action.
- If the conversation already contains a <compacted-summary> block, it is a PRIOR checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer information into a single consolidated summary under the same structure.


CHECKPOINT_PREAMBLE = (
    "This is an automatically generated checkpoint condensing an earlier span "
    "of the conversation to free up context. Treat the captured context as "
    "established background and build on it without restating it. Continue "
    "the task directly from the messages that follow, without acknowledging "
    "this checkpoint."
)
"""

CHECKPOINT_PREAMBLE = (
    "This is an automatically generated checkpoint condensing an earlier span "
    "of the conversation to free up context. Treat the captured context as "
    "established background and build on it without restating it. Continue "
    "the task directly from the messages that follow, without acknowledging "
    "this checkpoint."
)


@runtime_checkable
class ContextCompressor(Protocol):
    """
    Protocol for context compressors.
    Provides an interface for compressing message lists.

    Attributes:
        strategy: Optional machine-readable strategy name (``"llm_compress"``
            / ``"truncate_by_turns"``). ``ContextManager`` reads it to label
            the compression report it hands to the UI; compressors that do not
            define it are reported as ``"custom"``.
    """

    def should_compress(
        self, messages: list[Message], current_tokens: int, max_tokens: int
    ) -> bool:
        """Check if compression is needed.

        Args:
            messages: The message list to evaluate.
            current_tokens: The current token count.
            max_tokens: The maximum allowed tokens for the model.

        Returns:
            True if compression is needed, False otherwise.
        """
        ...

    async def __call__(
        self,
        messages: list[Message],
        func_tool: "ToolSet | None" = None,
    ) -> list[Message]:
        """Compress the message list.

        Args:
            messages: The original message list.
            func_tool: Optional tool set attached to the summary request so
                its payload prefix (tools + system + history) stays identical
                to previous chat requests (provider prefix cache hits). The
                summary is a one-shot call with no execution loop, so the
                schema is visible but tools cannot actually run.

        Returns:
            The compressed message list.
        """
        ...


class TruncateByTurnsCompressor:
    """Truncate by turns compressor implementation.
    Truncates the message list by removing older turns.
    """

    strategy = "truncate_by_turns"
    """Strategy label for the compression report consumed by the frontend."""

    def __init__(
        self,
        truncate_turns: int = 1,
        compression_threshold: float = 0.82,
        compress_threshold_tokens: int = 0,
        target_usage_ratio: float = 0.4,
        max_tokens: int = 0,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize the truncate by turns compressor.

        Args:
            truncate_turns: The number of turns to remove when truncating (default: 1).
            compression_threshold: The compression trigger threshold (default: 0.82).
            compress_threshold_tokens: Absolute token threshold that triggers
                compression. When ``> 0``, compression fires once the estimated
                context tokens exceed this value and ``compression_threshold``
                is ignored. ``<= 0`` falls back to the ratio.
            target_usage_ratio: Target usage ratio of ``max_tokens`` after
                truncation (default: 0.4). When ``max_tokens`` is provided, the
                compressor drops the oldest turns until the remaining estimated
                tokens fit this budget instead of a fixed turn count.
            max_tokens: The maximum allowed tokens for the model. ``<= 0``
                disables token-budget truncation and falls back to dropping
                ``truncate_turns`` turns at a time.
            token_counter: Token counter used to estimate the budget.
        """
        self.truncate_turns = truncate_turns
        self.compression_threshold = compression_threshold
        self.compress_threshold_tokens = compress_threshold_tokens
        self.target_usage_ratio = max(0.0, min(float(target_usage_ratio), 1.0))
        self.max_tokens = max_tokens
        self.token_counter = token_counter

    def should_compress(
        self, messages: list[Message], current_tokens: int, max_tokens: int
    ) -> bool:
        """Check if compression is needed.

        Args:
            messages: The message list to evaluate.
            current_tokens: The current token count.
            max_tokens: The maximum allowed tokens.

        Returns:
            True if compression is needed, False otherwise.

        Notes:
            A positive ``compress_threshold_tokens`` takes precedence over the
            ratio-based trigger.
        """
        if current_tokens <= 0:
            return False
        if self.compress_threshold_tokens > 0:
            return current_tokens > self.compress_threshold_tokens
        if max_tokens <= 0:
            return False
        usage_rate = current_tokens / max_tokens
        return usage_rate > self.compression_threshold

    async def __call__(
        self,
        messages: list[Message],
        func_tool: "ToolSet | None" = None,
    ) -> list[Message]:
        """Truncate the message list by dropping turns.

        Args:
            messages: The message list to truncate.
            func_tool: Ignored; truncation involves no LLM request.

        Returns:
            The truncated message list.
        """
        truncator = ContextTruncator()
        if (
            self.max_tokens > 0
            and self.target_usage_ratio > 0
            and self.token_counter is not None
        ):
            # Token-budget truncation: drop the oldest turns until the
            # remaining tokens fit max_tokens * target_usage_ratio, so the
            # next requests have headroom and can reuse the prefix cache.
            budget_tokens = int(self.max_tokens * self.target_usage_ratio)
            return truncator.truncate_by_token_budget(
                messages,
                budget_tokens=budget_tokens,
                token_counter=self.token_counter,
                min_drop_turns=self.truncate_turns,
            )
        truncated_messages = truncator.truncate_by_dropping_oldest_turns(
            messages,
            drop_turns=self.truncate_turns,
        )
        return truncated_messages


def _extract_system_messages(messages: list[Message]) -> list[Message]:
    """Return the leading system messages from a message list."""
    result = []
    for msg in messages:
        if msg.role == "system":
            result.append(msg)
        else:
            break
    return result


class LLMSummaryCompressor:
    """LLM-based summary compressor.
    Uses LLM to summarize old conversation history while keeping a recent token
    budget as exact context.
    """

    strategy = "llm_compress"
    """Strategy label for the compression report consumed by the frontend."""

    def __init__(
        self,
        provider: "Provider",
        keep_recent_ratio: float = 0.15,
        compression_threshold: float = 0.82,
        compress_threshold_tokens: int = 0,
        token_counter: TokenCounter | None = None,
        llm_params: dict | None = None,
    ) -> None:
        """Initialize the LLM summary compressor.

        Args:
            provider: The LLM provider instance.
            keep_recent_ratio: Ratio of current context tokens to keep as recent
                exact context. Clamped to 0-0.3.
            compression_threshold: The compression trigger threshold (default: 0.82).
            compress_threshold_tokens: Absolute token threshold that triggers
                compression. When ``> 0``, compression fires once the estimated
                context tokens exceed this value and ``compression_threshold``
                is ignored. ``<= 0`` falls back to the ratio.
            token_counter: Custom token counter. Defaults to EstimateTokenCounter.
            llm_params: Per-request LLM parameters (e.g. thinking_effort) sent
                with the summary request so provider fields derived from them
                (such as reasoning_effort) stay identical to the chat requests
                the prefix cache was built from.
        """
        self.provider = provider
        self.keep_recent_ratio = min(max(float(keep_recent_ratio), 0.0), 0.3)
        self.compression_threshold = compression_threshold
        self.compress_threshold_tokens = compress_threshold_tokens
        self.token_counter = token_counter or EstimateTokenCounter()
        self.llm_params = llm_params or {}

        self.instruction_text = COMPACTION_INSTRUCTION
        self.last_shadowed_tokens: int | None = None
        self.last_checkpoint_tokens: int | None = None
        self.last_summary_tokens: int | None = None

    def should_compress(
        self, messages: list[Message], current_tokens: int, max_tokens: int
    ) -> bool:
        """Check if compression is needed.

        Args:
            messages: The message list to evaluate.
            current_tokens: The current token count.
            max_tokens: The maximum allowed tokens.

        Returns:
            True if compression is needed, False otherwise.

        Notes:
            A positive ``compress_threshold_tokens`` takes precedence over the
            ratio-based trigger.
        """
        if current_tokens <= 0:
            return False
        if self.compress_threshold_tokens > 0:
            return current_tokens > self.compress_threshold_tokens
        if max_tokens <= 0:
            return False
        usage_rate = current_tokens / max_tokens
        return usage_rate > self.compression_threshold

    def _split_recent_rounds_by_token_ratio(
        self,
        rounds: list[list[Message]],
        total_tokens: int,
    ) -> tuple[list[list[Message]], list[list[Message]]]:
        """Split rounds into summarised history and exact recent context.

        The token budget is computed from the current context token count and
        `keep_recent_ratio`, then floored by `int(...)`. Mapping that budget to
        rounds is round-granular: a positive ratio always preserves the latest
        whole round, even if that round itself exceeds the budget. Earlier
        rounds are added only while the accumulated recent rounds stay within
        the budget. No round is split.
        """
        if not rounds or self.keep_recent_ratio <= 0 or total_tokens <= 0:
            return rounds, []

        budget = max(1, int(total_tokens * self.keep_recent_ratio))
        used = 0
        recent_start = len(rounds)

        for idx in range(len(rounds) - 1, -1, -1):
            round_tokens = self.token_counter.count_tokens(rounds[idx])
            if recent_start < len(rounds) and used + round_tokens > budget:
                break
            used += round_tokens
            recent_start = idx

        return rounds[:recent_start], rounds[recent_start:]

    async def _request_summary(
        self,
        summary_contexts: list[Message],
        func_tool: "ToolSet | None",
        *,
        max_summary_tokens: int | None = None,
    ) -> str | None:
        """Generate one summary from the supplied prefix.

        Args:
            summary_contexts: Prefix replayed to the summary model.
            func_tool: Tool schema kept for prefix-cache parity.
            max_summary_tokens: Optional cap for the summary output.

        Returns:
            The summary text, or None when generation failed or was truncated.

        Raises:
            ProcessPersistenceError: Durable team execution failed.
            ToolProtocolIntegrityError: History cannot be paired.
        """
        from astrbot.core.agent_team_execution import (
            ProcessPersistenceError,
            current_team_execution,
            model_delivery,
            resolve_model_output_limit,
        )

        if summary_contexts[-1].role != "assistant":
            summary_contexts.append(
                Message(
                    role="assistant",
                    content="Acknowledged.",
                )
            )
        summary_contexts.append(Message(role="user", content=self.instruction_text))
        sanitized_summary_contexts, sanitize_stats = sanitize_contexts_by_modalities(
            summary_contexts,
            self.provider.provider_config.get("modalities", None),
        )
        log_context_sanitize_stats(sanitize_stats)
        sanitized_summary_contexts = [
            message.model_dump()
            for message in ContextTruncator().fix_messages(
                bind_checkpoint_messages(sanitized_summary_contexts)
            )
        ]
        modalities = self.provider.provider_config.get("modalities")
        summary_tools = func_tool
        if isinstance(modalities, list) and modalities and "tool_use" not in modalities:
            summary_tools = None

        # Generate summary. A single attempt is enough: on failure the caller
        # falls back to truncation, so retrying (e.g. on an exhausted quota)
        # would only hold up the reply.
        summary_content = ""
        try:
            for schemas in (
                [summary_tools, None] if summary_tools is not None else [None]
            ):
                execution = current_team_execution.get()
                params = dict(self.llm_params or {})
                if max_summary_tokens is not None:
                    key = (
                        "max_completion_tokens"
                        if "max_completion_tokens" in params
                        else "max_tokens"
                    )
                    params[key] = min(
                        int(params.get(key) or max_summary_tokens),
                        max_summary_tokens,
                    )
                receipt = None
                input_tokens = self.token_counter.count_tokens(
                    bind_checkpoint_messages(sanitized_summary_contexts)
                )
                if schemas:
                    input_tokens += self.token_counter.count_tokens(
                        [
                            Message(
                                role="system",
                                content=json.dumps(
                                    schemas.openai_schema(), ensure_ascii=False
                                ),
                            )
                        ]
                    )
                window = self.provider.provider_config.get("max_context_tokens", 0)
                if window > 0 and input_tokens > window:
                    raise ContextCapacityError(
                        f"Summary context capacity exceeded: {input_tokens} input "
                        f"tokens cannot fit in the {window}-token window."
                    )
                if execution and execution.begin_usage:
                    receipt = await execution.begin_usage(
                        {
                            "receipt_id": uuid.uuid4().hex,
                            "invocation_id": uuid.uuid4().hex,
                            "kind": "model",
                            "name": self.provider.provider_config.get("id"),
                            "input_tokens": input_tokens,
                            "max_output_tokens": params.get("max_completion_tokens")
                            or params.get("max_tokens"),
                        }
                    )
                response = None
                try:
                    default_limit = getattr(
                        self.provider, "DEFAULT_MAX_OUTPUT_TOKENS", 0
                    )
                    if get_default := getattr(
                        self.provider, "get_default_output_token_limit", None
                    ):
                        default_limit = get_default()
                    output_budget = resolve_model_output_limit(
                        params,
                        self.provider.provider_config.get("custom_extra_body"),
                        default=default_limit,
                        admitted_limit=receipt["max_output_tokens"]
                        if receipt
                        else None,
                    )
                    if receipt:
                        params[
                            "max_completion_tokens"
                            if "max_completion_tokens" in params
                            else "max_tokens"
                        ] = output_budget
                    explicit_output = (
                        isinstance(output_budget, int) and output_budget > 0
                    )
                    input_limit = (
                        window - output_budget
                        if explicit_output
                        else int(window * self.compression_threshold)
                    )
                    if window > 0 and input_tokens > input_limit:
                        raise ContextCapacityError(
                            f"Summary context capacity exceeded: {input_tokens} input "
                            f"tokens cannot fit with its output budget in the {window}-token window."
                        )
                    with model_delivery(receipt, output_limit=output_budget):
                        if schemas is None and func_tool is not None:
                            # Tool-free recovery omits the original schema argument.
                            response = await self.provider.text_chat(
                                contexts=sanitized_summary_contexts,
                                llm_params=params or None,
                                request_max_retries=1,
                            )
                        else:
                            response = await self.provider.text_chat(
                                contexts=sanitized_summary_contexts,
                                func_tool=schemas,
                                llm_params=params or None,
                                request_max_retries=1,
                            )
                finally:
                    if receipt and execution and execution.report_usage:
                        report = (
                            {
                                "input_tokens": response.usage.input,
                                "output_tokens": response.usage.output,
                            }
                            if response and response.usage
                            else {
                                "estimated_tokens": input_tokens
                                + (len(response.completion_text) + 3) // 4
                            }
                            if response and response.role != "err"
                            else {}
                        )
                        await execution.report_usage(receipt["receipt_id"], report)
                summary_content = (
                    (response.completion_text or "").strip() if response else ""
                )
                if summary_content and self._summary_was_truncated(response):
                    logger.warning(
                        "LLM context compression was truncated at the summary "
                        "output cap; retaining history."
                    )
                    return None
                if summary_content:
                    break
        except Exception as e:
            if isinstance(e, (ProcessPersistenceError, ToolProtocolIntegrityError)):
                raise
            logger.error(f"Failed to generate summary: {e}")
            return None

        if not summary_content:
            logger.warning("LLM context compression returned an empty summary.")
            return None
        return summary_content

    async def _summarize_range(
        self,
        messages: list[Message],
        func_tool: "ToolSet | None",
        *,
        spec: CompactionSpec,
        selected_range: CompactionRange,
    ) -> list[Message]:
        """Summarize one selected range into a single checkpoint."""
        input_snapshot = copy.deepcopy(messages)
        summary_content = await self._request_summary(
            list(messages[: selected_range.end]),
            func_tool,
            max_summary_tokens=spec.max_summary_tokens,
        )
        if summary_content is None:
            return messages
        if messages != input_snapshot:
            logger.warning(
                "Summary input changed while generation was running; retaining current history."
            )
            return messages

        last_user = -1
        for position, message in enumerate(messages):
            if message.role == "user" and not message.compaction_id:
                last_user = position
        role = (
            "assistant"
            if last_user >= 0 and selected_range.start > last_user
            else "user"
        )
        checkpoint = Message(
            role=role,
            content=(
                f"{CHECKPOINT_PREAMBLE}\n\n<compacted-summary>\n"
                f"{summary_content}\n</compacted-summary>"
            ),
            _compaction_id=uuid.uuid4().hex,
        )
        self.last_shadowed_tokens = selected_range.shadowed_tokens
        self.last_checkpoint_tokens = self.token_counter.count_tokens([checkpoint])
        self.last_summary_tokens = self.token_counter.count_tokens(
            [Message(role="assistant", content=summary_content)]
        )
        if self.last_checkpoint_tokens >= selected_range.shadowed_tokens * (
            1 - spec.min_yield_ratio
        ):
            logger.warning(
                "Compaction checkpoint did not meet the minimum yield; retaining history."
            )
            return messages
        result = (
            messages[: selected_range.start]
            + [checkpoint]
            + messages[selected_range.end :]
        )
        if self.token_counter.count_tokens(result) >= self.token_counter.count_tokens(
            messages
        ):
            logger.warning(
                "Summary did not reduce the selected context; retaining its input."
            )
            return messages
        return result

    @staticmethod
    def _summary_was_truncated(response: object) -> bool:
        """Return True when the provider stopped at the summary output cap."""
        raw = getattr(response, "raw_completion", None)
        if raw is None:
            return False
        choices = getattr(raw, "choices", None)
        if choices:
            return getattr(choices[0], "finish_reason", None) in {
                "length",
                "max_tokens",
            }
        stop_reason = getattr(raw, "stop_reason", None)
        if stop_reason is not None:
            return stop_reason == "max_tokens"
        candidates = getattr(raw, "candidates", None)
        if candidates:
            return (
                "MAX_TOKENS"
                in str(getattr(candidates[0], "finish_reason", None)).upper()
            )
        return False

    async def __call__(
        self,
        messages: list[Message],
        func_tool: "ToolSet | None" = None,
        *,
        spec: CompactionSpec | None = None,
        selected_range: CompactionRange | None = None,
        compact_current_round: bool = False,
        protected_messages: list[Message] | None = None,
    ) -> list[Message]:
        """Use LLM to generate a summary of the conversation history.

        Uses round-based splitting to preserve user-assistant turn boundaries.
        On LLM failure, returns the paired input view so the caller can fall
        back to truncation.

        Args:
            messages: The original message list.
            func_tool: Tool set attached to the summary request so the payload
                prefix (tools + system + history) matches previous chat
                requests byte-for-byte (provider prefix cache hits). The
                summary is a one-shot call with no tool-execution loop, so
                the schema is visible but tools cannot run; if the model
                still answers with a tool call (empty text), the request is
                retried once without tools.
            compact_current_round: Select earlier completed batches in the
                current round after ordinary history compaction made progress.
            protected_messages: Inputs awaiting durable delivery confirmation.

        Returns:
            Replacement history, or the paired input view when summarization
            fails or its request cannot fit in the summary model window.

        Raises:
            ToolProtocolIntegrityError: History cannot be paired unambiguously.
        """

        from .round_utils import split_into_rounds

        if spec is not None and selected_range is not None:
            return await self._summarize_range(
                messages, func_tool, spec=spec, selected_range=selected_range
            )

        # Shared by automatic compression and direct kernel-compressor callers.
        # Repair only the model view; execution records remain authoritative.
        fixed_messages = ContextTruncator().fix_messages(messages)
        if fixed_messages != messages:
            messages = fixed_messages
        rounds = split_into_rounds(messages)
        message_rounds = [
            [seg for seg in rnd if isinstance(seg, Message)] for rnd in rounds
        ]
        total_tokens = self.token_counter.count_tokens(messages)
        input_snapshot = copy.deepcopy(messages)
        old_rounds, recent_rounds = self._split_recent_rounds_by_token_ratio(
            message_rounds,
            total_tokens,
        )

        for index, group in enumerate(message_rounds):
            if (
                any(msg.role == "system" for msg in group)
                and any(msg.role != "system" for msg in group)
            ) or any(msg in (protected_messages or []) for msg in group):
                old_rounds = old_rounds[:index]
                recent_rounds = message_rounds[len(old_rounds) :]
                break

        # A tool continuation still belongs to the latest real user request.
        current_round = next(
            (
                group
                for group in reversed(message_rounds)
                if any(msg.role == "user" and not msg.compaction_id for msg in group)
            ),
            None,
        )
        if current_round is not None and any(
            group is current_round for group in old_rounds
        ):
            old_rounds = [group for group in old_rounds if group is not current_round]
            recent_rounds = [current_round, *recent_rounds]

        partial_start = None
        partial_end = None
        summary_contexts = [msg for rnd in old_rounds for msg in rnd]
        if compact_current_round or not any(
            msg.role != "system" for msg in summary_contexts
        ):
            user_index = next(
                (
                    index
                    for index in range(len(messages) - 1, -1, -1)
                    if messages[index].role == "user"
                    and not messages[index].compaction_id
                ),
                None,
            )
            if user_index is None:
                return messages
            # Protocol repair supplies missing results; their internal marker
            # must not be interpreted as evidence of completed execution.
            units = []
            index = user_index + 1
            protected_end = index
            latest_batch = None
            while index < len(messages):
                start = index
                message = messages[index]
                index += 1
                if message.role == "assistant" and message.tool_calls:
                    latest_batch = start
                    while index < len(messages) and messages[index].role == "tool":
                        index += 1
                group = messages[start:index]
                if any(
                    msg.role == "system" or msg.tool_result_unknown for msg in group
                ):
                    protected_end = index
                units.append((start, index))
            partial_start = max(user_index + 1, protected_end)
            if latest_batch is None:
                partial_end = (
                    len(messages) if messages[-1].role == "assistant" else partial_start
                )
            else:
                budget = max(1, int(total_tokens * self.keep_recent_ratio))
                kept = 0
                tail_start = len(messages)
                for start, end in reversed(units):
                    cost = self.token_counter.count_tokens(messages[start:end])
                    if (
                        start < latest_batch
                        and tail_start < len(messages)
                        and kept + cost > budget
                    ):
                        break
                    kept += cost
                    tail_start = start
                partial_end = min(tail_start, latest_batch)
            if partial_end <= partial_start:
                return messages
            # Replay the exact prefix for cache reuse; replace only the chosen
            # earlier execution steps, leaving the request and recent tail exact.
            summary_contexts = list(messages[:partial_end])

        summary_content = await self._request_summary(summary_contexts, func_tool)
        if summary_content is None:
            return messages

        if messages != input_snapshot:
            logger.warning(
                "Summary input changed while generation was running; retaining current history."
            )
            return messages

        compaction_id = uuid.uuid4().hex
        if partial_start is not None and partial_end is not None:
            result = (
                messages[:partial_start]
                + [
                    Message(
                        role="assistant",
                        content=(
                            "Earlier execution checkpoint (continue the current request):\n"
                            f"<compacted-summary>\n{summary_content}\n</compacted-summary>"
                        ),
                        _compaction_id=compaction_id,
                    )
                ]
                + messages[partial_end:]
            )
        else:
            result = (
                _extract_system_messages(messages)
                + [
                    Message(
                        role="user",
                        content=(
                            "Our previous history conversation summary:\n"
                            f"<compacted-summary>\n{summary_content}\n</compacted-summary>"
                        ),
                        _compaction_id=compaction_id,
                    ),
                    Message(
                        role="assistant",
                        content="Acknowledged the summary of our previous conversation history.",
                        _compaction_id=compaction_id,
                    ),
                ]
                + [msg for group in recent_rounds for msg in group]
            )
        if self.token_counter.count_tokens(result) >= self.token_counter.count_tokens(
            messages
        ):
            logger.warning(
                "Summary did not reduce the selected context; retaining its input."
            )
            return messages
        return result
