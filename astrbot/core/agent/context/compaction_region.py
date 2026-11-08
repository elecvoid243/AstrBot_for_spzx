"""Select a tool-pair balanced range to compact.

The selector mirrors deepseek-harness' ``selectCompactableRange``: it retains a
priced tail, rewinds the cut point to a balanced tool-call/result boundary, and
never splits a batch. Unlike dsh, AstrBot also keeps the exact current user
request outside the range, so a long single round can only compact the completed
steps that follow it.
"""

from dataclasses import dataclass

from ..message import Message
from .token_counter import TokenCounter


@dataclass(frozen=True)
class CompactionRange:
    """A contiguous, balanced range to replace with a checkpoint.

    Attributes:
        start: Inclusive first message index.
        end: Exclusive last message index.
        shadowed_tokens: Estimated tokens removed by the replacement.
        retained_tokens: Estimated tokens kept verbatim after the range.
    """

    start: int
    end: int
    shadowed_tokens: int
    retained_tokens: int


def _call_ids(message: Message) -> list[str]:
    """Return the non-empty tool-call ids carried by an assistant message."""
    ids: list[str] = []
    for call in message.tool_calls or []:
        call_id = call.get("id") if isinstance(call, dict) else call.id
        if isinstance(call_id, str) and call_id:
            ids.append(call_id)
    return ids


def tool_pairing_balanced_before(messages: list[Message], index: int) -> bool:
    """Check whether every tool call before ``index`` has a result.

    Args:
        messages: Model-visible history.
        index: Cut position; messages before it are inspected.

    Returns:
        True when no tool call before the cut is still awaiting a result.
    """
    open_ids: set[str] = set()
    for message in messages[:index]:
        if message.role == "assistant":
            open_ids.update(_call_ids(message))
        elif message.role == "tool" and message.tool_call_id:
            open_ids.discard(message.tool_call_id)
    return not open_ids


def tool_pairing_balanced_after(messages: list[Message], index: int) -> bool:
    """Check whether the cut after ``index`` leaves no open tool call.

    Args:
        messages: Model-visible history.
        index: Inclusive position of the last retained message.

    Returns:
        True when the cut after ``index`` is balanced.
    """
    return tool_pairing_balanced_before(messages, index + 1)


def select_compaction_range(
    messages: list[Message],
    *,
    retain_tokens: int,
    token_counter: TokenCounter,
    protected_messages: list[Message] | None = None,
) -> CompactionRange | None:
    """Select the largest safe prefix that leaves a priced tail.

    Args:
        messages: Model-visible history, including the current request.
        retain_tokens: Minimum verbatim tail budget; <= 0 keeps only the last
            balanced unit.
        token_counter: Counter used to price units and the final range.
        protected_messages: Inputs that must never enter the range.

    Returns:
        A balanced range, or None when no safe range exists.
    """
    first_non_system = next(
        (index for index, message in enumerate(messages) if message.role != "system"),
        len(messages),
    )
    if first_non_system >= len(messages):
        return None

    protected_ids = {id(message) for message in (protected_messages or [])}

    # Group completed tool batches so a cut never separates a call from its
    # results. Walk backwards because the tail is what we retain.
    units: list[tuple[int, int]] = []
    index = len(messages)
    while index > first_non_system:
        message = messages[index - 1]
        if message.role == "tool":
            group_start = index - 1
            while (
                group_start > first_non_system
                and messages[group_start - 1].role == "tool"
            ):
                group_start -= 1
            owner = group_start - 1
            if (
                owner >= first_non_system
                and messages[owner].role == "assistant"
                and messages[owner].tool_calls
            ):
                units.append((owner, index))
                index = owner
            else:
                units.append((group_start, index))
                index = group_start
        else:
            units.append((index - 1, index))
            index -= 1
    units.reverse()
    if not units:
        return None

    keep_index = len(units) - 1
    retained = token_counter.count_tokens(
        messages[units[keep_index][0] : units[keep_index][1]]
    )
    while keep_index > 0 and retained < retain_tokens:
        keep_index -= 1
        retained += token_counter.count_tokens(
            messages[units[keep_index][0] : units[keep_index][1]]
        )
    cut = units[keep_index][0]

    # A partial/overlong unit may leave an open call at the cut; keep more
    # rather than splitting a batch.
    while cut > first_non_system and not tool_pairing_balanced_before(messages, cut):
        cut -= 1

    last_user = -1
    for position, message in enumerate(messages):
        if message.role == "user" and not message.compaction_id:
            last_user = position

    if last_user >= 0 and cut > last_user:
        # The tail starts inside the current round: keep the exact current
        # request outside the range and compact only the completed steps after
        # it.
        start = max(first_non_system, last_user + 1)
    else:
        start = first_non_system
        if last_user >= 0:
            cut = min(cut, last_user)

    end = cut
    while start < end and (
        messages[start].role == "system" or id(messages[start]) in protected_ids
    ):
        start += 1

    if end <= start:
        return None
    if not tool_pairing_balanced_before(messages, start):
        return None
    if not tool_pairing_balanced_before(messages, end):
        return None
    for position in range(start, end):
        message = messages[position]
        if message.role == "system" or id(message) in protected_ids:
            return None

    return CompactionRange(
        start=start,
        end=end,
        shadowed_tokens=token_counter.count_tokens(messages[start:end]),
        retained_tokens=token_counter.count_tokens(messages[end:]),
    )
