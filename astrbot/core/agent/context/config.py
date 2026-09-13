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
    enforce_max_turns: int = -1  # -1 means no limit
    """Maximum number of conversation turns to keep. -1 means no limit. Executed before compression."""
    truncate_turns: int = 1
    """Number of conversation turns to discard at once when truncation is triggered.
    Two processes will use this value:

    1. Enforce max turns truncation.
    2. Truncation by turns compression strategy.
    """
    truncate_target_usage_ratio: float = 0.4
    """Target usage ratio of ``max_context_tokens`` after turn-based truncation.

    When token-budget truncation is active, the oldest turns are dropped until
    the remaining estimated tokens are below ``max_context_tokens`` times this
    ratio, giving the request headroom for a stable prefix (prefix cache).
    ``<= 0`` disables token-budget truncation and falls back to a fixed
    ``truncate_turns`` drop.
    """
    llm_compress_instruction: str | None = None
    """Instruction prompt for LLM-based compression."""
    llm_compress_keep_recent_ratio: float = 0.15
    """Percent of current context tokens to keep as exact recent context during LLM-based compression."""
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
