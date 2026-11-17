"""Model-facing notices for the token budget strategy.

These strings are control messages sent to the model, not UI copy: the kernel
has no runtime i18n, so they stay English like codex's developer messages.

The budget is announced at three water levels before the window is replaced:
25% is where planning still helps, 50% is where the task must be scoped, and
85% is where the current step must land. The exhausted notice is the hard stop
for a window that cannot be trimmed any further.
"""

SYSTEM_NOTICE_PREFIX = "[SYSTEM NOTICE]"

EARLY_NOTICE = (
    f"{SYSTEM_NOTICE_PREFIX} 25% of this window's context budget is used. The "
    "window is replaced when it fills, so plan the remaining work and start "
    "notes for anything the next window will need."
)

PLANNING_NOTICE = (
    f"{SYSTEM_NOTICE_PREFIX} 50% of this window's context budget is used. Keep "
    "the task scoped: read what you need instead of whole files, and write "
    "durable state to notes before the window fills."
)

WRAP_UP_NOTICE = (
    f"{SYSTEM_NOTICE_PREFIX} 85% of this window's context budget is used; the "
    "window is replaced at 100%. Finish the current step, save what the next "
    "window needs to notes, and do not start work you cannot finish here."
)

EXHAUSTED_TEXT = (
    f"{SYSTEM_NOTICE_PREFIX} The context is at its limit and cannot be trimmed "
    "further. Give your current conclusion without starting new tool calls."
)
