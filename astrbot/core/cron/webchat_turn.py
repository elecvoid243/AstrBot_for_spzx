"""Delivery modes for scheduled (cron) jobs.

Author: elecvoid243
Date: 2026-10-08
"""

from __future__ import annotations

DELIVERY_MODE_PROACTIVE = "proactive"
"""The agent runs on its own and pushes the result to the platform."""

DELIVERY_MODE_WEBCHAT_USER_TURN = "webchat_user_turn"
"""The job's note is injected as a user message into a webchat conversation."""

DELIVERY_MODES = (DELIVERY_MODE_PROACTIVE, DELIVERY_MODE_WEBCHAT_USER_TURN)


def normalize_delivery_mode(value: object) -> str:
    """Resolve a stored cron delivery mode.

    Args:
        value: Raw payload value. Missing or empty means proactive delivery,
            which keeps jobs created before this field existed working.

    Returns:
        One of ``DELIVERY_MODES``.

    Raises:
        ValueError: The value is not a known delivery mode.
    """
    mode = str(value or "").strip() or DELIVERY_MODE_PROACTIVE
    if mode not in DELIVERY_MODES:
        raise ValueError(f"Unknown cron delivery_mode: {mode}")
    return mode


def parse_webchat_turn_session(session_str: str) -> tuple[str, str]:
    """Split a webchat unified message origin into conversation id and owner.

    Webchat sessions are built as ``webchat:FriendMessage:webchat!<owner>!<cid>``
    by ``build_webchat_unified_msg_origin``: ``<cid>`` is the raw conversation
    id the webchat queue manager keys on, ``<owner>`` is the dashboard user who
    created the session (and therefore the sender of an injected turn).

    Args:
        session_str: Unified message origin of the delivery target.

    Returns:
        ``(conversation_id, owner)``.

    Raises:
        ValueError: Not a webchat session, or the session id is not the
            composite ``webchat!<owner>!<cid>`` form.
    """
    parts = str(session_str or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "webchat":
        raise ValueError(
            "delivery_mode=webchat_user_turn requires a webchat session, got: "
            f"{session_str!r}"
        )

    session_id_parts = parts[2].split("!", 2)
    if len(session_id_parts) != 3 or session_id_parts[0] != "webchat":
        raise ValueError(
            "webchat session id must look like 'webchat!<owner>!<conversation>', "
            f"got: {parts[2]!r}"
        )

    conversation_id = session_id_parts[2]
    if not conversation_id:
        raise ValueError(
            "webchat session id must look like 'webchat!<owner>!<conversation>', "
            f"got: {parts[2]!r}"
        )
    return conversation_id, session_id_parts[1]
