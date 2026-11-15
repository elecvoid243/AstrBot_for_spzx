from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .compressor import ContextCompressor
from .token_counter import TokenCounter

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider


DEFAULT_COMPACTION_REMINDER_TOKENS = 16384
"""Remaining budget that triggers the one-shot window reminder."""


def resolve_budget_strategy(
    strategy: str, provider_config: dict
) -> tuple[str, int]:
    """Resolve the effective overflow strategy and reminder threshold.

    The window is a provider fact, so a token budget needs one: without a
    known window the strategy falls back to LLM compression instead of
    guessing a budget that the provider would not enforce.

    Args:
        strategy: Configured overflow strategy.
        provider_config: Provider configuration holding ``max_context_tokens``
            and the optional ``compaction_reminder_tokens``.

    Returns:
        ``(effective strategy, reminder tokens)``.
    """
    reminder_tokens = provider_config.get("compaction_reminder_tokens")
    try:
        reminder_tokens = int(reminder_tokens)
    except (TypeError, ValueError):
        reminder_tokens = DEFAULT_COMPACTION_REMINDER_TOKENS
    if reminder_tokens <= 0:
        reminder_tokens = DEFAULT_COMPACTION_REMINDER_TOKENS

    if strategy != "token_budget":
        return strategy, reminder_tokens

    try:
        window = int(provider_config.get("max_context_tokens", 0) or 0)
    except (TypeError, ValueError):
        window = 0
    if window <= 0:
        return "llm_compress", reminder_tokens
    return strategy, reminder_tokens


@dataclass
class ContextConfig:
    """Context configuration class."""

    strategy: str = "llm_compress"
    """Overflow strategy that decides how a request is compacted.

    ``llm_compress`` summarizes old history; ``token_budget`` replaces the
    window with the current round and never calls a model.
    """
    max_context_tokens: int = 0
    """Maximum number of context tokens. <= 0 means no limit."""
    compress_threshold_tokens: int = 0
    """Absolute token threshold that triggers compression.

    When > 0, the context is compressed once its estimated token count exceeds
    this value, regardless of ``max_context_tokens``; <= 0 falls back to the
    compressors' ratio-based trigger.
    """
    truncate_turns: int = 1
    """Internal fallback: turns discarded per pass when no summary provider exists.

    Kept out of the user-facing compression config; the fallback policy is
    fixed unless a caller constructs ``ContextConfig`` directly.
    """
    truncate_target_usage_ratio: float = 0.4
    """Internal fallback: target usage ratio for the no-provider truncator.

    Older turns are dropped until the remaining tokens are below
    ``max_context_tokens * this ratio``. ``<= 0`` disables token-budget
    truncation and falls back to a fixed ``truncate_turns`` drop.
    """
    threshold_ratio: float = 0.8
    """Fraction of the context window that triggers compaction."""
    headroom_tokens: int = 16384
    """Space kept free above the trigger for compaction work."""
    reminder_tokens: int = DEFAULT_COMPACTION_REMINDER_TOKENS
    """Remaining budget that triggers the one-shot reminder (token_budget only)."""
    retain_ratio: float = 0.15
    """Fraction of the message budget kept verbatim during compaction."""
    target_ratio: float = 0.55
    """Desired post-compaction usage; <= 0 disables the target."""
    min_yield_ratio: float = 0.0
    """Minimum reduction a checkpoint must achieve; 0 accepts any smaller summary."""
    max_summary_tokens: int = 0
    """Upper bound for the summary request's output; 0 keeps the chat limit."""
    compaction_retries: int = 1
    """Extra summary passes within one process call."""
    llm_compress_keep_recent_ratio: float | None = None
    """Deprecated alias for ``retain_ratio``; kept for existing callers."""
    llm_compress_provider: "Provider | None" = None
    """LLM provider used for compression tasks. If None, truncation strategy is used."""
    custom_token_counter: TokenCounter | None = None
    """Custom token counting method. If None, the default method is used."""
    custom_compressor: ContextCompressor | None = None
    """Custom context compression method. If None, the default method is used."""
    llm_params: dict = field(default_factory=dict)
    """Per-request LLM parameters (e.g. thinking_effort) attached to the LLM
    summary request so provider fields derived from them (such as
    reasoning_effort) stay identical to the chat requests the provider prefix
    cache was built from."""

    def __post_init__(self) -> None:
        """Map the legacy keep-recent ratio onto the message-budget ratio."""
        if self.llm_compress_keep_recent_ratio is not None:
            self.retain_ratio = self.llm_compress_keep_recent_ratio
