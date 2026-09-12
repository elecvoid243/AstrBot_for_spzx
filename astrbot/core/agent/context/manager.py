from typing import TYPE_CHECKING

from astrbot import logger

from ..message import Message
from .compressor import LLMSummaryCompressor, TruncateByTurnsCompressor
from .config import ContextConfig
from .token_counter import EstimateTokenCounter
from .truncator import ContextTruncator

if TYPE_CHECKING:
    from ..tool import ToolSet


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

        self.token_counter = config.custom_token_counter or EstimateTokenCounter()
        self.truncator = ContextTruncator()

        if config.custom_compressor:
            self.compressor = config.custom_compressor
        elif config.llm_compress_provider:
            self.compressor = LLMSummaryCompressor(
                provider=config.llm_compress_provider,
                keep_recent_ratio=config.llm_compress_keep_recent_ratio,
                instruction_text=config.llm_compress_instruction,
                token_counter=self.token_counter,
            )
        else:
            self.compressor = TruncateByTurnsCompressor(
                truncate_turns=config.truncate_turns,
                target_usage_ratio=config.truncate_target_usage_ratio,
                max_tokens=config.max_context_tokens,
                token_counter=self.token_counter,
            )

    async def process(
        self,
        messages: list[Message],
        trusted_token_usage: int = 0,
        func_tool: "ToolSet | None" = None,
    ) -> list[Message]:
        """Process the messages.

        Args:
            messages: The original message list.
            trusted_token_usage: Trusted token usage reported by the provider,
                used to estimate the current context size.
            func_tool: Tool set attached to the LLM summary request so its
                payload prefix matches previous chat requests (prefix cache
                hits). Ignored by non-LLM compressors.

        Returns:
            The processed message list.
        """
        try:
            result = messages

            # 1. 基于轮次的截断 (Enforce max turns)
            if self.config.enforce_max_turns != -1:
                result = self.truncator.truncate_by_turns(
                    result,
                    keep_most_recent_turns=self.config.enforce_max_turns,
                    drop_turns=self.config.truncate_turns,
                )

            # 2. 基于 token 的压缩
            if self.config.max_context_tokens > 0:
                total_tokens = self.token_counter.count_tokens(
                    result, trusted_token_usage
                )

                if self.compressor.should_compress(
                    result, total_tokens, self.config.max_context_tokens
                ):
                    result = await self._run_compression(
                        result, total_tokens, func_tool
                    )

            return result
        except Exception as e:
            logger.error(f"Error during context processing: {e}", exc_info=True)
            return messages

    async def _run_compression(
        self,
        messages: list[Message],
        prev_tokens: int,
        func_tool: "ToolSet | None" = None,
    ) -> list[Message]:
        """
        Compress/truncate the messages.

        Args:
            messages: The original message list.
            prev_tokens: The token count before compression.
            func_tool: Tool set attached to the LLM summary request.

        Returns:
            The compressed/truncated message list.
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

        # double check
        tokens_after_summary = self.token_counter.count_tokens(messages)

        # calculate compress rate
        compress_rate = (tokens_after_summary / self.config.max_context_tokens) * 100
        logger.info(
            f"Compress completed."
            f" {prev_tokens} -> {tokens_after_summary} tokens,"
            f" compression rate: {compress_rate:.2f}%.",
        )

        # last check
        if self.compressor.should_compress(
            messages, tokens_after_summary, self.config.max_context_tokens
        ):
            logger.info(
                "Context still exceeds max tokens after compression, applying halving truncation..."
            )
            # still need compress, truncate by half
            messages = self.truncator.truncate_by_halving(messages)

        return messages
