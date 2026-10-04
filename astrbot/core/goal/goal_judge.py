# Author: elecvoid243 · Created: 2026-07-25
"""Judge prompts, continuation prompts, and judge-response parsing.

Stdlib-only. Prompt contracts mirror Hermes `hermes_cli/goals.py`, which
has production-hardened these templates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

DEFAULT_JUDGE_MAX_TOKENS = 4096
JUDGE_RESPONSE_SNIPPET_CHARS = 4000

CONTINUATION_PROMPT_TEMPLATE = (
    "[继续推进你的常驻目标]\n"
    "目标：{goal}\n\n"
    "请继续朝这个目标工作，采取下一个具体步骤。"
    "当目标完成时，必须调用 goal_done 工具并在 reason 中写明完成证据；"
    "当被阻塞或需要用户输入时，必须调用 goal_blocked 工具并写明原因。"
    "不要只在回复正文里口头声称完成。"
)

CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "[继续推进你的常驻目标]\n"
    "目标：{goal}\n\n"
    "用户在循环中途追加的额外标准：\n"
    "{subgoals_block}\n\n"
    "请继续朝目标以及全部额外标准工作，采取下一个具体步骤。"
    "当目标和每条额外标准都完成时，必须调用 goal_done 工具，"
    "reason 中逐条写明证据；被阻塞或需要用户输入时调用 goal_blocked。"
    "不要只在回复正文里口头声称完成。"
)

JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge evaluating whether an autonomous agent has "
    "achieved a user's stated goal. You receive the goal text and the "
    "agent's most recent response. Your only job is to decide whether "
    "the goal is fully satisfied based on that response.\n\n"
    "A goal is DONE only when:\n"
    "- The response explicitly confirms the goal was completed, OR\n"
    "- The response clearly shows the final deliverable was produced, OR\n"
    "- The response explains the goal is unachievable / blocked / needs "
    "user input (treat this as DONE with reason describing the block).\n\n"
    "Otherwise the goal is NOT done — CONTINUE.\n\n"
    "Reply ONLY with a single JSON object on one line. Do NOT output any "
    "thinking, reasoning, tool-call traces, explanations, Markdown fences, "
    "or anything outside the JSON object:\n"
    '{"done": <true|false>, "reason": "<one-sentence rationale>"}'
)

JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Is the goal satisfied?"
)

JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Additional criteria the user added mid-loop (all must also be "
    "satisfied for the goal to be DONE):\n{subgoals_block}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Decision: For each numbered criterion above, find concrete "
    "evidence in the agent's response that the criterion is "
    "satisfied. Do not accept generic phrases like 'all requirements "
    "met' — require specific evidence. If ANY criterion lacks "
    "specific evidence, return CONTINUE.\n\n"
    "Is the goal AND every additional criterion satisfied?"
)

_LEAK_BLOCK_TAG_RE = re.compile(
    r"<(?P<tag>think|thinking|thought|thoughts|reasoning|analysis|"
    r"tool_call|tool_calls|tool_call_result|tool_call_results|tool_result)"
    r"(?:\s[^>]*)?>.*?</(?P=tag)>",
    re.DOTALL | re.IGNORECASE,
)
_LEAK_ORPHAN_TAG_RE = re.compile(
    r"</?(?:think|thinking|thought|thoughts|reasoning|analysis|"
    r"tool_call|tool_calls|tool_call_result|tool_call_results|tool_result)"
    r"(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_CODE_FENCE_RE = re.compile(r"````*[a-zA-Z]*\s*\n?(.*?)````*", re.DOTALL)

JUDGE_PARSE_FAILURE_REASON = "judge 输出无法解析（原文仅记录于日志）"
"""Reason used when the judge reply is not valid JSON; the raw text is never
shown to the user so thinking/tool-call traces cannot leak into chat."""

_TAIL_LEAK_TAG = "</think>"


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "… [truncated]"


def _strip_markup(text: str) -> str:
    """Remove code fences and thinking/tool-call trace tags from judge output.

    Judge models frequently wrap their reasoning in ``<think>`` / ``<thinking>``
    blocks or emit tool-call logs. Those are internal traces, never part of
    the JSON verdict, and stripping them before parsing keeps them out of the
    user-facing reason message.

    Args:
        text: Raw judge output.

    Returns:
        Text with fenced blocks unwrapped and leak tags removed.
    """
    text = _CODE_FENCE_RE.sub(r"\1", text)
    text = _LEAK_BLOCK_TAG_RE.sub("", text)
    text = _LEAK_ORPHAN_TAG_RE.sub("", text)
    # Models sometimes emit a dangling ``</think>`` after the JSON.
    if text.rstrip().endswith(_TAIL_LEAK_TAG):
        text = text.rstrip()[: -len(_TAIL_LEAK_TAG)].strip()
    return text.strip()


def _extract_json_object(text: str) -> dict | None:
    """Locate the first syntactically complete JSON object in ``text``.

    Unlike a non-greedy ``\\{.*?\\}`` regex (which can match a brace pair
    inside a <think> block), this scans every ``{`` and asks the JSON decoder
    to validate the whole object, so a stray ``{done}`` inside thinking text
    is skipped and the real verdict object is found.

    Args:
        text: Judge output (already stripped of markup).

    Returns:
        The first parsed dict, or None when no valid JSON object exists.
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _sanitize_reason(reason: str, limit: int = 200) -> str:
    """Strip trace markup and normalize whitespace in a judge reason.

    Args:
        reason: Raw reason from the judge verdict.
        limit: Maximum length of the sanitized reason.

    Returns:
        Sanitized one-line reason (may be empty).
    """
    cleaned = _LEAK_BLOCK_TAG_RE.sub("", reason)
    cleaned = _LEAK_ORPHAN_TAG_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _truncate(cleaned, limit)


def parse_judge_response(raw: str) -> tuple[bool, str, bool]:
    """Parse the judge's reply, fail-open to (False, reason, parse_failed).

    Never leaks the raw model output into the returned reason: on parse
    failure a fixed summary is returned and the raw text is only meant for
    logging (see :data:`JUDGE_PARSE_FAILURE_REASON`).

    Args:
        raw: Raw text returned by the judge model.

    Returns:
        Tuple of (done, reason, parse_failed). parse_failed is True when
        the output could not be interpreted as the expected JSON verdict.
    """
    if not raw:
        return False, "judge returned empty response", True
    text = _strip_markup(raw.strip())
    data = None
    try:
        data = json.loads(text)
    except Exception:
        data = _extract_json_object(text)
    if not isinstance(data, dict):
        return False, JUDGE_PARSE_FAILURE_REASON, True
    done_val = data.get("done")
    if isinstance(done_val, str):
        done = done_val.strip().lower() in {"true", "yes", "1", "done"}
    else:
        done = bool(done_val)
    reason = _sanitize_reason(
        str(data.get("reason") or "").strip() or "no reason provided"
    )
    return done, reason, False


def build_continuation_prompt(goal: str, subgoals: list[str] | None) -> str:
    """Build the user-role message fed back into the session."""
    clean = [s.strip() for s in (subgoals or []) if s and s.strip()]
    if clean:
        block = "\n".join(f"- {i}. {t}" for i, t in enumerate(clean, 1))
        return CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=goal, subgoals_block=block
        )
    return CONTINUATION_PROMPT_TEMPLATE.format(goal=goal)


def build_judge_messages(
    goal: str, response: str, subgoals: list[str] | None, now_str: str
) -> list[dict]:
    """Build the [system, user] message pair for the judge call."""
    clean = [s.strip() for s in (subgoals or []) if s and s.strip()]
    if clean:
        block = "\n".join(f"- {i}. {t}" for i, t in enumerate(clean, 1))
        user = JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            subgoals_block=_truncate(block, 2000),
            response=_truncate(response, JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=now_str,
        )
    else:
        user = JUDGE_USER_PROMPT_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            response=_truncate(response, JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=now_str,
        )
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


async def judge_goal(
    *,
    llm_caller: Callable[[str, str], Awaitable[str | None]],
    goal: str,
    last_response: str,
    subgoals: list[str] | None = None,
) -> tuple[str, str, bool, bool]:
    """Ask the judge model whether the goal is satisfied.

    Deliberately fail-open on the verdict: any transport error returns
    ("continue", ..., False, True) so a broken judge never wedges the loop —
    the consecutive-transport-failure pause in GoalManager is the guard.

    Args:
        llm_caller: Async callable (system_prompt, user_prompt) -> raw text,
            returning None on transport/API errors.
        goal: The standing goal text.
        last_response: The agent's final assistant text for this turn.
        subgoals: Optional user-added criteria (from /subgoal).

    Returns:
        Tuple of (verdict, reason, parse_failed, transport_failed) where
        verdict is "done" | "continue" | "skipped".
    """
    if not goal.strip():
        return "skipped", "empty goal", False, False
    if not last_response.strip():
        return "continue", "empty response (nothing to evaluate)", False, False

    now_str = (
        datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    )
    messages = build_judge_messages(goal, last_response, subgoals, now_str)
    raw = await llm_caller(messages[0]["content"], messages[1]["content"])
    if raw is None:
        return "continue", "judge unavailable (transport error)", False, True

    done, reason, parse_failed = parse_judge_response(raw)
    return ("done" if done else "continue"), reason, parse_failed, False
