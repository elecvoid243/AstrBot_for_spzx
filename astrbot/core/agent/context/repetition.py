"""Collapse repetitive reasoning before it re-enters model context or ChatUI."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from itertools import groupby
from time import perf_counter
from typing import Any

from astrbot.core.agent.context.reasoning_ngram import observe_loop
from astrbot.core.agent.message import Message, ThinkPart

logger = logging.getLogger("astrbot")

DEFAULT_MIN_PERIOD = 3
DEFAULT_MAX_PERIOD = 8
DEFAULT_MIN_REPEATS = 8
DEFAULT_MIN_LINES = 40
DEFAULT_MIN_CHARS = 512
DEFAULT_MAX_SCAN_LINES = 20_000
DEFAULT_FALLBACK_DUP_RATIO = 0.8
DEFAULT_FALLBACK_MAX_UNIQUE_LINES = 8
MAX_SCAN_CHARS = 1_000_000
MAX_SCAN_PERIOD = 32
_FOLD_MARKER = re.compile(
    r"\[context sanitizer: collapsed (?:\d+ x (?:\d+-line repeated block|repeated line)"
    r"|repetitive reasoning \(\d+ lines, \d+ chars\))\]",
)


@dataclass(frozen=True, slots=True)
class RepetitionSanitizeConfig:
    """Thresholds for folding repeated reasoning blocks."""

    enabled: bool = True
    min_period: int = DEFAULT_MIN_PERIOD
    max_period: int = DEFAULT_MAX_PERIOD
    min_repeats: int = DEFAULT_MIN_REPEATS
    min_lines: int = DEFAULT_MIN_LINES
    min_chars: int = DEFAULT_MIN_CHARS
    max_scan_lines: int = DEFAULT_MAX_SCAN_LINES
    fallback_dup_ratio: float = DEFAULT_FALLBACK_DUP_RATIO

    @classmethod
    def from_provider_config(cls, raw: Any) -> RepetitionSanitizeConfig:
        """Build a config from a provider's ``context_sanitizer`` mapping.

        Args:
            raw: Provider configuration value; non-mapping values use defaults.

        Returns:
            A validated, conservative configuration.
        """
        if not isinstance(raw, dict):
            return cls()

        def _int(name: str, default: int, minimum: int) -> int:
            try:
                value = int(raw.get(name, default))
            except (TypeError, ValueError, OverflowError):
                return default
            return max(minimum, value)

        def _ratio(name: str, default: float) -> float:
            try:
                value = float(raw.get(name, default))
            except (TypeError, ValueError):
                return default
            return min(1.0, max(0.0, value))

        def _bool(name: str, default: bool) -> bool:
            value = raw.get(name, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "on"}:
                    return True
                if lowered in {"false", "0", "no", "off"}:
                    return False
            return default

        min_period = _int("min_period", DEFAULT_MIN_PERIOD, 2)
        return cls(
            enabled=_bool("enabled", True),
            min_period=min_period,
            max_period=_int("max_period", DEFAULT_MAX_PERIOD, min_period),
            min_repeats=_int("min_repeats", DEFAULT_MIN_REPEATS, 2),
            min_lines=_int("min_lines", DEFAULT_MIN_LINES, 1),
            min_chars=_int("min_chars", DEFAULT_MIN_CHARS, 0),
            max_scan_lines=_int("max_scan_lines", DEFAULT_MAX_SCAN_LINES, 1),
            fallback_dup_ratio=_ratio("fallback_dup_ratio", DEFAULT_FALLBACK_DUP_RATIO),
        )


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    """Folded text and counters for one reasoning string."""

    text: str
    collapsed_blocks: int = 0
    collapsed_lines: int = 0
    original_chars: int = 0
    changed: bool = False


def _duplicate_ratio(lines: list[str]) -> float:
    meaningful = [line for line in lines if len(line) >= 4]
    if not meaningful:
        return 0.0
    counts = Counter(meaningful)
    duplicated = sum(count for count in counts.values() if count > 1)
    return duplicated / len(meaningful)


def _find_repeated_blocks(
    keys: list[str], cfg: RepetitionSanitizeConfig
) -> list[tuple[int, int, list[int]]]:
    """Find nearby occurrences of exact line blocks in the original text.

    Args:
        keys: Original lines without their line endings.
        cfg: Sanitizer thresholds.

    Returns:
        Candidates as tuples of removable line count, period, and starts.
    """
    counts = Counter(keys)
    eligible = [i for i, key in enumerate(keys) if counts[key] >= cfg.min_repeats]
    groups: list[tuple[int, int, list[int]]] = []
    max_period = min(cfg.max_period, MAX_SCAN_PERIOD)
    for period in range(cfg.min_period, max_period + 1):
        # Start a new group after a gap too wide to support a local loop.
        max_gap = min(max_period, 2 * period)
        positions: dict[tuple[str, ...], list[int]] = {}
        for index in eligible:
            if index + period > len(keys):
                break
            block = tuple(keys[index : index + period])
            positions.setdefault(block, []).append(index)
        for block, starts in positions.items():
            if len(starts) < cfg.min_repeats:
                continue
            if not any(len(line.strip()) >= 8 for line in block):
                continue
            if _minimal_period(block) < cfg.min_period:
                continue
            selected: list[int] = []
            for start in starts:
                if selected:
                    distance = start - selected[-1]
                    if distance < period:
                        continue
                    if distance > max_gap:
                        if len(selected) >= cfg.min_repeats:
                            groups.append(
                                ((len(selected) - 1) * period, period, selected)
                            )
                        selected = []
                selected.append(start)
            if len(selected) >= cfg.min_repeats:
                groups.append(((len(selected) - 1) * period, period, selected))
    return groups


def _minimal_period(block: tuple[str, ...]) -> int:
    """Return the smallest period that reproduces the block.

    Args:
        block: Normalized line block.

    Returns:
        The minimal period in lines.
    """
    for period in range(1, len(block)):
        if all(block[index] == block[index % period] for index in range(len(block))):
            return period
    return len(block)


def _remove_block_occurrences(
    text: str, groups: list[tuple[int, int, list[int]]]
) -> tuple[str, int]:
    """Fold non-overlapping groups in a single reconstruction of the text.

    Args:
        text: Original reasoning text.
        groups: Candidate blocks in the original text.

    Returns:
        ``(new_text, removed_occurrences)``.
    """
    lines = text.splitlines(keepends=True)
    # Reserve the retained occurrence as well so later groups cannot remove it.
    state = bytearray(len(lines))
    markers: dict[int, str] = {}
    removed = 0
    groups.sort(key=lambda group: group[0], reverse=True)
    for _, period, starts in groups:
        if any(any(state[start : start + period]) for start in starts):
            continue
        first = starts[0]
        state[first : first + period] = b"\x01" * period
        for start in starts[1:]:
            state[start : start + period] = b"\x02" * period
        count = len(starts) - 1
        description = (
            "repeated line" if period == 1 else f"{period}-line repeated block"
        )
        markers[first + period - 1] = (
            f"[context sanitizer: collapsed {count} x {description}]\n"
        )
        removed += count
    return (
        "".join(
            line + markers.get(i, "") for i, line in enumerate(lines) if state[i] != 2
        ),
        removed,
    )


def sanitize_repetitive_text(
    text: str, cfg: RepetitionSanitizeConfig, *, observe_ngram: bool = False
) -> SanitizeResult:
    """Collapse consecutive repeated line blocks in a reasoning string.

    Args:
        text: Original reasoning content.
        cfg: Sanitizer thresholds.
        observe_ngram: Record a possible loop before folding fresh reasoning.

    Returns:
        The folded text and counters; the original text when nothing matched.
    """
    original_chars = len(text)
    if not cfg.enabled:
        return SanitizeResult(text=text, original_chars=original_chars)
    if observe_ngram:
        try:
            started = perf_counter()
            observation = observe_loop(text)
            if observation.start is not None:
                logger.info(
                    "context sanitizer: suspected n-gram reasoning loop "
                    "(period=%d, offset=%d, window_chars=%d, input_chars=%d, scan_ms=%.2f)",
                    observation.period,
                    observation.start,
                    observation.window_chars,
                    original_chars,
                    (perf_counter() - started) * 1000,
                )
        except Exception:
            logger.warning("reasoning n-gram observation failed", exc_info=True)
    if not cfg.min_chars <= original_chars <= MAX_SCAN_CHARS:
        return SanitizeResult(text=text, original_chars=original_chars)
    try:
        all_lines = text.splitlines()
        # Previously folded history must not form new loops around removed lines.
        if "[context sanitizer: collapsed " in text and any(
            _FOLD_MARKER.fullmatch(line) for line in all_lines
        ):
            return SanitizeResult(text=text, original_chars=original_chars)
        non_empty = [line.strip() for line in all_lines if line.strip()]
        if len(non_empty) < cfg.min_lines:
            return SanitizeResult(text=text, original_chars=original_chars)

        groups = []
        if len(all_lines) <= min(cfg.max_scan_lines, DEFAULT_MAX_SCAN_LINES):
            groups = _find_repeated_blocks(all_lines, cfg)

        # A high duplication ratio only permits folding exact contiguous runs.
        if (
            len({line for line in non_empty if len(line) >= 4})
            <= DEFAULT_FALLBACK_MAX_UNIQUE_LINES
            and _duplicate_ratio(non_empty) > cfg.fallback_dup_ratio
        ):
            start = 0
            for key, run in groupby(all_lines):
                count = sum(1 for _ in run)
                if len(key.strip()) >= 4 and count >= cfg.min_repeats:
                    groups.append((count - 1, 1, list(range(start, start + count))))
                start += count

        if not groups:
            return SanitizeResult(text=text, original_chars=original_chars)
        folded, collapsed_blocks = _remove_block_occurrences(text, groups)
        return SanitizeResult(
            text=folded,
            collapsed_blocks=collapsed_blocks,
            collapsed_lines=max(
                0,
                len(non_empty)
                - sum(bool(line.strip()) for line in folded.splitlines()),
            ),
            original_chars=original_chars,
            changed=collapsed_blocks > 0,
        )
    except Exception:
        logger.warning("context sanitizer failed; keeping original text", exc_info=True)
        return SanitizeResult(text=text, original_chars=original_chars)


def sanitize_think_part(part: ThinkPart, cfg: RepetitionSanitizeConfig) -> ThinkPart:
    """Fold one ThinkPart unless it carries an encrypted reasoning signature.

    Args:
        part: Assistant reasoning part.
        cfg: Sanitizer thresholds.

    Returns:
        A new ThinkPart when folding changed the text, otherwise the input.
    """
    if part.encrypted:
        return part
    result = sanitize_repetitive_text(part.think, cfg)
    if not result.changed:
        return part
    return part.model_copy(update={"think": result.text})


def sanitize_message_think_parts(
    messages: list[Message], cfg: RepetitionSanitizeConfig
) -> list[Message]:
    """Fold ThinkParts in assistant messages without touching other roles.

    Args:
        messages: Model-visible messages.
        cfg: Sanitizer thresholds.

    Returns:
        A new list when any message changed, otherwise the input list.
    """
    changed = False
    output: list[Message] = []
    for message in messages:
        if message.role != "assistant" or not isinstance(message.content, list):
            output.append(message)
            continue
        parts = []
        message_changed = False
        for part in message.content:
            if isinstance(part, ThinkPart):
                sanitized = sanitize_think_part(part, cfg)
                if sanitized is not part:
                    message_changed = True
                parts.append(sanitized)
            else:
                parts.append(part)
        if message_changed:
            output.append(message.model_copy(update={"content": parts}))
            changed = True
        else:
            output.append(message)
    return output if changed else messages


def sanitize_history_think_parts(
    history_data: list[dict], cfg: RepetitionSanitizeConfig
) -> None:
    """Fold bot think parts in a ChatUI history payload in place.

    Args:
        history_data: Platform-history record dicts.
        cfg: Sanitizer thresholds.
    """
    for record in history_data:
        if not isinstance(record, dict):
            continue
        content = record.get("content")
        if not isinstance(content, dict) or content.get("type") != "bot":
            continue
        message = content.get("message")
        if not isinstance(message, list):
            continue
        for part in message:
            if not isinstance(part, dict) or part.get("type") != "think":
                continue
            if part.get("encrypted"):
                continue
            original = part.get("think")
            if not isinstance(original, str):
                continue
            result = sanitize_repetitive_text(original, cfg)
            if not result.changed:
                continue
            part["think"] = result.text
            part["think_original"] = original
            part["think_sanitized"] = True
            part["think_collapsed_lines"] = result.collapsed_lines
