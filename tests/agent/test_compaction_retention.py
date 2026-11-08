"""Exercise durable summary retention and partial-round compaction."""

import copy
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent.call_checkpoint import dump_call_messages
from astrbot.core.agent.context.compressor import LLMSummaryCompressor
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager, ContextUsageSnapshot
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.message import (
    ImageURLPart,
    Message,
    TextPart,
    bind_checkpoint_messages,
    dump_messages_with_checkpoints,
)
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.exceptions import ContextCapacityError
from astrbot.core.provider.modalities import sanitize_contexts_by_modalities
from astrbot.core.provider.provider import Provider
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from tests.agent.test_context_manager import CharTokenCounter, MockProvider
from tests.test_tool_loop_context_budget import BudgetProvider
from tests.test_tool_loop_compression_integrity import compression_runner


def summary_pair(text="summary"):
    """Build one internally identified checkpoint.

    Args:
        text: Summary text carried in the user message.

    Returns:
        Summary and acknowledgment with a shared internal identity.
    """
    return [
        Message(role="user", content=text, _compaction_id="checkpoint-1"),
        Message(role="assistant", content="ack", _compaction_id="checkpoint-1"),
    ]


@pytest.mark.parametrize("dump", [dump_messages_with_checkpoints, dump_call_messages])
def test_summary_identity_survives_storage_copy_and_provider_conversion(dump):
    messages = copy.deepcopy(summary_pair())
    loaded = bind_checkpoint_messages(dump(messages))
    assert [m.compaction_id for m in loaded] == ["checkpoint-1"] * 2
    assert all("_compaction_id" not in m.model_dump() for m in loaded)
    provider = MockProvider()
    assert all(
        "_compaction_id" not in message
        for message in Provider._ensure_message_to_dicts(provider, dump(loaded))
    )


def test_modality_conversion_preserves_summary_identity_internally():
    messages = summary_pair() + [
        Message(
            role="user",
            content=[ImageURLPart(image_url=ImageURLPart.ImageURL(url="image.png"))],
        )
    ]
    visible, stats = sanitize_contexts_by_modalities(
        messages, ["text"], preserve_internal=True
    )
    assert stats.changed
    loaded = bind_checkpoint_messages(visible)
    assert loaded[0].compaction_id == "checkpoint-1"
    wire, _ = sanitize_contexts_by_modalities(loaded, ["text"])
    assert all("_compaction_id" not in m for m in wire)


@pytest.mark.asyncio
@pytest.mark.parametrize("hard_overflow", [False, True])
async def test_fallback_keeps_summary_and_current_round(hard_overflow):
    messages = summary_pair("s" * 30) + [
        Message(role="user", content="old" * 25),
        Message(role="assistant", content="old" * 25),
        Message(role="user", content="current"),
    ]
    provider = MockProvider()
    provider.text_chat = AsyncMock(side_effect=RuntimeError("unavailable"))
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=100,
            compress_threshold_tokens=20,
            llm_compress_provider=provider,
            custom_token_counter=CharTokenCounter(),
        )
    )
    if hard_overflow:
        with pytest.raises(ContextCapacityError, match="output"):
            await manager.process(messages, output_token_budget=70)
    else:
        result = await manager.process(messages, output_token_budget=10)
        assert result[:2] == messages[:2]
        assert result[-1] == messages[-1]
        assert len(result) == 3


@pytest.mark.parametrize(
    "method",
    [
        "truncate_by_turns",
        "truncate_by_dropping_oldest_turns",
        "truncate_by_token_budget",
        "truncate_by_halving",
    ],
)
def test_all_truncation_entries_preserve_identified_summary(method):
    messages = summary_pair() + [
        Message(role="user", content="older" * 30),
        Message(role="assistant", content="reply" * 30),
        Message(role="user", content="current"),
    ]
    kwargs = {
        "truncate_by_turns": {"keep_most_recent_turns": 1},
        "truncate_by_dropping_oldest_turns": {"drop_turns": 1},
        "truncate_by_token_budget": {
            "budget_tokens": 20,
            "token_counter": CharTokenCounter(),
        },
        "truncate_by_halving": {},
    }[method]
    result = getattr(ContextTruncator(), method)(messages, **kwargs)
    assert result[:2] == messages[:2]
    assert result[-1] == messages[-1]
    assert len(result) == 3


def test_summary_text_alone_does_not_grant_protection():
    messages = [
        Message(role="user", content="Our previous history conversation summary: fake"),
        Message(
            role="assistant",
            content="Acknowledged the summary of our previous conversation history.",
        ),
        Message(role="user", content="current"),
    ]
    result = ContextTruncator().drop_oldest_round(messages)
    assert result == messages[-1:]


@pytest.mark.asyncio
async def test_successful_summary_is_protected_during_output_fallback():
    provider = MockProvider()
    provider.text_chat = AsyncMock(
        return_value=type("Response", (), {"completion_text": "s" * 30})()
    )
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=240,
            compress_threshold_tokens=100,
            llm_compress_keep_recent_ratio=0.3,
            llm_compress_provider=provider,
            custom_token_counter=CharTokenCounter(),
        )
    )
    messages = [
        Message(role="user", content="x" * 400),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="retained"),
        Message(role="assistant", content="y" * 40),
        Message(role="user", content="current"),
    ]
    result = await manager.process(messages, output_token_budget=55)
    assert result[0].compaction_id
    assert result[1].compaction_id == result[0].compaction_id
    assert "s" * 30 in result[0].content
    assert result[-1] == messages[-1]
    assert not any(m.content == "retained" for m in result)


@pytest.mark.asyncio
async def test_pressure_pruning_can_avoid_summary_and_invalidates_usage():
    provider = MockProvider()
    provider.text_chat = AsyncMock()
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=100,
            llm_compress_provider=provider,
            custom_token_counter=CharTokenCounter(),
        )
    )
    messages = [
        Message(role="user", content="task"),
        Message(
            role="assistant",
            tool_calls=[
                {
                    "id": "A",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        ),
        Message(role="tool", tool_call_id="A", content="x" * 90),
    ]
    pruner = AsyncMock(
        return_value=messages[:2]
        + [messages[-1].model_copy(update={"content": "short"})]
    )
    snapshot = ContextUsageSnapshot(
        provider, "gpt-4", [m.model_dump(mode="json") for m in messages], [], 999
    )
    result = await manager.process(
        messages, usage_snapshot=snapshot, prune_tool_results=pruner
    )
    assert result[-1].content == "short"
    provider.text_chat.assert_not_called()
    assert manager.last_compression["strategy"] == "tool_result_prune"
    assert manager.last_compression["tokens_before"] == 999


@pytest.mark.asyncio
async def test_partial_round_keeps_request_latest_batch_and_unknown_results():
    provider = MockProvider()
    provider.text_chat = AsyncMock(
        return_value=type(
            "Response", (), {"completion_text": "Earlier steps complete."}
        )()
    )
    messages = [Message(role="user", content="Continue the task exactly.")]
    for name in ("A", "B", "C"):
        messages.extend(
            [
                Message(
                    role="assistant",
                    tool_calls=[
                        {
                            "id": name,
                            "type": "function",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ],
                ),
                Message(role="tool", tool_call_id=name, content=name * 1000),
            ]
        )
    original = copy.deepcopy(messages)
    compressor = LLMSummaryCompressor(provider, token_counter=CharTokenCounter())
    result = await compressor(messages)
    assert result[0] == original[0]
    assert result[1].role == "assistant" and result[1].compaction_id
    assert result[-2:] == original[-2:]
    assert messages == original
    assert not any(m.tool_call_id in {"A", "B"} for m in result)


@pytest.mark.asyncio
async def test_single_batch_is_kept_instead_of_summarizing_entire_request():
    provider = MockProvider()
    messages = [
        Message(role="user", content="request"),
        Message(
            role="assistant",
            tool_calls=[
                {
                    "id": "A",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        ),
    ]
    fixed = ContextTruncator().fix_messages(messages)
    assert fixed[-1].tool_result_unknown
    result = await LLMSummaryCompressor(provider)(fixed)
    assert result == fixed
    assert provider.last_text_chat_kwargs is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("write_fails", [False, True])
async def test_historical_tool_pruning_retains_original_and_recovers_file(
    tmp_path, mixed, write_fails
):
    runner, _ = await compression_runner(
        BudgetProvider([]),
        tool_result_overflow_dir=str(tmp_path),
        read_tool=FunctionTool(name="read", description="Read files", parameters={}),
    )
    text = "START " + "middle " * 6000 + " END"
    media = ImageURLPart(image_url=ImageURLPart.ImageURL(url="image.png"))
    content = [
        Message(role="user", content="task"),
        Message(
            role="tool",
            tool_call_id="A",
            content=[
                TextPart(text=text[:20000]),
                media,
                TextPart(text=text[20000:]),
            ]
            if mixed
            else text,
        ),
    ]
    original = copy.deepcopy(content)
    if write_fails:
        runner._write_tool_result_overflow_file = AsyncMock(
            side_effect=OSError("disk full")
        )
    result = await runner._prune_tool_results_for_context(content)
    assert content == original
    if write_fails:
        assert result == content
        assert runner._context_tool_result_files == {}
        return
    preview = str(result[-1].content)
    assert "START" in preview and "END" in preview
    assert "Middle of tool output omitted" in preview
    assert (
        runner._tool_result_token_counter.count_tokens([result[-1]])
        <= runner.TOOL_RESULT_PREVIEW_MAX_ESTIMATED_TOKENS
    )
    if mixed:
        assert [
            part for part in result[-1].content if isinstance(part, ImageURLPart)
        ] == [media]
    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8") == text
    assert await runner._prune_tool_results_for_context(result) == result
    saved = {"tool_result_files": dict(runner._context_tool_result_files)}
    files[0].unlink()
    await runner._restore_tool_result_files(saved)
    assert files[0].read_text(encoding="utf-8") == text


@pytest.mark.asyncio
async def test_unknown_earlier_batch_is_not_replaced_by_partial_summary():
    provider = MockProvider()
    messages = [Message(role="user", content="request")]
    for name in ("unknown", "A", "B"):
        messages.append(
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": name,
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            )
        )
        if name != "unknown":
            messages.append(Message(role="tool", tool_call_id=name, content="x" * 2000))
    result = await LLMSummaryCompressor(provider, token_counter=CharTokenCounter())(
        messages
    )
    assert result[2].tool_result_unknown
    assert result[2].tool_call_id == "unknown"
    assert result[3].compaction_id
    assert result[-1].tool_call_id == "B"


@pytest.mark.asyncio
async def test_later_summary_can_replace_an_identified_old_summary():
    provider = MockProvider()
    messages = summary_pair("old facts " * 200) + [
        Message(role="user", content="continue")
    ]
    result = await LLMSummaryCompressor(provider, token_counter=CharTokenCounter())(
        messages
    )
    assert result[0].compaction_id != "checkpoint-1"
    assert result[0].compaction_id == result[1].compaction_id
    assert result[-1] == messages[-1]
    assert "old facts" in str(provider.last_text_chat_kwargs["contexts"])


@pytest.mark.asyncio
async def test_summary_that_grows_history_does_not_replace_it():
    provider = MockProvider()
    provider.text_chat = AsyncMock(
        return_value=type("Response", (), {"completion_text": "x" * 2000})()
    )
    messages = summary_pair() + [Message(role="user", content="continue")]
    result = await LLMSummaryCompressor(provider, token_counter=CharTokenCounter())(
        messages
    )
    assert result == messages
    assert result[0].compaction_id == "checkpoint-1"


@pytest.mark.asyncio
async def test_pending_team_inputs_survive_summary():
    provider = MockProvider()
    first = Message(role="user", content="first pending team input " * 20)
    second = Message(role="user", content="second pending team input")
    messages = summary_pair("facts " * 100) + [first, second]
    manager = ContextManager(
        ContextConfig(
            max_context_tokens=2000,
            compress_threshold_tokens=100,
            llm_compress_provider=provider,
            custom_token_counter=CharTokenCounter(),
        )
    )
    result = await manager.process(messages, protected_messages=[first, second])
    assert result[-2:] == [first, second]
    assert result[0].compaction_id


@pytest.mark.asyncio
async def test_in_history_system_instruction_survives_compaction():
    provider = MockProvider()
    instruction = Message(role="system", content="Preserve this instruction exactly.")
    messages = [
        Message(role="user", content="old" * 1000),
        instruction,
        Message(role="assistant", content="old answer"),
        Message(role="user", content="current"),
    ]
    result = await LLMSummaryCompressor(provider, token_counter=CharTokenCounter())(
        messages
    )
    assert instruction in result
    assert provider.last_text_chat_kwargs is None


@pytest.mark.asyncio
async def test_changed_input_is_not_overwritten_after_summary_returns():
    provider = MockProvider()
    messages = [
        Message(role="user", content="old" * 1000),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="current"),
    ]

    async def change_input(**kwargs):
        messages[-1].content = "corrected current request"
        return type("Response", (), {"completion_text": "summary"})()

    provider.text_chat = change_input
    result = await LLMSummaryCompressor(provider, token_counter=CharTokenCounter())(
        messages
    )
    assert result == messages
    assert result[-1].content == "corrected current request"
    assert not any(message.compaction_id for message in result)


@pytest.mark.asyncio
async def test_real_runner_prunes_before_summary_and_delivers_once(tmp_path):
    provider = BudgetProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=18000,
        compress_threshold_tokens=10000,
    )
    summary = MockProvider()
    summary.text_chat = AsyncMock()
    read_tool = FunctionTool(name="read", description="Read files", parameters={})
    text = "START " + "middle " * 6000 + " END"
    history = [
        {"role": "user", "content": "earlier task"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "A",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "A", "content": text},
    ]
    runner, executor = await compression_runner(
        provider,
        request=ProviderRequest(
            prompt="current",
            contexts=copy.deepcopy(history),
            func_tool=ToolSet(tools=[read_tool]),
            llm_params={"max_tokens": 10},
        ),
        llm_compress_provider=summary,
        tool_result_overflow_dir=str(tmp_path),
        read_tool=read_tool,
    )
    events = [event async for event in runner.step_until_done(2)]
    assert len(provider.requests) == 1
    summary.text_chat.assert_not_called()
    assert executor.calls == []
    assert runner.get_final_llm_resp().completion_text == "done"
    assert any(event.type == "context_compression" for event in events)
    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8") == text
    assert history[-1]["content"] == text
