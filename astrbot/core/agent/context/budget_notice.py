"""Model-facing notices for the token budget strategy.

These strings are English control messages sent to the model. The four budget
levels move the model from planning to handoff. The exhausted notice applies
when trimming cannot free enough room.
"""

SYSTEM_NOTICE_PREFIX = "[SYSTEM NOTICE]"

EARLY_NOTICE = (
    f"{SYSTEM_NOTICE_PREFIX} 25% of this window's context budget is used. The "
    "window is replaced when it fills. Plan the remaining work and start "
    "a handoff note if the task may span windows."
)

PLANNING_NOTICE = (
    f"{SYSTEM_NOTICE_PREFIX} 50% of this window's context budget is used. Keep "
    "the task scoped. Read only what you need and update the handoff note "
    "with verified progress and decisions needed in the next window."
)

WRAP_UP_NOTICE = (
    f"{SYSTEM_NOTICE_PREFIX} 75% of this window's context budget is used. "
    "Finish or safely pause the current step and update the handoff note "
    "before any tool call likely to return a large result. If more work remains "
    "and a fresh window will help, confirm the note was written and call new_context."
)

HANDOFF_NOTICE = (
    f"{SYSTEM_NOTICE_PREFIX} 90% of this window's context budget is used. "
    "You must hand off any unfinished task now. Stop starting new work. "
    "Use notes_write to write or update a detailed handoff note. "
    "Choose a descriptive path if you are creating it. "
    "Include the user's goal and constraints, completed work and verification results, "
    "decisions and reasons, unverified work, blockers, and the exact next "
    "action and completion criteria. Include the current window ID and "
    "references to relevant user requests, files, and tool results. "
    "Confirm the write succeeded before calling new_context. Continue the "
    "unfinished task in the fresh window if it frees space. If the retained "
    "round still leaves no room, avoid repeated resets."
)

EXHAUSTED_TEXT = (
    f"{SYSTEM_NOTICE_PREFIX} The context is at its limit and cannot be trimmed "
    "further. Give the current result without starting new tool calls other "
    "than notes_read or history_read. If the task is unfinished, state what "
    "is verified, what remains, and what prevents continuation."
)
