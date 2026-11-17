"""Manual context window resets requested by the new_context tool."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

from astrbot.core.agent import history_archive
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.provider import Provider


class ScriptedProvider(Provider):
    """Record delivered requests and return a finite script of responses."""

    def __init__(self, responses, **config):
        super().__init__(config, {})
        self.responses = iter(responses)
        self.requests = []
        self.set_model("window-model")

    def get_current_key(self):
        return "unused"

    def set_key(self, key):
        pass

    async def get_models(self):
        return [self.get_model()]

    async def text_chat(self, **kwargs):
        self.requests.append({**kwargs, "contexts": copy.deepcopy(kwargs["contexts"])})
        return next(self.responses)

    async def text_chat_stream(self, **kwargs):
        yield await self.text_chat(**kwargs)


class EmptyToolExecutor:
    """No tool is executed; the reset is requested directly by the test."""

    async def execute(self, tool, run_context, **kwargs):
        yield CallToolResult(content=[TextContent(type="text", text="")])


class CharacterTokenCounter:
    """Give text-only fixtures exact thresholds without a real tokenizer."""

    def count_tokens(self, messages, trusted_token_usage=0):
        return sum(
            len(message.content) if isinstance(message.content, str) else 0
            for message in messages
        )


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        history_archive, "get_astrbot_agent_history_path", lambda: str(tmp_path)
    )
    return tmp_path


async def _runner(provider, contexts, **kwargs):
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=ProviderRequest(
            prompt="current task",
            contexts=contexts,
            conversation=SimpleNamespace(cid="window", token_usage=0),
            func_tool=None,
            llm_params={"max_tokens": 1},
        ),
        run_context=ContextWrapper(context=None),
        tool_executor=EmptyToolExecutor(),
        agent_hooks=BaseAgentRunHooks(),
        custom_token_counter=CharacterTokenCounter(),
        **kwargs,
    )
    return runner


@pytest.mark.asyncio
async def test_manual_reset_drops_older_rounds_and_keeps_the_current_one(
    archive_root,
):
    provider = ScriptedProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    runner = await _runner(
        provider,
        [
            {"role": "user", "content": "old history " * 60},
            {"role": "assistant", "content": "old answer " * 60},
        ],
        context_limit_reached_strategy="token_budget",
    )

    runner.request_new_context_window()
    _ = [event async for event in runner.step_until_done(2)]
    await asyncio.gather(*runner._archive_tasks)

    assert runner.window_state.seq == 1
    assert runner.window_state.prev_id
    delivered = provider.requests[0]["contexts"]
    assert all("old history" not in str(message.content) for message in delivered)
    assert any("current task" in str(message.content) for message in delivered)
    assert any("<context_window>" in str(message.content) for message in delivered)
    assert len(history_archive.list_windows(runner.req.session_id)) == 1


@pytest.mark.asyncio
async def test_manual_reset_is_consumed_once(archive_root):
    provider = ScriptedProvider(
        [LLMResponse(role="assistant", completion_text="done")],
        max_context_tokens=1000,
    )
    runner = await _runner(
        provider,
        [{"role": "user", "content": "old history " * 60}],
        context_limit_reached_strategy="token_budget",
    )

    runner.request_new_context_window()
    runner.request_new_context_window()
    _ = [event async for event in runner.step_until_done(2)]

    assert runner.window_state.seq == 1
