"""Tests for subagent context inherit modes (normal / fork / auto)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.agent.agent import Agent
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import Message
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderRequest,
    TokenUsage,
)
from astrbot.core.provider.provider import Provider
from astrbot.core.star.context import Context
from astrbot.core.subagent_manager import SubAgentManager

UMO = "test_platform:private:s1"


class _FakeMainRunner:
    """Minimal stand-in for the main agent runner stored in agent context extra."""

    def __init__(self, toolset, schema_mode="full", provider=None):
        self.req = MagicMock()
        self.req.func_tool = toolset
        self.tool_schema_mode = schema_mode
        self.effective_raw_tool_set = toolset
        self.provider = provider


def make_handoff_tool(name="researcher", instructions="You are a researcher."):
    agent = Agent(name=name, instructions=instructions, tools=[])
    return HandoffTool(agent=agent, tool_description=f"Delegate to {name}")


def make_run_context(mock_ctx, mock_event, messages, extra=None):
    agent_context = AstrAgentContext(
        context=mock_ctx, event=mock_event, extra=extra or {}
    )
    return ContextWrapper(context=agent_context, messages=messages)


def make_main_messages():
    return [
        Message(role="system", content="MAIN SYSTEM PROMPT"),
        Message(role="user", content="hello main"),
        Message(role="assistant", content="hi"),
    ]


@pytest.fixture(autouse=True)
def restore_manager_state():
    """Snapshot and restore SubAgentManager class-level state around each test."""
    mode = SubAgentManager._context_inherit_mode
    sessions = dict(SubAgentManager._sessions)
    SubAgentManager._sessions.clear()
    SubAgentManager._context_inherit_mode = "normal"
    # Avoid creating workspace directories from prompts built during tests.
    prev = SubAgentManager._build_workdir_prompt
    SubAgentManager._build_workdir_prompt = classmethod(
        lambda cls, session_id, agent_name=None: ""
    )
    yield
    SubAgentManager._context_inherit_mode = mode
    SubAgentManager._sessions.clear()
    SubAgentManager._sessions.update(sessions)
    SubAgentManager._build_workdir_prompt = prev


@pytest.fixture
def mock_event():
    event = MagicMock(spec=AstrMessageEvent)
    event.unified_msg_origin = UMO
    event.get_platform_name.return_value = "test_platform"
    event.get_sender_name.return_value = "tester"
    event.trace = None
    message_obj = MagicMock()
    message_obj.message = []
    event.message_obj = message_obj
    return event


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.provider_config = {"id": "provider-1", "max_context_tokens": 0}
    return provider


@pytest.fixture
def mock_ctx(mock_event, mock_provider):
    ctx = MagicMock(spec=Context)
    ctx.get_config.return_value = {"provider_settings": {}}
    ctx.get_current_chat_provider_id = AsyncMock(return_value="provider-1")
    provider_manager = MagicMock()
    provider_manager.get_provider_by_id = AsyncMock(return_value=mock_provider)
    ctx.provider_manager = provider_manager
    ctx.tool_loop_agent = AsyncMock(
        return_value=LLMResponse(role="assistant", completion_text="ok")
    )
    return ctx


async def run_handoff(handoff_tool, run_context, task_input="do research"):
    return [
        result
        async for result in FunctionToolExecutor._execute_handoff(
            handoff_tool, run_context, input=task_input
        )
    ]


def get_tool_loop_agent_kwargs(mock_ctx):
    mock_ctx.tool_loop_agent.assert_awaited_once()
    return mock_ctx.tool_loop_agent.call_args.kwargs


@pytest.mark.asyncio
async def test_fork_mode_inherits_main_context(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "fork"

    main_messages = make_main_messages()
    toolset = ToolSet()
    toolset.add_tool(
        FunctionTool(
            name="main_tool",
            description="d",
            parameters={"type": "object", "properties": {}},
        )
    )
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        main_messages,
        extra={"main_agent_runner": _FakeMainRunner(toolset)},
    )

    results = await run_handoff(make_handoff_tool(), run_context)
    assert results[-1].content[0].text == "ok"

    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    # Byte-identical inheritance: contexts are dumps of the main agent's
    # messages, and no extra system prompt is inserted (main system message is
    # already at contexts[0]).
    assert kwargs["contexts"] == [msg.model_dump() for msg in main_messages]
    assert kwargs["system_prompt"] == ""
    assert kwargs["tools"] is toolset
    assert kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")
    assert "# Role" in kwargs["prompt"]
    assert "do research" in kwargs["prompt"]
    # The subagent's agent context is marked for permission isolation.
    assert kwargs["agent_context"].extra["is_subagent"] is True
    assert kwargs["agent_context"].extra["subagent_name"] == "researcher"


@pytest.mark.asyncio
async def test_fork_mode_inherits_main_llm_params(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "fork"

    main_runner = _FakeMainRunner(ToolSet())
    main_runner.req.llm_params = {"thinking_effort": "high"}
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": main_runner},
    )

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    # The main agent's per-request LLM params ride along so provider fields
    # derived from them (e.g. reasoning_effort) stay part of the cached prefix.
    assert kwargs["llm_params"] == {"thinking_effort": "high"}


@pytest.mark.asyncio
async def test_fork_mode_without_llm_params_omits_key(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "fork"

    run_context = make_run_context(mock_ctx, mock_event, make_main_messages())

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    # No main runner / no params -> the key is not passed at all, so the
    # request payload stays unchanged from the pre-inheritance behavior.
    assert "llm_params" not in kwargs


@pytest.mark.asyncio
async def test_fork_mode_falls_back_without_main_runner(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "fork"

    run_context = make_run_context(mock_ctx, mock_event, make_main_messages())

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["contexts"] == [msg.model_dump() for msg in run_context.messages]
    # Fallback toolset construction path (agent has no tools configured).
    assert kwargs["tools"] is None


@pytest.mark.asyncio
async def test_normal_mode_keeps_existing_behavior(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "normal"

    toolset = ToolSet()
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": _FakeMainRunner(toolset)},
    )

    results = await run_handoff(make_handoff_tool(), run_context)
    assert results[-1].content[0].text == "ok"

    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["system_prompt"].startswith("# Role")
    assert kwargs["prompt"] == "do research"
    assert kwargs["contexts"] is None
    assert kwargs["tools"] is not toolset
    # llm_params inheritance is fork-only: normal-mode payloads are unchanged.
    assert "llm_params" not in kwargs
    assert kwargs["agent_context"].extra["is_subagent"] is True


@pytest.mark.asyncio
async def test_auto_mode_forks_below_threshold(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "auto"
    mock_ctx.provider_manager.get_provider_by_id.return_value.provider_config[
        "max_context_tokens"
    ] = 1_000_000

    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": _FakeMainRunner(ToolSet())},
    )

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")
    assert kwargs["system_prompt"] == ""


@pytest.mark.asyncio
async def test_auto_mode_uses_normal_above_threshold(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "auto"
    mock_ctx.provider_manager.get_provider_by_id.return_value.provider_config[
        "max_context_tokens"
    ] = 5

    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": _FakeMainRunner(ToolSet())},
    )

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["system_prompt"].startswith("# Role")
    assert kwargs["prompt"] == "do research"


@pytest.mark.asyncio
async def test_auto_mode_forks_without_token_limit(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "auto"
    mock_ctx.provider_manager.get_provider_by_id.return_value.provider_config[
        "max_context_tokens"
    ] = 0

    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": _FakeMainRunner(ToolSet())},
    )

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")


@pytest.mark.asyncio
async def test_auto_mode_band_between_fork_line_and_compression_trigger_uses_normal(
    mock_ctx, mock_event
):
    """Prefix usage between the 0.4 auto-fork line and the 0.82
    compression trigger must fall back to normal.

    Regression for the old 0.82 line: a prefix just below the
    compression trigger left the subagent almost no run headroom — its
    own steps (tool results, appended instructions) pushed it over the
    trigger within a step or two, losing the cache benefit AND the
    inherited context, which is strictly worse than a clean normal
    start.
    """
    SubAgentManager._context_inherit_mode = "auto"
    agent_name, sys_prompt, task = "researcher", "You are a researcher.", "do research"

    run_context = make_run_context(mock_ctx, mock_event, make_main_messages())

    # Reference estimate of exactly what the resolver measures: main
    # messages plus the fork prompt it would send.
    fork_prompt = FunctionToolExecutor._build_fork_prompt(agent_name, sys_prompt, task)
    estimate = EstimateTokenCounter().count_tokens(
        list(run_context.messages) + [Message(role="user", content=fork_prompt)]
    )

    provider = mock_ctx.provider_manager.get_provider_by_id.return_value

    # ~60% of the limit: below the 0.82 compression trigger (old line
    # chose fork here) but above the 0.4 auto-fork line.
    provider.provider_config["max_context_tokens"] = int(estimate / 0.6)
    mode = await FunctionToolExecutor._resolve_auto_inherit_mode(
        run_context, mock_ctx, "provider-1", agent_name, sys_prompt, task
    )
    assert mode == "normal"

    # ~1/3 of the limit: comfortably below the 0.4 auto-fork line.
    provider.provider_config["max_context_tokens"] = estimate * 3
    mode = await FunctionToolExecutor._resolve_auto_inherit_mode(
        run_context, mock_ctx, "provider-1", agent_name, sys_prompt, task
    )
    assert mode == "fork"


@pytest.mark.asyncio
async def test_fork_mode_falls_back_to_normal_on_provider_mismatch(
    mock_ctx, mock_event
):
    """Fork on a different provider can never hit the prefix cache.

    The delegated subagent's provider (handoff.provider_id) differs from
    the main runner's actual provider — inheritance would be pure
    overhead, so the handoff must fall back to normal mode even with the
    inherit mode forced to "fork".
    """
    SubAgentManager._context_inherit_mode = "fork"
    main_provider = MagicMock()
    main_provider.provider_config = {"id": "provider-main"}
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": _FakeMainRunner(ToolSet(), provider=main_provider)},
    )

    handoff = make_handoff_tool()
    handoff.provider_id = "provider-sub"
    await run_handoff(handoff, run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["system_prompt"].startswith("# Role")
    assert kwargs["prompt"] == "do research"
    # The fork prompt (and the inherited message prefix) must NOT be used.
    assert not kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")


@pytest.mark.asyncio
async def test_fork_mode_keeps_fork_on_matching_provider(mock_ctx, mock_event):
    SubAgentManager._context_inherit_mode = "fork"
    main_provider = MagicMock()
    main_provider.provider_config = {"id": "provider-1"}
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": _FakeMainRunner(ToolSet(), provider=main_provider)},
    )

    # tool.provider_id unset -> prov_id falls back to the session's current
    # provider ("provider-1" from the mock ctx), matching the main runner.
    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")
    assert kwargs["system_prompt"] == ""


@pytest.mark.asyncio
async def test_fork_mode_falls_back_to_normal_on_model_mismatch(mock_ctx, mock_event):
    """Same provider but a different effective model is equally hopeless.

    Provider prefix caches are namespaced per model: the main agent pinned
    ``req.model="model-a"`` for this turn while the forked subagent always
    runs the provider default (``get_model()`` -> "model-b"), so the handoff
    must fall back to normal mode even with the inherit mode forced to
    "fork".
    """
    SubAgentManager._context_inherit_mode = "fork"
    main_provider = MagicMock()
    main_provider.provider_config = {"id": "provider-1"}
    main_runner = _FakeMainRunner(ToolSet(), provider=main_provider)
    main_runner.req.model = "model-a"
    sub_provider = mock_ctx.provider_manager.get_provider_by_id.return_value
    sub_provider.get_model.return_value = "model-b"
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": main_runner},
    )

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["system_prompt"].startswith("# Role")
    assert kwargs["prompt"] == "do research"
    # The fork prompt (and the inherited message prefix) must NOT be used.
    assert not kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")


@pytest.mark.asyncio
async def test_fork_mode_keeps_fork_on_matching_model(mock_ctx, mock_event):
    """A pinned main model equal to the subagent provider default keeps fork."""
    SubAgentManager._context_inherit_mode = "fork"
    main_provider = MagicMock()
    main_provider.provider_config = {"id": "provider-1"}
    main_runner = _FakeMainRunner(ToolSet(), provider=main_provider)
    main_runner.req.model = "model-same"
    sub_provider = mock_ctx.provider_manager.get_provider_by_id.return_value
    sub_provider.get_model.return_value = "model-same"
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": main_runner},
    )

    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")
    assert kwargs["system_prompt"] == ""


@pytest.mark.asyncio
async def test_fork_mode_keeps_fork_when_model_unresolvable(mock_ctx, mock_event):
    """Non-string / unresolvable models must not break fork (keep-fork stance).

    Mirrors the provider guard: when the effective models cannot be
    determined, keep fork instead of conservatively downgrading.
    """
    SubAgentManager._context_inherit_mode = "fork"
    main_provider = MagicMock()
    main_provider.provider_config = {"id": "provider-1"}
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        make_main_messages(),
        extra={"main_agent_runner": _FakeMainRunner(ToolSet(), provider=main_provider)},
    )

    # _FakeMainRunner.req.model is a MagicMock (non-string) and
    # mock_provider.get_model() also returns a MagicMock — the guard must
    # skip the comparison entirely.
    await run_handoff(make_handoff_tool(), run_context)
    kwargs = get_tool_loop_agent_kwargs(mock_ctx)
    assert kwargs["prompt"].startswith("[SUBAGENT MODE: FORK]")
    assert kwargs["system_prompt"] == ""


class _SingleToolCallProvider(Provider):
    """First call returns a tool call, second call returns a final response."""

    def __init__(self, tool_name: str):
        super().__init__({}, {})
        self.tool_name = tool_name
        self.call_count = 0
        self.received_contexts = []

    def get_current_key(self) -> str:
        return "test_key"

    def set_key(self, key: str):
        pass

    async def get_models(self) -> list[str]:
        return ["test_model"]

    async def text_chat(self, **kwargs) -> LLMResponse:
        self.call_count += 1
        self.received_contexts.append(list(kwargs.get("contexts") or []))
        if self.call_count == 1:
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=[self.tool_name],
                tools_call_args=[{"input": "x"}],
                tools_call_ids=["call_sub_1"],
                usage=TokenUsage(input_other=10, output=5),
            )
        return LLMResponse(
            role="assistant",
            completion_text="done",
            usage=TokenUsage(input_other=10, output=5),
        )


async def _run_tool_via_subagent(mock_ctx, mock_event, tool, denied_tools):
    """Drive the runner once with a subagent context and a denied tool set."""
    provider = _SingleToolCallProvider(tool.name)
    request = ProviderRequest(
        prompt="x",
        func_tool=ToolSet(tools=[tool]),
        contexts=[],
    )
    run_context = make_run_context(
        mock_ctx,
        mock_event,
        [],
        extra={
            "is_subagent": True,
            "subagent_name": "child",
            "denied_tools": denied_tools,
        },
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=request,
        run_context=run_context,
        tool_executor=FunctionToolExecutor,
        agent_hooks=BaseAgentRunHooks(),
        streaming=False,
    )
    async for _ in runner.step_until_done(5):
        pass
    return provider, runner


@pytest.mark.asyncio
async def test_subagent_cannot_call_management_tool(mock_ctx, mock_event):
    handler = AsyncMock(return_value="secret")
    tool = FunctionTool(
        name="create_subagent",
        description="d",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )
    provider, runner = await _run_tool_via_subagent(
        mock_ctx,
        mock_event,
        tool,
        SubAgentManager.get_main_agent_only_tools(),
    )

    handler.assert_not_awaited()
    assert runner.done()
    # 第二次 provider 调用的上下文应包含配对的拒绝错误结果
    tool_messages = [msg for msg in provider.received_contexts[1] if msg.role == "tool"]
    assert len(tool_messages) == 1
    text = tool_messages[0].content
    assert "Permission denied" in text
    assert "create_subagent" in text


@pytest.mark.asyncio
async def test_subagent_cannot_call_handoff_tool(mock_ctx, mock_event):
    handoff_tool = make_handoff_tool(name="inner")
    provider, runner = await _run_tool_via_subagent(
        mock_ctx, mock_event, handoff_tool, {handoff_tool.name}
    )

    assert runner.done()
    tool_messages = [msg for msg in provider.received_contexts[1] if msg.role == "tool"]
    assert len(tool_messages) == 1
    assert "Permission denied" in tool_messages[0].content


@pytest.mark.asyncio
async def test_main_agent_context_can_still_call_tools(mock_ctx, mock_event):
    handler = AsyncMock(return_value="ran")
    tool = FunctionTool(
        name="my_tool",
        description="d",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )
    # No is_subagent marker: this is the main agent's run context.
    run_context = make_run_context(mock_ctx, mock_event, [])

    results = [
        r
        async for r in FunctionToolExecutor.execute(tool=tool, run_context=run_context)
    ]
    handler.assert_awaited_once()
    assert any("ran" in c.text for r in results for c in r.content)


def test_effective_raw_tool_set_property():
    runner = ToolLoopAgentRunner()
    # Before reset there is no request.
    assert runner.effective_raw_tool_set is None

    full_set = ToolSet()
    runner.req = MagicMock()
    runner.req.func_tool = full_set
    runner.tool_schema_mode = "full"
    assert runner.effective_raw_tool_set is full_set

    # In skills_like mode req.func_tool holds the light schema set; the
    # property must expose the raw full-schema set instead.
    raw_set = ToolSet()
    runner.tool_schema_mode = "skills_like"
    runner._skill_like_raw_tool_set = raw_set
    assert runner.effective_raw_tool_set is raw_set


def test_configure_validates_inherit_mode():
    SubAgentManager.configure(context_inherit_mode="fork")
    assert SubAgentManager.get_context_inherit_mode() == "fork"
    SubAgentManager.configure(context_inherit_mode="auto")
    assert SubAgentManager.get_context_inherit_mode() == "auto"
    SubAgentManager.configure(context_inherit_mode="bogus")
    assert SubAgentManager.get_context_inherit_mode() == "normal"


def test_main_agent_only_tools_set():
    tools = SubAgentManager.get_main_agent_only_tools()
    assert "transfer_to_subagent" in tools
    for blacklisted in (
        "create_subagent",
        "remove_subagent",
        "list_subagents",
        "manage_subagent_protection",
        "wait_for_subagent",
        "orchestrate_tasks",
        "broadcast_shared_context",
        "view_shared_context",
    ):
        assert blacklisted in tools
