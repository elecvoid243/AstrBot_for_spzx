"""Model-facing notices for the token budget strategy.

These strings are control messages sent to the model, not UI copy: the kernel
has no runtime i18n, so they stay English like codex's developer messages.
"""

REMINDER_TEMPLATE = (
    "[context budget] {n} tokens left in this window. Wrap up the current task "
    "first; if more work is needed, give a conclusion and continue next turn."
)

EXHAUSTED_TEXT = (
    "[context budget] The context is at its limit and cannot be trimmed "
    "further. Give your current conclusion without starting new tool calls."
)


def build_reminder(remaining: int) -> str:
    """Render the low-budget reminder for a concrete remaining count."""
    return REMINDER_TEMPLATE.replace("{n}", str(remaining))
