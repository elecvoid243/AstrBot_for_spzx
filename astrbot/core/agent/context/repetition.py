"""Collapse repetitive reasoning before it re-enters model context or ChatUI."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from math import gcd
from typing import Any

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
MAX_SANITIZE_PASSES = 16


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
            except (TypeError, ValueError):
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


def _common_stride(starts: list[int]) -> int:
    """Return the greatest common stride between consecutive occurrences.

    Args:
        starts: Non-overlapping occurrence start indices.

    Returns:
        The common stride in lines; 0 when fewer than two starts exist.
    """
    stride = 0
    for index in range(len(starts) - 1):
        stride = gcd(stride, starts[index + 1] - starts[index])
    return stride


def _find_dominant_block(
    text: str, cfg: RepetitionSanitizeConfig
) -> tuple[int, list[int]] | None:
    """Find the block whose removal deletes the most lines.

    Args:
        text: Current reasoning text.
        cfg: Sanitizer thresholds.

    Returns:
        ``(period, non_overlapping_start_indices)`` or None.
    """
    keys = [line.strip() for line in text.splitlines()]
    best: tuple[int, int, list[int]] | None = None
    for period in range(cfg.min_period, cfg.max_period + 1):
        positions: dict[tuple[str, ...], list[int]] = {}
        for index in range(0, len(keys) - period + 1):
            block = tuple(keys[index : index + period])
            if not any(len(line) >= 8 for line in block):
                continue
            if _minimal_period(block) < cfg.min_period:
                continue
            positions.setdefault(block, []).append(index)
        for starts in positions.values():
            if len(starts) < cfg.min_repeats:
                continue
            selected: list[int] = []
            last = -1
            for start in starts:
                if start < last:
                    continue
                selected.append(start)
                last = start + period
            if len(selected) < cfg.min_repeats:
                continue
            if _common_stride(selected) > cfg.max_period:
                continue
            removed = (len(selected) - 1) * period
            if best is None or removed > best[0]:
                best = (removed, period, selected)
    if best is None:
        return None
    return best[1], best[2]


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
    text: str, period: int, starts: list[int]
) -> tuple[str, int]:
    """Keep the first occurrence and remove the rest.

    Args:
        text: Current reasoning text.
        period: Block length in lines.
        starts: Non-overlapping occurrence start indices.

    Returns:
        ``(new_text, removed_occurrences)``.
    """
    lines = text.splitlines(keepends=True)
    skip: set[int] = set()
    for start in starts[1:]:
        skip.update(range(start, start + period))
    out: list[str] = []
    index = 0
    marker_done = False
    first_end = starts[0] + period
    while index < len(lines):
        if index in skip:
            index += 1
            continue
        out.append(lines[index])
        index += 1
        if not marker_done and index == first_end:
            out.append(
                f"[context sanitizer: collapsed {len(starts) - 1} x "
                f"{period}-line repeated block]\n"
            )
            marker_done = True
    return "".join(out), len(starts) - 1


def _collapse_identical_runs(
    text: str, cfg: RepetitionSanitizeConfig
) -> tuple[str, int]:
    """Collapse consecutive identical non-empty lines.

    Args:
        text: Current reasoning text.
        cfg: Sanitizer thresholds.

    Returns:
        ``(new_text, collapsed_runs)``.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    collapsed = 0
    index = 0
    while index < len(lines):
        key = lines[index].strip()
        end = index + 1
        while end < len(lines) and lines[end].strip() == key:
            end += 1
        run = end - index
        if key and run >= cfg.min_repeats:
            out.append(lines[index])
            out.append(f"[context sanitizer: collapsed {run - 1} x repeated line]\n")
            collapsed += 1
        else:
            out.extend(lines[index:end])
        index = end
    return "".join(out), collapsed


def sanitize_repetitive_text(
    text: str, cfg: RepetitionSanitizeConfig
) -> SanitizeResult:
    """Collapse consecutive repeated line blocks in a reasoning string.

    Args:
        text: Original reasoning content.
        cfg: Sanitizer thresholds.

    Returns:
        The folded text and counters; the original text when nothing matched.
    """
    original_chars = len(text)
    if not cfg.enabled or original_chars < cfg.min_chars:
        return SanitizeResult(text=text, original_chars=original_chars)
    try:
        all_lines = text.splitlines()
        non_empty = [line.strip() for line in all_lines if line.strip()]
        if len(non_empty) < cfg.min_lines:
            return SanitizeResult(text=text, original_chars=original_chars)

        collapsed_blocks = 0
        if len(all_lines) <= cfg.max_scan_lines:
            for _ in range(MAX_SANITIZE_PASSES):
                found = _find_dominant_block(text, cfg)
                if found is None:
                    break
                period, starts = found
                text, removed = _remove_block_occurrences(text, period, starts)
                collapsed_blocks += removed
                if removed == 0:
                    break

        text, run_blocks = _collapse_identical_runs(text, cfg)
        collapsed_blocks += run_blocks

        remaining = [line.strip() for line in text.splitlines() if line.strip()]
        remaining_unique = {line for line in remaining if len(line) >= 4}
        if (
            len(remaining) >= cfg.min_lines
            and len(remaining_unique) <= DEFAULT_FALLBACK_MAX_UNIQUE_LINES
            and _duplicate_ratio(remaining) > cfg.fallback_dup_ratio
        ):
            return SanitizeResult(
                text=(
                    "[context sanitizer: collapsed repetitive reasoning "
                    f"({len(non_empty)} lines, {original_chars} chars)]"
                ),
                collapsed_blocks=max(1, collapsed_blocks),
                collapsed_lines=max(0, len(non_empty) - 1),
                original_chars=original_chars,
                changed=True,
            )
        return SanitizeResult(
            text=text,
            collapsed_blocks=collapsed_blocks,
            collapsed_lines=max(0, len(non_empty) - len(remaining)),
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
