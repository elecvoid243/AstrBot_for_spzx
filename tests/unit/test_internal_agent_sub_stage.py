"""Tests for per-event bindings and attachment preparation before session locks."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.astr_main_agent import MainAgentBuildConfig
from astrbot.core.message.components import File, Plain, Reply
from astrbot.core.pipeline.process_stage.method.agent_sub_stages import internal
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    _binding_overrides,
)
from astrbot.core.utils.session_lock import SessionLockManager


def _make_event(binding) -> SimpleNamespace:
    """Build a minimal event stand-in exposing only get_extra."""
    return SimpleNamespace(
        get_extra=lambda key: binding if key == "agent_team_execution" else None
    )


def _make_cfg() -> MainAgentBuildConfig:
    return MainAgentBuildConfig(
        tool_call_timeout=120,
        fallback_max_context_tokens=128000,
    )


def test_binding_overrides_no_binding_keeps_defaults():
    cfg = _make_cfg()
    max_step, main_agent_cfg = _binding_overrides(_make_event(None), 30, cfg)
    assert max_step == 30
    assert main_agent_cfg is cfg


def test_binding_overrides_max_steps_only():
    cfg = _make_cfg()
    binding = SimpleNamespace(max_steps=7, tool_call_timeout=None, context_length=None)
    max_step, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert max_step == 7
    # Only max_steps set: the config object is returned untouched.
    assert main_agent_cfg is cfg


def test_binding_overrides_invalid_max_steps_keeps_default():
    """A crafted non-numeric max_steps must not raise inside process()."""
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps="abc", tool_call_timeout=None, context_length=None
    )
    max_step, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert max_step == 30
    assert main_agent_cfg is cfg


def test_binding_overrides_invalid_timeout_and_context_length_keep_defaults():
    """Crafted non-numeric timeout/context values fall back to snapshot defaults."""
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout="soon", context_length="many"
    )
    _, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert main_agent_cfg is cfg
    assert main_agent_cfg.tool_call_timeout == 120
    assert main_agent_cfg.fallback_max_context_tokens == 128000


def test_binding_overrides_timeout_and_context_length():
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout=90.5, context_length=8000
    )
    max_step, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert max_step == 30
    assert main_agent_cfg is not cfg
    assert main_agent_cfg.tool_call_timeout == 90.5
    assert main_agent_cfg.fallback_max_context_tokens == 8000
    # Concurrency-safety: the stage-level config object is never mutated.
    assert cfg.tool_call_timeout == 120
    assert cfg.fallback_max_context_tokens == 128000


def test_binding_overrides_timeout_only_keeps_context_length():
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout=45.0, context_length=None
    )
    _, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert main_agent_cfg.tool_call_timeout == 45.0
    assert main_agent_cfg.fallback_max_context_tokens == 128000


def test_binding_overrides_context_length_only_keeps_timeout():
    cfg = _make_cfg()
    binding = SimpleNamespace(
        max_steps=None, tool_call_timeout=None, context_length=4000
    )
    _, main_agent_cfg = _binding_overrides(_make_event(binding), 30, cfg)
    assert main_agent_cfg.tool_call_timeout == 120
    assert main_agent_cfg.fallback_max_context_tokens == 4000


@pytest.fixture
def attachment_stage(monkeypatch):
    """Prepare a real stage that stops after request construction.

    Args:
        monkeypatch: Fixture used to replace downstream model execution.

    Returns:
        Stage, configuration, build mock, and isolated session lock manager.
    """
    config = {"provider_settings": {"enable": True}}
    stage = internal.InternalAgentSubStage()
    stage.streaming_response = False
    stage.show_reasoning = False
    stage.max_step = 128
    stage.main_agent_cfg = MainAgentBuildConfig(tool_call_timeout=1)
    stage.ctx = SimpleNamespace(
        plugin_manager=SimpleNamespace(
            context=SimpleNamespace(get_config=lambda **_kwargs: config)
        )
    )
    manager = SessionLockManager()
    build = AsyncMock(return_value=None)
    monkeypatch.setattr(internal, "session_lock_manager", manager)
    monkeypatch.setattr(internal, "build_main_agent", build)
    monkeypatch.setattr(internal, "call_event_hook", AsyncMock(return_value=False))
    monkeypatch.setattr(internal, "try_capture_follow_up", lambda _event: None)
    monkeypatch.setattr(
        internal.SessionServiceManager,
        "should_process_llm_request",
        AsyncMock(return_value=True),
    )
    return SimpleNamespace(stage=stage, config=config, build=build, manager=manager)


def make_attachment_event(parts, text="hello", platform="test"):
    """Build an event with mutable extras and recorded outbound calls.

    Args:
        parts: Incoming message components.
        text: Incoming plain message text.
        platform: Platform name used for wake-prefix rules.

    Returns:
        Event stand-in accepted by the stage's preflight and build path.
    """
    extras = {}
    return SimpleNamespace(
        unified_msg_origin="attachment-prefetch",
        message_str=text,
        message_obj=SimpleNamespace(message=parts),
        get_extra=extras.get,
        set_extra=extras.__setitem__,
        get_platform_name=lambda: platform,
        is_stopped=lambda: False,
        send_typing=AsyncMock(),
        stop_typing=AsyncMock(),
        send=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_while_waiting", [False, True])
async def test_direct_and_quoted_files_prepare_while_session_is_locked(
    attachment_stage, monkeypatch, stop_while_waiting
):
    direct = File(name="direct.txt", url="https://example.com/direct")
    quoted = File(name="quoted.txt", url="https://example.com/quoted")
    downloaded = asyncio.Event()
    names = []

    async def download(component, **_kwargs):
        names.append(component.name)
        if len(names) == 2:
            downloaded.set()
        return component.name

    monkeypatch.setattr(File, "get_file", download)
    event = make_attachment_event([direct, Reply(id="quoted", chain=[quoted])])

    async def process():
        async for _ in attachment_stage.stage.process(event, ""):
            pass

    async with attachment_stage.manager.acquire_lock(event.unified_msg_origin):
        task = asyncio.create_task(process())
        try:
            await asyncio.wait_for(downloaded.wait(), 2)
            attachment_stage.build.assert_not_awaited()
            assert names == ["direct.txt", "quoted.txt"]
            if stop_while_waiting:
                event.set_extra("agent_stop_requested", True)
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    await asyncio.wait_for(task, 2)
    if stop_while_waiting:
        attachment_stage.build.assert_not_awaited()
    else:
        attachment_stage.build.assert_awaited_once()
    event.send.assert_not_awaited()
    event.stop_typing.assert_awaited_once()


@pytest.mark.asyncio
async def test_slow_download_does_not_block_another_turn(attachment_stage, monkeypatch):
    downloading = asyncio.Event()
    release = asyncio.Event()

    async def download(component, **_kwargs):
        downloading.set()
        await release.wait()
        return component.name

    monkeypatch.setattr(File, "get_file", download)
    file_event = make_attachment_event(
        [File(name="slow.txt", url="https://example.com")]
    )
    text_event = make_attachment_event([Plain(text="second")])

    async def process(event):
        async for _ in attachment_stage.stage.process(event, ""):
            pass

    task = asyncio.create_task(process(file_event))
    try:
        await asyncio.wait_for(downloading.wait(), 2)
        await asyncio.wait_for(process(text_event), 2)
        assert attachment_stage.build.await_args.kwargs["event"] is text_event
        assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert attachment_stage.build.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["prefix", "provider", "session", "stopped", "agent_stop"]
)
async def test_inactive_requests_do_not_download_files(
    attachment_stage, monkeypatch, reason
):
    get_file = AsyncMock(return_value="file.txt")
    monkeypatch.setattr(File, "get_file", get_file)
    event = make_attachment_event([File(name="file.txt", url="https://example.com")])
    if reason == "provider":
        attachment_stage.config["provider_settings"]["enable"] = False
    elif reason == "session":
        internal.SessionServiceManager.should_process_llm_request.return_value = False
    elif reason == "stopped":
        event.is_stopped = lambda: True
    elif reason == "agent_stop":
        event.set_extra("agent_stop_requested", True)
    async for _ in attachment_stage.stage.process(
        event, "/wake" if reason == "prefix" else ""
    ):
        pass
    get_file.assert_not_awaited()
    attachment_stage.build.assert_not_awaited()
    event.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_request", [False, True])
async def test_webchat_and_explicit_requests_keep_wake_prefix_exemption(
    attachment_stage, monkeypatch, explicit_request
):
    get_file = AsyncMock(return_value="file.txt")
    monkeypatch.setattr(File, "get_file", get_file)
    event = make_attachment_event(
        [File(name="file.txt", url="https://example.com")],
        platform="test" if explicit_request else "webchat",
    )
    if explicit_request:
        event.set_extra("provider_request", object())
    async for _ in attachment_stage.stage.process(event, "/wake"):
        pass
    get_file.assert_awaited_once()
    attachment_stage.build.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_during_prefetch_does_not_start_agent(attachment_stage, monkeypatch):
    event = make_attachment_event([File(name="file.txt", url="https://example.com")])

    async def download(component, **_kwargs):
        event.set_extra("agent_stop_requested", True)
        return component.name

    monkeypatch.setattr(File, "get_file", download)
    async for _ in attachment_stage.stage.process(event, ""):
        pass
    attachment_stage.build.assert_not_awaited()
    event.stop_typing.assert_awaited_once()


@pytest.mark.asyncio
async def test_prefetched_file_is_reused_without_another_download(
    attachment_stage, monkeypatch, tmp_path
):
    path = tmp_path / "file.txt"
    path.write_text("attachment", encoding="utf-8")
    downloads = []

    async def download(component):
        downloads.append(component.url)
        component.file_ = str(path)

    monkeypatch.setattr(File, "_download_file", download)
    component = File(name="file.txt", url="https://example.com/file")
    event = make_attachment_event([component])
    async for _ in attachment_stage.stage.process(event, ""):
        pass
    assert await component.get_file() == str(path.resolve())
    assert downloads == ["https://example.com/file"]
    attachment_stage.build.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelling_prefetch_releases_typing_and_never_acquires_lock(
    attachment_stage, monkeypatch
):
    downloading = asyncio.Event()

    async def download(component, **_kwargs):
        downloading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(File, "get_file", download)
    event = make_attachment_event([File(name="file.txt", url="https://example.com")])

    async def process():
        async for _ in attachment_stage.stage.process(event, ""):
            pass

    task = asyncio.create_task(process())
    await asyncio.wait_for(downloading.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    attachment_stage.build.assert_not_awaited()
    event.stop_typing.assert_awaited_once()
    async with attachment_stage.manager.acquire_lock(event.unified_msg_origin):
        pass
