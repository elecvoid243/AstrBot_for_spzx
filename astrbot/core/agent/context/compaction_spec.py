"""Pure token-budget resolution for context compaction.

Every compaction threshold is resolved here so the compressors do not carry
their own ratio constants. The formulas mirror deepseek-harness'
``compaction-basic``: the trigger must leave room for the request's output
reservation and a compaction headroom, while the verbatim tail scales from the
message budget rather than the current context size.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CompactionSpec:
    """Resolved budgets for one model request.

    Attributes:
        context_window: Effective model window in tokens.
        output_budget: Output tokens reserved for this request.
        headroom_tokens: Space kept free above the trigger for compaction work.
        threshold_tokens: Input-token trigger for compaction.
        retain_tokens: Verbatim recent tail kept during compaction.
        target_tokens: Desired usage after compaction; 0 disables the target.
        max_summary_tokens: Output cap for the summary request.
        compaction_retries: Extra summary passes within one process call.
        min_growth_tokens: Token growth required before another auto compaction.
        min_yield_ratio: Minimum fraction of the shadowed tokens a checkpoint
            must remove; 0 accepts any strictly smaller summary.
    """

    context_window: int
    output_budget: int
    headroom_tokens: int
    threshold_tokens: int
    retain_tokens: int
    target_tokens: int
    max_summary_tokens: int
    compaction_retries: int
    min_growth_tokens: int
    min_yield_ratio: float


def _coerce_int(value: object, default: int) -> int:
    """Coerce a config value to a non-negative integer.

    Args:
        value: Raw configuration value.
        default: Fallback used for bools and malformed values.

    Returns:
        A non-negative integer.
    """
    if isinstance(value, bool):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _coerce_ratio(value: object, default: float) -> float:
    """Coerce a config value to a ratio in the closed interval [0, 1].

    Args:
        value: Raw configuration value.
        default: Fallback used for bools and malformed values.

    Returns:
        A ratio between 0 and 1.
    """
    if isinstance(value, bool):
        return default
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def resolve_compaction_spec(
    *,
    context_window: int,
    output_budget: int,
    threshold_ratio: float = 0.8,
    headroom_tokens: int = 16384,
    absolute_threshold_tokens: int = 0,
    retain_ratio: float = 0.16,
    target_ratio: float = 0.55,
    max_summary_tokens: int = 8192,
    compaction_retries: int = 1,
    min_growth_ratio: float = 0.1,
    min_yield_ratio: float = 0.0,
) -> CompactionSpec | None:
    """Resolve the compaction budgets for one request.

    Args:
        context_window: Effective model window; <= 0 disables compaction.
        output_budget: Output tokens reserved for this request.
        threshold_ratio: Fraction of the window that triggers compaction.
        headroom_tokens: Space kept free above the trigger.
        absolute_threshold_tokens: Provider-level absolute trigger; 0 uses the
            ratio. A positive value is clamped to the pressure budget.
        retain_ratio: Fraction of the message budget kept verbatim.
        target_ratio: Desired post-compaction usage; 0 disables the target.
        max_summary_tokens: Upper bound for the summary request's output.
        compaction_retries: Extra summary passes; capped at 3.
        min_growth_ratio: Fraction of the window required before re-compacting.
        min_yield_ratio: Minimum reduction a checkpoint must achieve.

    Returns:
        The resolved spec, or None when the request has no message budget.
    """
    window = _coerce_int(context_window, 0)
    if window <= 0:
        return None

    reserved = min(_coerce_int(output_budget, 0), window)
    message_budget = window - reserved
    if message_budget <= 0:
        return None

    headroom = _coerce_int(headroom_tokens, 0)
    pressure_budget = message_budget - headroom
    if pressure_budget <= 0:
        # A headroom larger than the message budget is unusable; ignore it
        # rather than disabling compaction entirely.
        headroom = 0
        pressure_budget = message_budget

    ratio = _coerce_ratio(threshold_ratio, 0.8)
    if ratio <= 0:
        ratio = 0.8
    threshold = min(int(window * ratio), pressure_budget)

    absolute = _coerce_int(absolute_threshold_tokens, 0)
    if absolute > 0:
        threshold = min(absolute, pressure_budget)
    threshold = max(2, threshold)

    retain = int(message_budget * _coerce_ratio(retain_ratio, 0.16))
    retain = max(1, min(retain, threshold - 1))

    target = int(window * _coerce_ratio(target_ratio, 0.55))
    if target <= 0 or target >= threshold:
        target = 0

    summary_cap = _coerce_int(max_summary_tokens, 8192)
    summary_cap = max(
        64, min(summary_cap, headroom or pressure_budget, pressure_budget)
    )

    retries = min(3, _coerce_int(compaction_retries, 1))
    min_growth = max(1024, int(window * _coerce_ratio(min_growth_ratio, 0.1)))
    min_growth = min(min_growth, threshold)

    return CompactionSpec(
        context_window=window,
        output_budget=reserved,
        headroom_tokens=headroom,
        threshold_tokens=threshold,
        retain_tokens=retain,
        target_tokens=target,
        max_summary_tokens=summary_cap,
        compaction_retries=retries,
        min_growth_tokens=min_growth,
        min_yield_ratio=min(0.9, _coerce_ratio(min_yield_ratio, 0.0)),
    )
