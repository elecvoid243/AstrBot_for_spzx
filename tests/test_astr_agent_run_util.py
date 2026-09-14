import asyncio
from types import SimpleNamespace

import pytest

from astrbot.core.agent.response import AgentResponse
from astrbot.core.astr_agent_run_util import _simulated_stream_tts, run_agent
from astrbot.core.message.message_event_result import MessageChain


class _FakeEvent:
    """Minimal event surface used by the agent stream bridge."""

    def is_stopped(self) -> bool:
        return False

    def get_extra(self, key: str):
        del key
        return None

    def get_platform_name(self) -> str:
        return "test"


class _StreamingErrorRunner:
    """Agent runner that finishes with one provider error response."""

    streaming = True
    req = None

    def __init__(self, error_text: str) -> None:
        self.error_text = error_text
        self.finished = False
        self.run_context = SimpleNamespace(context=SimpleNamespace(event=_FakeEvent()))

    async def step(self):
        self.finished = True
        yield AgentResponse(
            type="err",
            data={"chain": MessageChain().message(self.error_text)},
        )

    def done(self) -> bool:
        return self.finished


class _MalformedStreamingErrorRunner(_StreamingErrorRunner):
    """Agent runner that returns an invalid provider error payload."""

    async def step(self):
        self.finished = True
        yield AgentResponse(type="err", data={})


@pytest.mark.asyncio
async def test_run_agent_forwards_streaming_provider_error():
    error_text = (
        "LLM 响应错误: Not found the model k2.7-code-highspeed or Permission denied"
    )
    runner = _StreamingErrorRunner(error_text)

    chains = [chain async for chain in run_agent(runner)]

    assert len(chains) == 1
    assert chains[0].get_plain_text() == error_text


@pytest.mark.asyncio
async def test_run_agent_replaces_malformed_streaming_provider_error():
    runner = _MalformedStreamingErrorRunner("unused")

    chains = [chain async for chain in run_agent(runner)]

    assert len(chains) == 1
    assert chains[0].get_plain_text() == "Error occurred during AI execution."


@pytest.mark.asyncio
async def test_simulated_stream_tts_leaves_audio_for_deferred_cleanup(tmp_path):
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(b"audio")

    class _TTSProvider:
        async def get_audio(self, text: str) -> str:
            assert text == "hello"
            return str(audio_path)

    text_queue: asyncio.Queue[str | None] = asyncio.Queue()
    audio_queue: asyncio.Queue[bytes | tuple[str, bytes] | None] = asyncio.Queue()
    await text_queue.put("hello")
    await text_queue.put(None)

    await _simulated_stream_tts(_TTSProvider(), text_queue, audio_queue)

    assert await audio_queue.get() == ("hello", b"audio")
    assert await audio_queue.get() is None
    assert audio_path.exists()


class _WebchatEvent(_FakeEvent):
    """Webchat variant that records chains sent via ``send()``."""

    def __init__(self) -> None:
        self.sent_chains: list[MessageChain] = []

    def get_platform_name(self) -> str:
        return "webchat"

    async def send(self, chain: MessageChain) -> None:
        self.sent_chains.append(chain)


class _FileChangesRunner:
    """Streaming runner that finishes with a single file_changes response."""

    streaming = True
    req = None

    def __init__(self, event: _WebchatEvent) -> None:
        self.finished = False
        self.run_context = SimpleNamespace(context=SimpleNamespace(event=event))

    async def step(self):
        self.finished = True
        yield AgentResponse(
            type="file_changes",
            data={
                "chain": MessageChain(
                    type="file_changes",
                    chain=[],
                ).message("payload")
            },
        )

    def done(self) -> bool:
        return self.finished


@pytest.mark.asyncio
async def test_run_agent_forwards_file_changes_on_webchat():
    """file_changes must reach the webchat event instead of being dropped.

    Mirrors the agent_stats dispatch: without an explicit branch the
    streaming-mode fall-through silently discards the response, so the
    ChatUI never receives the end-of-turn file change summary.
    """
    event = _WebchatEvent()
    runner = _FileChangesRunner(event)

    chains = [chain async for chain in run_agent(runner)]

    assert chains == []
    assert len(event.sent_chains) == 1
    assert event.sent_chains[0].type == "file_changes"


@pytest.mark.asyncio
async def test_run_agent_drops_file_changes_off_webchat():
    """Non-webchat platforms have no summary UI: drop silently."""

    class _OtherPlatformEvent(_FakeEvent):
        def __init__(self) -> None:
            self.sent_chains: list[MessageChain] = []

        async def send(self, chain: MessageChain) -> None:
            self.sent_chains.append(chain)

    event = _OtherPlatformEvent()
    runner = _FileChangesRunner(event)  # type: ignore[arg-type]

    chains = [chain async for chain in run_agent(runner)]

    assert chains == []
    assert event.sent_chains == []
