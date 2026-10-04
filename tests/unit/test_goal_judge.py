"""Tests for judge prompt building and response parsing (ported from the
astrbot_plugin_goal plugin)."""

import pytest

from astrbot.core.goal.goal_judge import (
    build_continuation_prompt,
    build_judge_messages,
    judge_goal,
    parse_judge_response,
)

pytestmark = pytest.mark.asyncio


def test_parse_plain_json():
    done, reason, failed = parse_judge_response('{"done": true, "reason": "ok"}')
    assert (done, reason, failed) == (True, "ok", False)


def test_parse_fenced_json():
    raw = '```json\n{"done": false, "reason": "not yet"}\n```'
    done, reason, failed = parse_judge_response(raw)
    assert (done, failed) == (False, False)
    assert reason == "not yet"


def test_parse_prose_wrapped_json():
    raw = 'Sure! {"done": "true", "reason": "finished"} hope that helps'
    done, reason, failed = parse_judge_response(raw)
    assert done is True and failed is False


def test_parse_empty_and_garbage():
    assert parse_judge_response("") == (False, "judge returned empty response", True)
    _, _, failed = parse_judge_response("I think the goal is done")
    assert failed is True


def test_build_continuation_prompt_plain():
    p = build_continuation_prompt("write report", None)
    assert "write report" in p
    assert "额外标准" not in p


def test_build_continuation_prompt_with_subgoals():
    p = build_continuation_prompt("g", ["a", "b"])
    assert "- 1. a" in p and "- 2. b" in p


def test_build_judge_messages_variants():
    msgs = build_judge_messages("g", "resp", None, "2026-07-25 00:00:00 CST")
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    assert "Additional criteria" not in msgs[1]["content"]
    msgs2 = build_judge_messages("g", "resp", ["crit"], "now")
    assert "crit" in msgs2[1]["content"]


async def _caller_ok(system, user):
    return '{"done": true, "reason": "all finished"}'


async def _caller_transport_error(system, user):
    return None


async def _caller_garbage(system, user):
    return "I cannot decide"


async def test_judge_goal_done():
    verdict, reason, parse_failed, transport_failed = await judge_goal(
        llm_caller=_caller_ok, goal="g", last_response="resp"
    )
    assert (verdict, parse_failed, transport_failed) == ("done", False, False)
    assert reason == "all finished"


async def test_judge_goal_transport_error_fails_open():
    verdict, _, parse_failed, transport_failed = await judge_goal(
        llm_caller=_caller_transport_error, goal="g", last_response="resp"
    )
    assert verdict == "continue"
    assert parse_failed is False  # transport errors must NOT count as parse failures
    assert transport_failed is True


async def test_judge_goal_garbage_counts_parse_failure():
    verdict, _, parse_failed, transport_failed = await judge_goal(
        llm_caller=_caller_garbage, goal="g", last_response="resp"
    )
    assert verdict == "continue"
    assert parse_failed is True
    assert transport_failed is False


async def test_judge_goal_skips_empty_inputs():
    verdict, _, parse_failed, transport_failed = await judge_goal(
        llm_caller=_caller_ok, goal="  ", last_response="resp"
    )
    assert (verdict, parse_failed, transport_failed) == ("skipped", False, False)


def test_parse_strips_leading_think_tags():
    raw = '<think>评估中</think>\n{"done": false, "reason": "not yet"}'
    done, reason, failed = parse_judge_response(raw)
    assert (done, reason, failed) == (False, "not yet", False)


def test_parse_think_braces_do_not_misgrab():
    raw = '<think>需要判断 {done} 后回复</think>\n{"done": true, "reason": "finished"}'
    done, reason, failed = parse_judge_response(raw)
    assert (done, reason, failed) == (True, "finished", False)


def test_parse_strips_thinking_variant_and_orphan_tags():
    raw = '<thinking>notes</thinking>\n</think>\n{"done": true, "reason": "ok"}'
    done, reason, failed = parse_judge_response(raw)
    assert (done, reason, failed) == (True, "ok", False)


def test_parse_failure_reason_does_not_leak_raw_text():
    raw = '<think>整个思考内容\n<tool_call>foo</tool_call>\n{"done": 真假}</think>'
    done, reason, failed = parse_judge_response(raw)
    assert done is False and failed is True
    assert raw not in reason
    assert "<think>" not in reason
    assert "<tool_call>" not in reason


def test_parse_sanitizes_reason_field():
    raw = (
        '{"done": false, "reason": "经分析 <think>还在想</think> 并 '
        '<tool_call>ls</tool_call> 后仍未完成"}'
    )
    done, reason, failed = parse_judge_response(raw)
    assert (done, failed) == (False, False)
    assert "<think>" not in reason
    assert "<tool_call>" not in reason
    assert "还在想" not in reason
    assert "未完成" in reason
