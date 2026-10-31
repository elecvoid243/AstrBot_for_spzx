import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.core.exceptions import ContextCapacityError, ToolProtocolIntegrityError
from astrbot.core.provider.modalities import sanitize_contexts_by_modalities

from ..message import Message, bind_checkpoint_messages
from .compressor import LLMSummaryCompressor, TruncateByTurnsCompressor
from .config import ContextConfig
from .round_utils import split_into_rounds
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
                keep_recent_ratio=config.llm_compress_keep_recent_ratio,
                instruction_text=config.llm_compress_instruction,
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
            visible, stats = sanitize_contexts_by_modalities(messages, modalities)
            if stats.changed:
                messages = bind_checkpoint_messages(visible)
        messages = self.truncator.fix_messages(messages)
        try:
            result = messages

            # Enforce the existing round limit before validating a usage prefix.
            if self.config.enforce_max_turns != -1:
                result = self.truncator.truncate_by_turns(
                    result,
                    keep_most_recent_turns=self.config.enforce_max_turns,
                    drop_turns=self.config.truncate_turns,
                )

            # Apply token-based compression to the complete visible request.
            if (
                self.config.max_context_tokens > 0
                or self.config.compress_threshold_tokens > 0
            ):
                total_tokens = self._count_request_tokens(
                    result, func_tool, usage_snapshot
                )

                if (
                    check_compression_threshold
                    and self.compressor.should_compress(
                        result, total_tokens, self.config.max_context_tokens
                    )
                ) or (
                    self.config.max_context_tokens > 0
                    and total_tokens + output_token_budget
                    > self.config.max_context_tokens
                ):
                    result = await self._run_compression(
                        result, total_tokens, func_tool, output_token_budget, modalities
                    )
                    usage_snapshot = None

                total_tokens = self._count_request_tokens(
                    result, func_tool, usage_snapshot
                )
                if (
                    self.config.max_context_tokens > 0
                    and total_tokens + output_token_budget
                    > self.config.max_context_tokens
                ):
                    raise ContextCapacityError(
                        f"Context capacity exceeded: {total_tokens} input tokens "
                        f"plus {output_token_budget} reserved output tokens "
                        f"cannot fit in the {self.config.max_context_tokens}-token window "
                        "after compression. Reduce the current input or tool definitions."
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
        messages: list[Message],
        prev_tokens: int,
        func_tool: "ToolSet | None" = None,
        output_token_budget: int = 0,
        modalities: list[str] | None = None,
    ) -> list[Message]:
        """
        Compress/truncate the messages.

        If the context still exceeds the limit after compression (for example
        because the summary request failed), whole rounds are dropped, oldest
        first, until it fits. The latest round is always kept, even when it
        alone exceeds the limit.

        Args:
            messages: The original message list.
            prev_tokens: The token count before compression.
            func_tool: Tool set attached to the LLM summary request.
            output_token_budget: Output tokens reserved in the model window.
            modalities: Effective provider capabilities for replacement history.

        Returns:
            The compressed/truncated message list.

        Notes:
            Records the outcome in ``self.last_compression`` so callers can
            surface it to the user (webchat shows a transient notice).
        """
        logger.debug("Compress triggered, starting compression...")

        try:
            messages = await self.compressor(messages, func_tool=func_tool)
        except TypeError:
            # Third-party compressors may still implement the old
            # single-argument protocol; retry without the kwarg so they keep
            # working (without summary-request cache parity).
            logger.warning(
                "Custom compressor rejected the func_tool argument; "
                "falling back to the legacy one-argument call.",
            )
            messages = await self.compressor(messages)

        # Validate the replacement view before counting or dropping any rounds.
        if modalities:
            visible, stats = sanitize_contexts_by_modalities(messages, modalities)
            if stats.changed:
                messages = bind_checkpoint_messages(visible)
        messages = self.truncator.fix_messages(messages)
        tokens_after_summary = self._count_request_tokens(messages, func_tool)

        # calculate compress rate
        if self.config.max_context_tokens > 0:
            compress_rate = (
                tokens_after_summary / self.config.max_context_tokens
            ) * 100
            logger.info(
                f"Compress completed."
                f" {prev_tokens} -> {tokens_after_summary} tokens,"
                f" compression rate: {compress_rate:.2f}%.",
            )
        else:
            # Absolute-threshold mode without a window size: nothing to
            # express the result as a rate of.
            logger.info(
                f"Compress completed. {prev_tokens} -> {tokens_after_summary} tokens.",
            )

        tokens = tokens_after_summary
        needs_fallback = self.compressor.should_compress(
            messages, tokens, self.config.max_context_tokens
        ) or (
            self.config.max_context_tokens > 0
            and tokens + output_token_budget > self.config.max_context_tokens
        )
        if needs_fallback:
            logger.info(
                "Context still exceeds max tokens after compression, dropping the oldest rounds..."
            )
            # Drop whole rounds so tool calls stay with their results. Recount
            # after each removal and always preserve the current request.
            first_non_system = next(
                (i for i, msg in enumerate(messages) if msg.role != "system"),
                len(messages),
            )
            system_messages = messages[:first_non_system]
            rounds = [
                [seg for seg in rnd if isinstance(seg, Message)]
                for rnd in split_into_rounds(messages[first_non_system:])
            ]
            dropped_rounds = 0
            while len(rounds) > 1 and (
                self.compressor.should_compress(
                    messages, tokens, self.config.max_context_tokens
                )
                or (
                    self.config.max_context_tokens > 0
                    and tokens + output_token_budget > self.config.max_context_tokens
                )
            ):
                rounds.pop(0)
                dropped_rounds += 1
                messages = system_messages + [msg for rnd in rounds for msg in rnd]
                messages = self.truncator.fix_messages(messages)
                tokens = self._count_request_tokens(messages, func_tool)
            logger.info(f"Dropped {dropped_rounds} oldest round(s).")

            if self.compressor.should_compress(
                messages, tokens, self.config.max_context_tokens
            ):
                logger.warning(
                    "Context still exceeds the compression trigger with only the "
                    "latest round left."
                )

        self.last_compression = {
            # Reuse the existing lossy-truncation label in the WebUI notice.
            "strategy": "truncate_by_turns"
            if needs_fallback
            else getattr(self.compressor, "strategy", "custom"),
            "tokens_before": prev_tokens,
            "tokens_after": tokens,
        }
        return messages
