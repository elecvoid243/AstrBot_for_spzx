import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.core.exceptions import ContextCapacityError, ToolProtocolIntegrityError
from astrbot.core.provider.modalities import sanitize_contexts_by_modalities

from ..message import Message, bind_checkpoint_messages
from .compaction_region import select_compaction_range
from .compaction_spec import CompactionSpec, resolve_compaction_spec
from .compressor import LLMSummaryCompressor, TruncateByTurnsCompressor
from .config import ContextConfig
from .token_counter import EstimateTokenCounter
from .truncator import ContextTruncator

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider

    from ..tool import ToolSet


@dataclass
class ContextUsageSnapshot:
    """Input usage associated with one successfully completed model request.

    Attributes:
        provider: Provider instance that received the request.
        model: Effective model selected for that delivery.
        messages: Independent serialized copy of the messages actually sent.
        tool_schema: Independent copy of the offered tool definitions.
        input_tokens: Successful request input usage, including cached input.
    """

    provider: "Provider"
    model: str
    messages: list[dict]
    tool_schema: list[dict]
    input_tokens: int = 0


class ContextManager:
    """Context compression manager."""

    def __init__(
        self,
        config: ContextConfig,
    ) -> None:
        """Initialize the context manager.

        There are two strategies to handle context limit reached:
        1. Truncate by turns: remove older messages by turns.
        2. LLM-based compression: use LLM to summarize old messages.

        Args:
            config: The context configuration.
        """
        self.config = config

        self.last_compression: dict | None = None
        self._last_compaction_tokens: int | None = None
        """Report of the compression performed by the latest ``process()`` call.

        ``None`` means that call did not compress. Reset at the start of every
        call: a stale report would make a later caller (the agent runner)
        announce a compression that never happened for that step.
        """

        self.token_counter = config.custom_token_counter or EstimateTokenCounter()
        self.truncator = ContextTruncator()

        if config.custom_compressor:
            self.compressor = config.custom_compressor
        elif config.llm_compress_provider:
            self.compressor = LLMSummaryCompressor(
                provider=config.llm_compress_provider,
                keep_recent_ratio=config.retain_ratio,
                compress_threshold_tokens=config.compress_threshold_tokens,
                token_counter=self.token_counter,
                llm_params=config.llm_params,
            )
        else:
            self.compressor = TruncateByTurnsCompressor(
                truncate_turns=config.truncate_turns,
                compress_threshold_tokens=config.compress_threshold_tokens,
                target_usage_ratio=config.truncate_target_usage_ratio,
                max_tokens=config.max_context_tokens,
                token_counter=self.token_counter,
            )

    async def process(
        self,
        messages: list[Message],
        trusted_token_usage: int = 0,
        func_tool: "ToolSet | None" = None,
        *,
        usage_snapshot: ContextUsageSnapshot | None = None,
        output_token_budget: int = 0,
        check_compression_threshold: bool = True,
        modalities: list[str] | None = None,
        prune_tool_results: Callable[[list[Message]], Awaitable[list[Message]]]
        | None = None,
        protected_messages: list[Message] | None = None,
    ) -> list[Message]:
        """Process the messages.

        Args:
            messages: The original message list.
            trusted_token_usage: Legacy scalar without request provenance. It
                cannot override estimation; use a matching usage_snapshot.
            func_tool: Tool set attached to the LLM summary request so its
                payload prefix matches previous chat requests (prefix cache
                hits), and included in the input estimate for all strategies.
            usage_snapshot: Successful input usage and its immutable request
                prefix, already validated against the effective provider/model.
            output_token_budget: Positive output limit effective for this request.
                Zero preserves the existing ratio-based output headroom.
            check_compression_threshold: Check the normal compression trigger.
                False only rechecks capacity after output admission, avoiding a
                second futile compression of an already processed request.
            modalities: Effective provider capabilities, also applied to any
                replacement history before pairing and counting it.
            prune_tool_results: Optional runner-owned result offloading, invoked
                only after compression or capacity pressure is established.
            protected_messages: Inputs awaiting durable delivery confirmation.

        Returns:
            The processed message list.

        Raises:
            ToolProtocolIntegrityError: Tool history cannot be paired unambiguously.
            ContextCapacityError: The request cannot fit in its known window
                after applying the existing compression strategies.

        Notes:
            Sets ``self.last_compression`` to this call's report (``None`` when
            nothing was compressed); the report never carries over between
            calls.
        """
        self.last_compression = None
        total_tokens = None

        if modalities:
            visible, stats = sanitize_contexts_by_modalities(
                messages, modalities, preserve_internal=True
            )
            if stats.changed:
                messages = bind_checkpoint_messages(visible)
        messages = self.truncator.fix_messages(messages)
        try:
            result = messages

            spec = resolve_compaction_spec(
                context_window=self.config.max_context_tokens,
                output_budget=output_token_budget,
                threshold_ratio=self.config.threshold_ratio,
                headroom_tokens=self.config.headroom_tokens,
                absolute_threshold_tokens=self.config.compress_threshold_tokens,
                retain_ratio=self.config.retain_ratio,
                target_ratio=self.config.target_ratio,
                max_summary_tokens=self.config.max_summary_tokens,
                compaction_retries=self.config.compaction_retries,
                min_yield_ratio=self.config.min_yield_ratio,
            )
            if spec is None:
                if (
                    self.config.max_context_tokens > 0
                    and output_token_budget >= self.config.max_context_tokens
                ):
                    raise ContextCapacityError(
                        f"Context capacity exceeded: {output_token_budget} reserved "
                        f"output tokens cannot fit in the "
                        f"{self.config.max_context_tokens}-token window."
                    )
                return result

            total_tokens = self._count_request_tokens(result, func_tool, usage_snapshot)
            if self._last_compaction_tokens is None and any(
                message.compaction_id for message in result
            ):
                # A restored checkpoint is already recent context; do not
                # immediately re-summarize it until the request grows again.
                self._last_compaction_tokens = total_tokens
            capacity_pressure = (
                spec.context_window > 0
                and total_tokens + output_token_budget > spec.context_window
            )
            threshold_pressure = (
                check_compression_threshold and total_tokens > spec.threshold_tokens
            )
            if capacity_pressure or threshold_pressure:
                if (
                    not capacity_pressure
                    and self._last_compaction_tokens is not None
                    and total_tokens
                    < self._last_compaction_tokens + spec.min_growth_tokens
                ):
                    return result
                result = await self._run_compression(
                    spec,
                    result,
                    total_tokens,
                    func_tool,
                    output_token_budget,
                    modalities,
                    prune_tool_results,
                    protected_messages,
                    reason="capacity" if capacity_pressure else "threshold",
                )
                usage_snapshot = None

            total_tokens = self._count_request_tokens(result, func_tool, usage_snapshot)
            if (
                spec.context_window > 0
                and total_tokens + output_token_budget > spec.context_window
            ):
                raise ContextCapacityError(
                    f"Context capacity exceeded: {total_tokens} input tokens "
                    f"plus {output_token_budget} reserved output tokens "
                    f"cannot fit in the {spec.context_window}-token window "
                    "after compression. Reduce the current input, tool definitions, or output limit."
                )

            return result
        except Exception as e:
            from astrbot.core.agent_team_execution import ProcessPersistenceError

            if isinstance(
                e,
                (
                    ProcessPersistenceError,
                    ToolProtocolIntegrityError,
                    ContextCapacityError,
                ),
            ):
                raise
            logger.error(f"Error during context processing: {e}", exc_info=True)
            if (
                self.config.max_context_tokens > 0
                and total_tokens is not None
                and total_tokens + output_token_budget > self.config.max_context_tokens
            ):
                raise ContextCapacityError(
                    f"Context capacity exceeded: {total_tokens} input tokens "
                    f"plus {output_token_budget} reserved output tokens cannot fit "
                    f"in the {self.config.max_context_tokens}-token window, "
                    "and context compression failed."
                ) from e
            return messages

    def _count_request_tokens(
        self,
        messages: list[Message],
        func_tool: "ToolSet | None",
        usage_snapshot: ContextUsageSnapshot | None = None,
    ) -> int:
        """Estimate the visible request, reusing usage only for an unchanged prefix.

        Args:
            messages: Effective model-visible messages.
            func_tool: Tools actually offered to the model.
            usage_snapshot: Successful request whose input usage covers its prefix.

        Returns:
            Estimated input tokens, including tool definitions exactly once.
        """
        tool_schema = func_tool.openai_schema() if func_tool else []
        if (
            usage_snapshot is not None
            and usage_snapshot.tool_schema == tool_schema
            and len(messages) >= len(usage_snapshot.messages)
            and [
                message.model_dump(mode="json")
                for message in messages[: len(usage_snapshot.messages)]
            ]
            == usage_snapshot.messages
        ):
            return usage_snapshot.input_tokens + self.token_counter.count_tokens(
                messages[len(usage_snapshot.messages) :]
            )
        tokens = self.token_counter.count_tokens(messages)
        if tool_schema:
            tokens += self.token_counter.count_tokens(
                [
                    Message(
                        role="system",
                        content=json.dumps(tool_schema, ensure_ascii=False),
                    )
                ]
            )
        return tokens

    async def _run_compression(
        self,
        spec: CompactionSpec,
        messages: list[Message],
        prev_tokens: int,
        func_tool: "ToolSet | None" = None,
        output_token_budget: int = 0,
        modalities: list[str] | None = None,
        prune_tool_results: Callable[[list[Message]], Awaitable[list[Message]]]
        | None = None,
        protected_messages: list[Message] | None = None,
        reason: str = "threshold",
    ) -> list[Message]:
        """Compress or truncate the messages toward the resolved target.

        Args:
            spec: Resolved compaction budgets for this request.
            messages: The original message list.
            prev_tokens: Token count before compression.
            func_tool: Tool set attached to the summary request.
            output_token_budget: Output tokens reserved in the model window.
            modalities: Effective provider capabilities for replacement history.
            prune_tool_results: Optional runner-owned tool-result offloading.
            protected_messages: Inputs awaiting durable delivery confirmation.
            reason: Trigger label recorded in the compression report.

        Returns:
            The compressed/truncated message list.

        Notes:
            Records the outcome in ``self.last_compression`` so callers can
            surface it to the user (webchat shows a transient notice).
        """
        logger.debug("Compress triggered, starting compression...")

        if prune_tool_results is not None:
            replacement = await prune_tool_results(messages)
            if replacement != messages:
                messages = replacement
                tokens = self._count_request_tokens(messages, func_tool)
                if tokens <= spec.threshold_tokens and (
                    spec.context_window <= 0
                    or tokens + output_token_budget <= spec.context_window
                ):
                    self.last_compression = {
                        "strategy": "tool_result_prune",
                        "tokens_before": prev_tokens,
                        "tokens_after": tokens,
                        "shadowed_tokens": 0,
                        "checkpoint_tokens": 0,
                        "yield_ratio": 0.0,
                        "passes": 0,
                        "reason": reason,
                        "insufficient": False,
                        "threshold_tokens": spec.threshold_tokens,
                        "retain_tokens": spec.retain_tokens,
                        "target_tokens": spec.target_tokens,
                        "context_window": spec.context_window,
                        "output_budget": spec.output_budget,
                    }
                    self._last_compaction_tokens = tokens
                    return messages

        passes = 0
        attempted = False
        if isinstance(self.compressor, LLMSummaryCompressor):
            # Capacity pressure cannot be fixed by summarizing the same range
            # twice; hard-drop handles the residual instead.
            max_passes = 1 if reason == "capacity" else 1 + spec.compaction_retries
            for _ in range(max_passes):
                selected = select_compaction_range(
                    messages,
                    retain_tokens=spec.retain_tokens,
                    token_counter=self.token_counter,
                    protected_messages=protected_messages,
                )
                if selected is None:
                    break
                attempted = True
                before = messages
                try:
                    messages = await self.compressor(
                        messages,
                        func_tool=func_tool,
                        spec=spec,
                        selected_range=selected,
                        protected_messages=protected_messages,
                    )
                except TypeError:
                    messages = await self.compressor(messages, func_tool=func_tool)
                messages = self.truncator.fix_messages(messages)
                if messages == before:
                    break
                passes += 1
                tokens = self._count_request_tokens(messages, func_tool)
                if spec.target_tokens and tokens <= spec.target_tokens:
                    break
                if tokens <= spec.threshold_tokens and (
                    spec.context_window <= 0
                    or tokens + output_token_budget <= spec.context_window
                ):
                    break
        else:
            attempted = True
            before = messages
            try:
                messages = await self.compressor(messages, func_tool=func_tool)
            except TypeError:
                messages = await self.compressor(messages)
            messages = self.truncator.fix_messages(messages)
            if messages != before:
                passes = 1

        tokens = self._count_request_tokens(messages, func_tool)
        dropped_rounds = 0
        if tokens > spec.threshold_tokens or (
            spec.context_window > 0
            and tokens + output_token_budget > spec.context_window
        ):
            logger.info(
                "Context still exceeds the compression target, dropping the oldest rounds..."
            )
            while tokens > spec.threshold_tokens or (
                spec.context_window > 0
                and tokens + output_token_budget > spec.context_window
            ):
                replacement = self.truncator.drop_oldest_round(
                    messages, protected_messages
                )
                if replacement is messages:
                    break
                messages = replacement
                dropped_rounds += 1
                messages = self.truncator.fix_messages(messages)
                tokens = self._count_request_tokens(messages, func_tool)
            logger.info(f"Dropped {dropped_rounds} oldest round(s).")
            if tokens > spec.threshold_tokens:
                logger.warning(
                    "Protected context still exceeds the compression trigger."
                )

        insufficient = bool(
            (spec.target_tokens and tokens > spec.target_tokens)
            or tokens > spec.threshold_tokens
        )
        if attempted or dropped_rounds:
            shadowed = getattr(self.compressor, "last_shadowed_tokens", 0)
            if not isinstance(shadowed, int):
                shadowed = 0
            checkpoint = getattr(self.compressor, "last_checkpoint_tokens", 0)
            if not isinstance(checkpoint, int):
                checkpoint = 0
            strategy = getattr(self.compressor, "strategy", "custom")
            if not isinstance(strategy, str):
                strategy = "custom"
            yield_ratio = (1 - checkpoint / shadowed) if shadowed else 0.0
            self.last_compression = {
                "strategy": ("truncate_by_turns" if dropped_rounds else strategy),
                "tokens_before": prev_tokens,
                "tokens_after": tokens,
                "shadowed_tokens": shadowed,
                "checkpoint_tokens": checkpoint,
                "yield_ratio": round(max(0.0, min(1.0, yield_ratio)), 4),
                "passes": passes,
                "reason": reason,
                "insufficient": insufficient,
                "threshold_tokens": spec.threshold_tokens,
                "retain_tokens": spec.retain_tokens,
                "target_tokens": spec.target_tokens,
                "context_window": spec.context_window,
                "output_budget": spec.output_budget,
            }
            self._last_compaction_tokens = tokens
        return messages
