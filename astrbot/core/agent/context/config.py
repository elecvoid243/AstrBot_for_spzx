from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .compressor import ContextCompressor
from .token_counter import TokenCounter

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider


@dataclass
class ContextConfig:
    """Context configuration class."""

    max_context_tokens: int = 0
    """Maximum number of context tokens. <= 0 means no limit."""
    compress_threshold_tokens: int = 0
    """Absolute token threshold that triggers compression.

    When > 0, the context is compressed once its estimated token count exceeds
    this value, regardless of ``max_context_tokens``; <= 0 falls back to the
    compressors' ratio-based trigger.
    """
    truncate_turns: int = 1
    """Number of conversation turns to discard at once when truncation is triggered.

    Used as the fixed drop count of the turn-based compressor and as the
    minimum drop for token-budget truncation.
    """
    truncate_target_usage_ratio: float = 0.4
    """Target usage ratio of ``max_context_tokens`` after turn-based truncation.

    When token-budget truncation is active, the oldest turns are dropped until
    the remaining estimated tokens are below ``max_context_tokens`` times this
    ratio, giving the request headroom for a stable prefix (prefix cache).
    ``<= 0`` disables token-budget truncation and falls back to a fixed
    ``truncate_turns`` drop.
    """
    threshold_ratio: float = 0.8
    """Fraction of the context window that triggers compaction."""
    headroom_tokens: int = 16384
    """Space kept free above the trigger for compaction work."""
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
