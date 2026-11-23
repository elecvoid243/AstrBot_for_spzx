"""Observe approximate loops in one reasoning part with bounded sampling.

For offline diagnosis, run this module with ``--full-scan`` and pass a reasoning
part on stdin.
"""

import re
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain

NGRAM_SIZE = 6
MIN_UNIT_CHARS = 2 * NGRAM_SIZE
MIN_NGRAM_INPUT_CHARS = 3 * MIN_UNIT_CHARS
MIN_SIMILARITY = 0.65
MAX_PERIOD = 4
MAX_UNIT_CHARS = 256
WINDOW_CHARS = 4096
WINDOW_COUNT = 8
STREAM_CHECK_CHARS = 256
STREAM_MIN_REPEAT_CHARS = 512
STREAM_MIN_REPEATS = 8
STREAM_MAX_CANDIDATES = 16

_SENTENCE_END = re.compile(r"[\r\n。！？!?；;]+|\.(?=\s|$)")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)*")


@dataclass(frozen=True, slots=True)
class Observation:
    """A possible loop and the amount of text covered by the opened windows."""

    window_chars: int
    start: int | None = None
    period: int | None = None


class StreamingReasoningLoop:
    """Detect sustained exact repetition with bounded state across deltas."""

    def __init__(self) -> None:
        self.tail = ""
        self.pending = 0

    def feed(self, delta: str) -> bool:
        """Report a sustained exact suffix loop before the delta is forwarded.

        Args:
            delta: New reasoning text from the provider.

        Returns:
            Whether the latest suffix contains at least eight identical cycles.
        """
        offset = 0
        while offset < len(delta):
            count = min(STREAM_CHECK_CHARS - self.pending, len(delta) - offset)
            self.tail = (self.tail + delta[offset : offset + count])[-WINDOW_CHARS:]
            offset += count
            self.pending += count
            if self.pending < STREAM_CHECK_CHARS:
                break
            self.pending = 0
            if len(self.tail) < STREAM_MIN_REPEAT_CHARS:
                continue

            # Locate candidate periods with a short anchor, then verify every
            # character. Approximate matches are suitable for logs only.
            anchor = self.tail[-32:]
            last = len(self.tail) - len(anchor)
            earliest = max(0, last - len(self.tail) // STREAM_MIN_REPEATS)
            end = len(self.tail) - 1
            for _ in range(STREAM_MAX_CANDIDATES):
                previous = self.tail.rfind(anchor, earliest, end)
                if previous < 0:
                    break
                period = last - previous
                length = max(STREAM_MIN_REPEAT_CHARS, period * STREAM_MIN_REPEATS)
                if self.tail[-length:-period] == self.tail[-length + period :] and any(
                    char.isalnum() for char in self.tail[-period:]
                ):
                    return True
                end = previous + len(anchor) - 1
        return False


def _units(text: str, start: int, end: int) -> Iterator[tuple[int, str]]:
    """Yield bounded reasoning units and their original offsets.

    Args:
        text: The reasoning part.
        start: Inclusive region offset.
        end: Exclusive region offset.

    Yields:
        Pairs of original offset and text.
    """
    for boundary in chain(_SENTENCE_END.finditer(text, start, end), (None,)):
        stop = end if boundary is None else boundary.start()
        for offset in range(start, stop, MAX_UNIT_CHARS):
            yield offset, text[offset : min(offset + MAX_UNIT_CHARS, stop)]
        if boundary is not None:
            start = boundary.end()


def _similar(
    left: tuple[set[str], tuple[str, ...]], right: tuple[set[str], tuple[str, ...]]
) -> bool:
    """Check approximate text similarity with an exact numeric check.

    Args:
        left: Character grams and numeric fields for one unit.
        right: Character grams and numeric fields for the other unit.

    Returns:
        Whether the units satisfy both checks.
    """
    left_grams, left_numbers = left
    right_grams, right_numbers = right
    if left_numbers != right_numbers:
        return False
    shared = len(left_grams & right_grams)
    return shared >= MIN_SIMILARITY * (len(left_grams) + len(right_grams) - shared)


def observe_loop(text: str, *, full_scan: bool = False) -> Observation:
    """Find three locally recurring blocks using character 6-gram overlap.

    Args:
        text: The raw text of one reasoning part.
        full_scan: Scan every character for comparison with bounded sampling.

    Returns:
        The first observed match and the total size of the opened windows.
    """
    size = len(text)
    if size < MIN_NGRAM_INPUT_CHARS:
        return Observation(0)
    if full_scan or size <= WINDOW_COUNT * WINDOW_CHARS:
        window_size = size
        starts = [0]
    else:
        window_size = WINDOW_CHARS
        last_start = size - window_size
        starts = [last_start * i // (WINDOW_COUNT - 1) for i in range(WINDOW_COUNT)]
        starts.reverse()  # A loop near the end of a reasoning part is useful early.

    window_chars = 0
    for start in starts:
        window_chars += window_size
        recent = deque(maxlen=3 * MAX_PERIOD)
        streaks = [0] * (MAX_PERIOD + 1)
        for offset, raw in _units(text, start, start + window_size):
            normalized = " ".join(raw.casefold().split())
            if not normalized:
                continue
            if len(normalized) < MIN_UNIT_CHARS:
                recent.clear()
                streaks = [0] * (MAX_PERIOD + 1)
                continue
            grams = {
                normalized[i : i + NGRAM_SIZE]
                for i in range(len(normalized) - NGRAM_SIZE + 1)
            }
            signature = (grams, tuple(_NUMBER.findall(normalized)))
            recent.append((offset, signature))
            for period in range(1, min(MAX_PERIOD, len(recent) - 1) + 1):
                if not _similar(recent[-period - 1][1], signature):
                    streaks[period] = 0
                    continue
                streaks[period] += 1
                if streaks[period] >= 2 * period and all(
                    _similar(recent[-3 * period + phase][1], recent[-period + phase][1])
                    for phase in range(period)
                ):
                    # Check the first and third blocks to guard against gradual drift.
                    return Observation(window_chars, recent[-3 * period][0], period)
    return Observation(window_chars)


if __name__ == "__main__":
    import argparse
    import json
    import sys
    from dataclasses import asdict
    from time import perf_counter

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-scan", action="store_true")
    options = parser.parse_args()
    reasoning = sys.stdin.read()
    started = perf_counter()
    result = observe_loop(reasoning, full_scan=options.full_scan)
    print(
        json.dumps(
            {
                **asdict(result),
                "input_chars": len(reasoning),
                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
            }
        )
    )
