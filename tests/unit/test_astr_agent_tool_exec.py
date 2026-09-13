import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import mcp
import pytest

from astrbot.core.agent.agent import Agent
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.message.components import Image
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.func_tool_manager import (
    FunctionToolManager,
    _PermissionGuardedTool,
)
from astrbot.core.star.context import Context


class _DummyEvent:
    def __init__(self, message_components: list[object] | None = None) -> None:
        self.unified_msg_origin = "webchat:FriendMessage:webchat!user!session"
        self.message_obj = SimpleNamespace(message=message_components or [])
        self.role = "member"

    def get_extra(self, _key: str):
        return None

    def get_platform_name(self):
        # Non-webchat: the subagent progress sink is never created in
        # handoff tests, keeping them on the pre-feature code path.
        return "test"


class _DummyTool:
    def __init__(self) -> None:
        self.name = "transfer_to_subagent"
        self.agent = SimpleNamespace(name="subagent")


def _build_run_context(message_components: list[object] | None = None):
    event = _DummyEvent(message_components=message_components)
    ctx = SimpleNamespace(event=event, context=SimpleNamespace())
    return ContextWrapper(context=ctx)


class _DoneRunner:
    async def step_until_done(self, _max_step):
        for item in ():
            yield item

    def get_final_llm_resp(self):
        return SimpleNamespace(role="assistant", completion_text="done")


def test_build_handoff_toolset_keeps_permission_guards_for_default_tools():
    mgr = FunctionToolManager()
    plugin_tool = FunctionTool(
        name="admin_only_mcp",
        description="admin tool",
        parameters={"type": "object", "properties": {}},
    )
    handoff = HandoffTool(Agent(name="child"))
    mgr.func_list = [plugin_tool, handoff]

    event = _DummyEvent()
    context = SimpleNamespace(
        get_config=lambda **_kwargs: {
            "provider_settings": {"computer_use_runtime": "none"}
        },
        get_llm_tool_manager=lambda: mgr,
    )
    run_context = ContextWrapper(context=SimpleNamespace(event=event, context=context))

    toolset = FunctionToolExecutor._build_handoff_toolset(run_context, tools=None)

    assert toolset is not None
    assert isinstance(toolset.get_tool("admin_only_mcp"), _PermissionGuardedTool)
    assert toolset.get_tool("transfer_to_child") is None


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_normalizes_filters_and_appends_event_image(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/event_image.png"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls_input = (
        " https://example.com/a.png ",
        "/tmp/not_an_image.txt",
        "/tmp/local.webp",
        123,
    )

    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        image_urls_input,
    )

    assert image_urls == [
        "https://example.com/a.png",
        "/tmp/local.webp",
        "/tmp/event_image.png",
    ]


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_skips_failed_event_image_conversion(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        ["https://example.com/a.png"],
    )

    assert image_urls == ["https://example.com/a.png"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_refs", "expected_supported_refs"),
    [
        pytest.param(
            (
                "https://example.com/valid.png",
                "base64://iVBORw0KGgoAAAANSUhEUgAAAAUA",
                "file:///tmp/photo.heic",
                "file://localhost/tmp/vector.svg",
                "file://fileserver/share/image.webp",
                "file:///tmp/not-image.txt",
                "mailto:user@example.com",
                "random-string-without-scheme-or-extension",
            ),
            {
                "https://example.com/valid.png",
                "base64://iVBORw0KGgoAAAANSUhEUgAAAAUA",
                "file:///tmp/photo.heic",
                "file://localhost/tmp/vector.svg",
                "file://fileserver/share/image.webp",
            },
            id="mixed_supported_and_unsupported_refs",
        ),
    ],
)
async def test_collect_handoff_image_urls_filters_supported_schemes_and_extensions(
    image_refs: tuple[str, ...],
    expected_supported_refs: set[str],
):
    run_context = _build_run_context([])
    result = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context, image_refs
    )
    assert set(result) == expected_supported_refs


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_collects_event_image_when_args_is_none(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/event_only.png"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        None,
    )

    assert image_urls == ["/tmp/event_only.png"]


@pytest.mark.asyncio
async def test_do_handoff_background_reports_prepared_image_urls(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def _fake_execute_handoff(
        cls, tool, run_context, image_urls_prepared=False, **tool_args
    ):
        assert image_urls_prepared is True
        yield mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="ok")]
        )

    async def _fake_wake(cls, run_context, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        FunctionToolExecutor,
        "_execute_handoff",
        classmethod(_fake_execute_handoff),
    )
    monkeypatch.setattr(
        FunctionToolExecutor,
        "_wake_main_agent_for_background_result",
        classmethod(_fake_wake),
    )

    run_context = _build_run_context()
    await FunctionToolExecutor._do_handoff_background(
        tool=_DummyTool(),
        run_context=run_context,
        task_id="task-id",
        input="hello",
        image_urls="https://example.com/raw.png",
    )

    assert captured["tool_args"]["image_urls"] == ["https://example.com/raw.png"]


@pytest.mark.asyncio
async def test_execute_handoff_skips_renormalize_when_image_urls_prepared(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    def _boom(_items):
        raise RuntimeError("normalize should not be called")

    async def _fake_tool_loop_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(completion_text="ok")

    # AstrAgentContext built inside _execute_handoff validates context/event
    # via isinstance, so spec'd MagicMocks are required over SimpleNamespace.
    context = MagicMock(spec=Context)
    context.get_current_chat_provider_id = AsyncMock(return_value="provider-id")
    context.tool_loop_agent = _fake_tool_loop_agent
    context.get_config = lambda **_kwargs: {"provider_settings": {}}
    event = MagicMock(spec=AstrMessageEvent)
    event.unified_msg_origin = "webchat:FriendMessage:webchat!user!session"
    event.get_platform_name.return_value = "test_platform"
    event.get_sender_name.return_value = "tester"
    event.trace = None
    event.message_obj = SimpleNamespace(message=[])
    run_context = ContextWrapper(
        context=SimpleNamespace(event=event, context=context, extra={})
    )
    tool = SimpleNamespace(
        name="transfer_to_subagent",
        provider_id=None,
        agent=SimpleNamespace(
            name="subagent",
            tools=[],
            instructions="subagent-instructions",
            begin_dialogs=[],
            run_hooks=None,
        ),
    )

    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.normalize_and_dedupe_strings", _boom
    )

    results = []
    async for result in FunctionToolExecutor._execute_handoff(
        tool,
        run_context,
        image_urls_prepared=True,
        input="hello",
        image_urls=["https://example.com/raw.png"],
    ):
        results.append(result)

    assert len(results) == 1
    assert captured["image_urls"] == ["https://example.com/raw.png"]


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_keeps_extensionless_existing_event_file(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/astrbot-handoff-image"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.get_astrbot_temp_path", lambda: "/tmp"
    )
    monkeypatch.setattr(
        "astrbot.core.utils.image_ref_utils.os.path.exists", lambda _: True
    )

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        [],
    )

    assert image_urls == ["/tmp/astrbot-handoff-image"]


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_filters_extensionless_missing_event_file(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/tmp/astrbot-handoff-missing-image"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.get_astrbot_temp_path", lambda: "/tmp"
    )
    monkeypatch.setattr(
        "astrbot.core.utils.image_ref_utils.os.path.exists", lambda _: False
    )

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        [],
    )

    assert image_urls == []


@pytest.mark.asyncio
async def test_execute_handoff_passes_tool_call_timeout_to_tool_loop_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def _fake_tool_loop_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(completion_text="ok")

    context = MagicMock(spec=Context)
    context.get_current_chat_provider_id = AsyncMock(return_value="provider-id")
    context.tool_loop_agent = _fake_tool_loop_agent
    context.get_config = lambda **_kwargs: {"provider_settings": {}}
    event = MagicMock(spec=AstrMessageEvent)
    event.unified_msg_origin = "webchat:FriendMessage:webchat!user!session"
    event.get_platform_name.return_value = "test_platform"
    event.get_sender_name.return_value = "tester"
    event.trace = None
    event.message_obj = SimpleNamespace(message=[])
    run_context = ContextWrapper(
        context=SimpleNamespace(event=event, context=context, extra={}),
        tool_call_timeout=120,
    )
    tool = SimpleNamespace(
        name="transfer_to_subagent",
        provider_id=None,
        agent=SimpleNamespace(
            name="subagent",
            tools=[],
            instructions="subagent-instructions",
            begin_dialogs=[],
            run_hooks=None,
        ),
    )

    results = []
    async for result in FunctionToolExecutor._execute_handoff(
        tool,
        run_context,
        image_urls_prepared=True,
        input="hello",
        image_urls=[],
    ):
        results.append(result)

    assert len(results) == 1
    assert captured["tool_call_timeout"] == 120


@pytest.mark.asyncio
async def test_background_wakeup_passes_history_and_provider_settings_to_main_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test background wakeup keeps structured history and provider settings."""
    provider_settings = {
        "fallback_chat_models": ["fallback-provider"],
        "request_max_retries": 3,
        "stream": True,
    }
    history = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    captured: dict = {}

    async def _fake_get_session_conv(**_kwargs):
        return SimpleNamespace(history=json.dumps(history))

    async def _fake_build_main_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(agent_runner=_DoneRunner())

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        _fake_get_session_conv,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent.build_main_agent",
        _fake_build_main_agent,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.persist_agent_history",
        AsyncMock(),
    )

    send_tool = FunctionTool(
        name="send_message_to_user",
        description="send",
        parameters={"type": "object", "properties": {}},
    )
    context = SimpleNamespace(
        get_config=lambda **_kwargs: {"provider_settings": provider_settings},
        get_llm_tool_manager=lambda: SimpleNamespace(
            get_builtin_tool=lambda _tool_cls: send_tool
        ),
        conversation_manager=SimpleNamespace(),
    )
    run_context = ContextWrapper(
        context=SimpleNamespace(event=_DummyEvent([]), context=context),
        tool_call_timeout=456,
    )

    await FunctionToolExecutor._wake_main_agent_for_background_result(
        run_context,
        task_id="task-id",
        tool_name="long_tool",
        result_text="ok",
        tool_args={},
        note="task finished",
        summary_name="BackgroundTask",
    )

    config = captured["config"]
    assert config.tool_call_timeout == 456
    assert config.streaming_response == provider_settings["stream"]
    assert config.provider_settings == provider_settings
    assert config.provider_settings["fallback_chat_models"] == ["fallback-provider"]
    request = captured["req"]
    assert "old question" not in request.system_prompt
    assert "old answer" not in request.system_prompt
    assert request.contexts == history


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_settings", "expected_max_step"),
    [
        pytest.param({"max_agent_step": 50}, 50, id="configured"),
        pytest.param({}, 30, id="missing_falls_back_to_default"),
        pytest.param({"max_agent_step": True}, 30, id="boolean_falls_back_to_default"),
        pytest.param({"max_agent_step": "50"}, 50, id="numeric_string_coerced"),
        pytest.param({"max_agent_step": 0}, 1, id="zero_clamped_to_min"),
    ],
)
async def test_background_wakeup_applies_max_agent_step(
    monkeypatch: pytest.MonkeyPatch,
    provider_settings: dict,
    expected_max_step: int,
):
    class _StepCapturingRunner:
        def __init__(self):
            self.captured_max_step = None

        async def step_until_done(self, max_step):
            self.captured_max_step = max_step
            if False:
                yield

        def get_final_llm_resp(self):
            return SimpleNamespace(role="assistant", completion_text="done")

    runner = _StepCapturingRunner()

    async def _fake_get_session_conv(**_kwargs):
        return SimpleNamespace(history="[]")

    async def _fake_build_main_agent(**_kwargs):
        return SimpleNamespace(agent_runner=runner)

    monkeypatch.setattr(
        "astrbot.core.astr_main_agent._get_session_conv",
        _fake_get_session_conv,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_main_agent.build_main_agent",
        _fake_build_main_agent,
    )
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.persist_agent_history",
        AsyncMock(),
    )

    send_tool = FunctionTool(
        name="send_message_to_user",
        description="send",
        parameters={"type": "object", "properties": {}},
    )
    context = SimpleNamespace(
        get_config=lambda **_kwargs: {
            "provider_settings": {},
            "agent_runner": {
                "runner_type": "local",
                "config": {
                    "misc": {"max_steps": provider_settings.get("max_agent_step", 30)}
                },
            },
        },
        get_llm_tool_manager=lambda: SimpleNamespace(
            get_builtin_tool=lambda _tool_cls: send_tool
        ),
        conversation_manager=SimpleNamespace(),
    )
    run_context = ContextWrapper(
        context=SimpleNamespace(event=_DummyEvent([]), context=context),
        tool_call_timeout=120,
    )

    await FunctionToolExecutor._wake_main_agent_for_background_result(
        run_context,
        task_id="task-id",
        tool_name="long_tool",
        result_text="ok",
        tool_args={},
        note="task finished",
        summary_name="BackgroundTask",
    )

    assert runner.captured_max_step == expected_max_step


@pytest.mark.asyncio
async def test_collect_handoff_image_urls_filters_extensionless_file_outside_temp_root(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_convert_to_file_path(self):
        return "/var/tmp/astrbot-handoff-image"

    monkeypatch.setattr(Image, "convert_to_file_path", _fake_convert_to_file_path)
    monkeypatch.setattr(
        "astrbot.core.astr_agent_tool_exec.get_astrbot_temp_path", lambda: "/tmp"
    )
    monkeypatch.setattr(
        "astrbot.core.utils.image_ref_utils.os.path.exists", lambda _: True
    )

    run_context = _build_run_context([Image(file="file:///tmp/original.png")])
    image_urls = await FunctionToolExecutor._collect_handoff_image_urls(
        run_context,
        [],
    )

    assert image_urls == []


class TestMaybeCreateSubagentSink:
    """Gating tests for FunctionToolExecutor._maybe_create_subagent_sink.

    Author: elecvoid243, 2026-07-26
    Plan: docs/superpowers/plans/2026-07-26-subagent-chatui-progress.md (Task 3)
    """

    def _event(self, platform_name="webchat"):
        class _Msg:
            message_id = "msg-42"

        class _Event:
            message_obj = _Msg()

            def get_platform_name(self):
                return platform_name

        return _Event()

    def test_returns_sink_for_webchat_when_enabled(self):
        sink = FunctionToolExecutor._maybe_create_subagent_sink(
            self._event(), {"show_subagent_progress": True}, "researcher", "do it"
        )
        assert sink is not None
        assert sink._message_id == "msg-42"
        assert sink._agent_name == "researcher"
        assert sink._input_preview == "do it"

    def test_returns_none_for_non_webchat(self):
        assert (
            FunctionToolExecutor._maybe_create_subagent_sink(
                self._event("aiocqhttp"), {}, "researcher", "do it"
            )
            is None
        )

    def test_returns_none_when_disabled(self):
        assert (
            FunctionToolExecutor._maybe_create_subagent_sink(
                self._event(), {"show_subagent_progress": False}, "researcher", "do it"
            )
            is None
        )

    def test_defaults_to_enabled(self):
        assert (
            FunctionToolExecutor._maybe_create_subagent_sink(
                self._event(), {}, "researcher", "do it"
            )
            is not None
        )

    def test_returns_none_without_message_id(self):
        class _Event:
            message_obj = SimpleNamespace(message_id=None)

            def get_platform_name(self):
                return "webchat"

        assert (
            FunctionToolExecutor._maybe_create_subagent_sink(
                _Event(), {}, "researcher", "do it"
            )
            is None
        )


class _SleepTool(FunctionTool):
    async def call(self, context, seconds: float = 0.0) -> str:
        await asyncio.sleep(seconds)
        return "done"


def _make_sleep_tool() -> _SleepTool:
    return _SleepTool(
        name="slow_tool",
        description="sleeps for the given seconds",
        parameters={
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
        },
    )


def _tool_run_context(exclude: list[str]) -> ContextWrapper:
    event = _DummyEvent()
    return ContextWrapper(
        context=SimpleNamespace(event=event),
        tool_call_timeout=1,
        tool_call_timeout_exclude=exclude,
    )


@pytest.mark.asyncio
async def test_execute_local_excluded_tool_ignores_tool_call_timeout():
    tool = _make_sleep_tool()
    run_context = _tool_run_context(exclude=["slow_tool"])

    results = []
    async for r in FunctionToolExecutor._execute_local(tool, run_context, seconds=1.5):
        results.append(r)

    # The tool slept 1.5s > tool_call_timeout=1s; exclusion means no outer
    # timeout fired and the result still came through.
    assert results[0].content[0].text == "done"


@pytest.mark.asyncio
async def test_execute_local_unexcluded_tool_times_out():
    tool = _make_sleep_tool()
    run_context = _tool_run_context(exclude=[])

    with pytest.raises(Exception, match="timeout after 1 seconds"):
        async for _ in FunctionToolExecutor._execute_local(
            tool, run_context, seconds=3
        ):
            pass
