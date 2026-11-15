"""Runner and recorder tests for developer-mode tool executions and provenance.

Author: impl_task6
Date: 2026-11-15
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from mcp.types import CallToolResult, TextContent

from astrbot.builtin_stars.compact.main import Main as CompactCommand
from astrbot.core import astr_main_agent
from astrbot.core.agent.context.compressor import LLMSummaryCompressor
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.message import Message
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.db.po import Conversation
from astrbot.core.exceptions import ContextCapacityError
from astrbot.core.provider import request_snapshot
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage
from astrbot.core.provider.provider import Provider
from astrbot.core.provider.request_snapshot import (
    record_provenance,
    request_snapshot_scope,
    request_snapshots,
)

SESSION_ID = "session-1"
UMO = f"webchat:FriendMessage:{SESSION_ID}"


class FakeSnapshotEvent:
    """Minimal event carrying the extras the runner trace helpers read."""

    def __init__(self) -> None:
        self.unified_msg_origin = UMO
        self._extras: dict[str, object] = {}

    def get_extra(self, key: str, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key: str, value) -> None:
        self._extras[key] = value

    def get_platform_name(self) -> str:
        return "webchat"


class FakeSnapshotProvider(Provider):
    """Provider double that captures one snapshot per ``text_chat`` call."""

    SNAPSHOT_ADAPTER = "openai_chat"

    def __init__(
        self,
        *,
        tool_calls: bool = True,
        tool_result: str = "tool result",
    ) -> None:
        super().__init__(
            {
                "id": "fake-snapshot-provider",
                "type": "openai_chat_completion",
                "max_context_tokens": 0,
            },
            {},
        )
        self.call_count = 0
        self.tool_calls = tool_calls
        self.tool_result = tool_result

    def get_current_key(self) -> str:
        return "test-key"

    def set_key(self, key: str) -> None:
        pass

    async def get_models(self) -> list[str]:
        return ["fake-model"]

    def _capture(self) -> None:
        request_snapshots.capture(
            self,
            {
                "model": "fake-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            adapter=self.SNAPSHOT_ADAPTER,
        )

    async def text_chat(self, **kwargs) -> LLMResponse:
        self.call_count += 1
        self._capture()
        if self.tool_calls and self.call_count == 1:
            return LLMResponse(
                role="assistant",
                completion_text="",
                tools_call_name=["read_file"],
                tools_call_args=[{"path": "a.txt"}],
                tools_call_ids=["call_1"],
                usage=TokenUsage(input_other=10, output=5),
            )
        return LLMResponse(
            role="assistant",
            completion_text="final answer",
            usage=TokenUsage(input_other=12, output=4),
        )


class FakeSnapshotHooks(BaseAgentRunHooks):
    """No-op hooks that can request a stop when a tool starts."""

    def __init__(self) -> None:
        self.runner: ToolLoopAgentRunner | None = None
        self.stop_on_tool_start = False

    async def on_agent_begin(self, run_context) -> None:
        pass

    async def on_tool_start(self, run_context, tool, tool_args) -> None:
        if self.stop_on_tool_start and self.runner is not None:
            self.runner.request_stop()

    async def on_tool_end(self, run_context, tool, tool_args, tool_result) -> None:
        pass

    async def on_agent_done(self, run_context, llm_response) -> None:
        pass


class FakeSnapshotToolExecutor:
    """Return one or more text results, or raise, for every tool call."""

    def __init__(
        self,
        *,
        text: str = "tool result",
        error: bool = False,
        chunks: list[str] | None = None,
    ) -> None:
        self.text = text
        self.error = error
        self.chunks = chunks

    def execute(self, tool, run_context, **tool_args):
        if self.error:

            async def failing():
                raise RuntimeError("tool exploded")
                yield  # pragma: no cover

            return failing()

        async def generator():
            if self.chunks is not None:
                for chunk in self.chunks:
                    yield CallToolResult(content=[TextContent(type="text", text=chunk)])
                return
            yield CallToolResult(content=[TextContent(type="text", text=self.text)])

        return generator()


def _tool_set() -> ToolSet:
    return ToolSet(
        tools=[
            FunctionTool(
                name="read_file",
                description="read a file",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
                handler=AsyncMock(),
            )
        ]
    )


class FakeRunnerFixture:
    """Bundle of the runner and its doubles used by the tests."""

    def __init__(
        self,
        provider: FakeSnapshotProvider,
        *,
        tool_executor: FakeSnapshotToolExecutor | None = None,
    ) -> None:
        self.provider = provider
        self.event = FakeSnapshotEvent()
        self.context = SimpleNamespace(event=self.event, extra={})
        self.run_context = ContextWrapper(context=self.context)
        self.hooks = FakeSnapshotHooks()
        self.tool_executor = tool_executor or FakeSnapshotToolExecutor(
            text=provider.tool_result
        )
        self.runner = ToolLoopAgentRunner()
        self.hooks.runner = self.runner

    async def reset(
        self,
        *,
        contexts: list[dict] | None = None,
        llm_compress_provider: Provider | None = None,
    ) -> ToolLoopAgentRunner:
        await self.runner.reset(
            provider=self.provider,
            request=ProviderRequest(
                prompt="hello",
                session_id=UMO,
                conversation=Conversation(
                    platform_id="webchat",
                    user_id="user-1",
                    cid=SESSION_ID,
                ),
                func_tool=_tool_set(),
                contexts=contexts or [],
            ),
            run_context=self.run_context,
            tool_executor=self.tool_executor,
            agent_hooks=self.hooks,
            llm_compress_provider=llm_compress_provider,
        )
        return self.runner


@pytest.fixture(autouse=True)
def recorder(monkeypatch: pytest.MonkeyPatch):
    """Enable developer mode and isolate the singleton recorder."""

    request_snapshots.reset()
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: True)
    yield
    request_snapshots.reset()


@pytest_asyncio.fixture
async def fake_runner() -> ToolLoopAgentRunner:
    fixture = FakeRunnerFixture(FakeSnapshotProvider())
    return await fixture.reset()


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: True)


def _snapshot_items(event) -> list[dict]:
    chain = event.data["chain"]
    assert chain.type == "llm_request_snapshots"
    payload = chain.chain[0].data
    if not isinstance(payload, dict):
        payload = json.loads(payload)
    return payload["items"]


def _stored(items: list[dict], index: int = 0) -> dict:
    snapshot = request_snapshots.get(items[index]["snapshot_id"])
    assert snapshot is not None
    return snapshot


@pytest.mark.asyncio
async def test_tool_execution_is_attached_to_the_request_that_called_it(
    fake_runner: ToolLoopAgentRunner,
):
    events = [event async for event in fake_runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]
    assert len(snapshots) == 2

    first = _stored(_snapshot_items(snapshots[0]))
    executions = first["tool_executions"]
    assert len(executions) == 1
    execution = executions[0]
    assert execution["call_id"] == "call_1"
    assert execution["name"] == "read_file"
    assert execution["status"] == "ok"
    assert execution["permission"] == "allow"
    assert execution["executor"] == "main"
    assert execution["result_bytes"] == len(b"tool result")
    assert execution["result_preview"] == "tool result"
    assert execution["result_ref"] is None
    assert isinstance(execution["started_at"], float)
    assert isinstance(execution["duration_ms"], int)
    assert execution["duration_ms"] >= 0

    # The follow-up request is a different invocation and must stay clean.
    second = _stored(_snapshot_items(snapshots[1]))
    assert second["invocation_id"] != first["invocation_id"]
    assert second["tool_executions"] == []


@pytest.mark.asyncio
async def test_denied_tool_is_recorded_as_denied(
    fake_runner: ToolLoopAgentRunner,
):
    fake_runner.req.denied_tools = {"read_file"}

    events = [event async for event in fake_runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]
    execution = _stored(_snapshot_items(snapshots[0]))["tool_executions"][0]

    assert execution["status"] == "denied"
    assert execution["permission"] == "deny"
    assert execution["call_id"] == "call_1"


@pytest.mark.asyncio
async def test_erroring_tool_is_recorded_as_error():
    fixture = FakeRunnerFixture(
        FakeSnapshotProvider(),
        tool_executor=FakeSnapshotToolExecutor(error=True),
    )
    runner = await fixture.reset()

    events = [event async for event in runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]
    execution = _stored(_snapshot_items(snapshots[0]))["tool_executions"][0]

    assert execution["status"] == "error"
    assert execution["permission"] == "allow"
    assert execution["result_preview"].startswith("error:")
    assert execution["result_bytes"] == len(execution["result_preview"].encode())


@pytest.mark.asyncio
async def test_interrupted_tool_is_recorded_as_interrupted():
    fixture = FakeRunnerFixture(FakeSnapshotProvider())
    runner = await fixture.reset()
    fixture.hooks.stop_on_tool_start = True

    events = [event async for event in runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]
    execution = _stored(_snapshot_items(snapshots[0]))["tool_executions"][0]

    assert execution["status"] == "interrupted"
    assert execution["call_id"] == "call_1"


@pytest.mark.asyncio
async def test_tool_result_preview_is_capped_at_two_kilobytes():
    big_result = "x" * 5000
    fixture = FakeRunnerFixture(FakeSnapshotProvider(tool_result=big_result))
    runner = await fixture.reset()

    events = [event async for event in runner.step_until_done(max_step=4)]
    snapshots = [e for e in events if e.type == "llm_request_snapshots"]
    execution = _stored(_snapshot_items(snapshots[0]))["tool_executions"][0]

    assert execution["result_bytes"] == len(big_result.encode())
    preview = execution["result_preview"]
    assert 0 < len(preview.encode("utf-8")) <= 2048
    assert big_result.startswith(preview)


def test_record_tool_execution_updates_every_attempt(monkeypatch: pytest.MonkeyPatch):
    _enable(monkeypatch)
    provider = FakeProvenanceProvider()
    raw = {
        "model": "m1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    with request_snapshot_scope(session_id=SESSION_ID, umo=UMO, invocation_id="inv-1"):
        first = request_snapshots.capture(provider, raw, adapter="openai_chat")
        second = request_snapshots.capture(provider, raw, adapter="openai_chat")
    assert first and second

    request_snapshots.record_tool_execution(
        "inv-1",
        call_id="call_1",
        name="read_file",
        status="ok",
        started_at=1.0,
        duration_ms=3,
        result_bytes=5000,
        result_preview="x" * 5000,
    )
    for snapshot_id in (first, second):
        rows = request_snapshots.get(snapshot_id)["tool_executions"]
        assert len(rows) == 1
        assert rows[0]["result_bytes"] == 5000
        assert len(rows[0]["result_preview"].encode("utf-8")) <= 2048
        assert rows[0]["result_ref"] is None

    # A repeated call identity replaces its row instead of duplicating it.
    request_snapshots.record_tool_execution(
        "inv-1",
        call_id="call_1",
        name="read_file",
        status="denied",
        started_at=1.0,
        duration_ms=3,
        result_bytes=0,
        permission="deny",
    )
    rows = request_snapshots.get(first)["tool_executions"]
    assert len(rows) == 1
    assert rows[0]["status"] == "denied"
    assert rows[0]["permission"] == "deny"


def test_record_tool_execution_normalizes_bad_values(monkeypatch: pytest.MonkeyPatch):
    _enable(monkeypatch)
    with request_snapshot_scope(session_id=SESSION_ID, umo=UMO, invocation_id="inv-1"):
        snapshot_id = request_snapshots.capture(
            FakeProvenanceProvider(),
            {"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
            adapter="openai_chat",
        )
    assert snapshot_id

    request_snapshots.record_tool_execution(
        "inv-1",
        call_id="call_1",
        name="read_file",
        status="weird",
        started_at=None,
        duration_ms=-5,
        result_bytes=-1,
        result_preview=None,
        permission="maybe",
        executor="main",
    )
    row = request_snapshots.get(snapshot_id)["tool_executions"][0]
    assert row["status"] == "error"
    assert row["started_at"] is None
    assert row["duration_ms"] is None
    assert row["result_bytes"] == 0
    assert row["result_preview"] == ""
    assert row["permission"] == "allow"


def test_record_tool_execution_without_a_known_invocation_is_a_noop(monkeypatch):
    _enable(monkeypatch)

    request_snapshots.record_tool_execution(
        "missing-invocation",
        call_id="call_1",
        name="read_file",
        status="ok",
        started_at=1.0,
        duration_ms=1,
        result_bytes=2,
    )

    assert request_snapshots.list_for_session(SESSION_ID)["items"] == []


class FakeCompressionProvider(Provider):
    """Provider double that captures the summary request like a real hook."""

    SNAPSHOT_ADAPTER = "openai_chat"

    def __init__(self) -> None:
        super().__init__(
            {
                "id": "fake-compression-provider",
                "type": "openai_chat_completion",
                "max_context_tokens": 0,
            },
            {},
        )

    def get_current_key(self) -> str:
        return "test-key"

    def set_key(self, key: str) -> None:
        pass

    async def get_models(self) -> list[str]:
        return ["fake-model"]

    async def text_chat(self, **kwargs) -> LLMResponse:
        request_snapshots.capture(
            self,
            {
                "model": "fake-model",
                "messages": [{"role": "user", "content": "summarize"}],
                "stream": False,
            },
            adapter=self.SNAPSHOT_ADAPTER,
        )
        return LLMResponse(
            role="assistant",
            completion_text="summary",
            usage=TokenUsage(input_other=5, output=2),
        )


@pytest.mark.asyncio
async def test_compression_and_title_kinds(monkeypatch: pytest.MonkeyPatch):
    _enable(monkeypatch)
    compressor = LLMSummaryCompressor(
        provider=FakeCompressionProvider(),
        keep_recent_ratio=0.0,
    )
    messages = [
        Message(role="system", content="SYS"),
        Message(role="user", content="old question"),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="new question"),
    ]

    # Compression runs inside the runner's request scope, which only has to
    # override the kind.
    with request_snapshot_scope(
        session_id=SESSION_ID,
        umo=UMO,
        turn_id="turn-1",
    ):
        await compressor(messages, None)

    compression_snapshot = _stored(
        request_snapshots.list_for_session(SESSION_ID)["items"]
    )
    assert compression_snapshot["kind"] == "compression"
    assert compression_snapshot["session_id"] == SESSION_ID
    assert compression_snapshot["turn_id"] == "turn-1"
    assert compression_snapshot["status"] == "ok"
    assert compression_snapshot["response"] is not None

    title_provider = FakeCompressionProvider()
    fake_db_helper = SimpleNamespace(
        get_platform_session_by_id=AsyncMock(
            return_value=SimpleNamespace(display_name=None)
        ),
        update_platform_session=AsyncMock(),
    )
    monkeypatch.setattr(
        "astrbot.core.db_helper",
        fake_db_helper,
        raising=False,
    )
    title_event = SimpleNamespace(
        session_id="webchat!FriendMessage!session-1",
        unified_msg_origin=UMO,
    )
    await astr_main_agent._handle_webchat(
        title_event,
        SimpleNamespace(prompt="hello"),
        title_provider,
    )

    items = request_snapshots.list_for_session(SESSION_ID)["items"]
    title_items = [item for item in items if item["kind"] == "title"]
    assert len(title_items) == 1
    title_snapshot = request_snapshots.get(title_items[0]["snapshot_id"])
    assert title_snapshot["kind"] == "title"
    assert title_snapshot["turn_id"] is None
    assert title_snapshot["session_id"] == SESSION_ID
    assert title_snapshot["umo"] == UMO
    assert title_snapshot["status"] == "ok"
    assert title_snapshot["response"] is not None


@pytest.mark.asyncio
async def test_runner_attributes_compression_to_its_session():
    provider = FakeSnapshotProvider(tool_calls=False)
    provider.provider_config["max_context_tokens"] = 100
    fixture = FakeRunnerFixture(provider)
    runner = await fixture.reset(
        contexts=[
            {"role": "user", "content": "old question " * 40},
            {"role": "assistant", "content": "old answer " * 40},
        ],
        llm_compress_provider=FakeCompressionProvider(),
    )

    with pytest.raises(ContextCapacityError):
        # The tiny window cannot fit the request even after summarizing; the
        # summary attempt itself is what this test attributes.
        [event async for event in runner.step_until_done(max_step=2)]

    items = request_snapshots.list_for_session(SESSION_ID)["items"]
    compression_items = [item for item in items if item["kind"] == "compression"]
    assert compression_items, [item["kind"] for item in items]
    compression_snapshot = request_snapshots.get(compression_items[0]["snapshot_id"])
    assert compression_snapshot["session_id"] == SESSION_ID
    assert compression_snapshot["umo"] == UMO
    assert compression_snapshot["turn_id"] == runner._snapshot_turn_id
    # Compression gets its own invocation id; it must not borrow the chat
    # invocation (which may not exist yet when the summary runs).
    assert compression_snapshot["invocation_id"] is not None


@pytest.mark.asyncio
async def test_subagent_never_records_a_compression_snapshot():
    provider = FakeSnapshotProvider(tool_calls=False)
    provider.provider_config["max_context_tokens"] = 100
    fixture = FakeRunnerFixture(provider)
    fixture.context.extra["is_subagent"] = True
    runner = await fixture.reset(
        contexts=[
            {"role": "user", "content": "old question " * 40},
            {"role": "assistant", "content": "old answer " * 40},
        ],
        llm_compress_provider=FakeCompressionProvider(),
    )

    with pytest.raises(ContextCapacityError):
        # The summary request happens before the capacity check fails, so this
        # exercises the compression scope of a subagent run.
        [event async for event in runner.step_until_done(max_step=2)]

    items = request_snapshots.list_for_session(SESSION_ID)["items"]
    assert [item for item in items if item["kind"] == "compression"] == []


@pytest.mark.asyncio
async def test_manual_compact_records_a_compression_snapshot():
    """``/compact`` attributes its summary request to the conversation.

    The command runs outside the agent runner, so the compressor has to carry
    the session itself; otherwise the capture hook drops the request.
    """

    provider = FakeCompressionProvider()
    conversation_manager = MagicMock()
    conversation_manager.get_curr_conversation_id = AsyncMock(return_value=SESSION_ID)
    conversation_manager.get_conversation = AsyncMock(
        return_value=SimpleNamespace(
            history=[
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "u3"},
            ]
        )
    )
    conversation_manager.update_conversation = AsyncMock()
    context = MagicMock()
    context.get_config.return_value = {}
    context.get_using_provider.return_value = provider
    context.get_provider_by_id.return_value = None
    context.conversation_manager = conversation_manager
    event = MagicMock()
    event.unified_msg_origin = UMO
    event.send = AsyncMock()

    await CompactCommand(context=context).compact(event)

    items = request_snapshots.list_for_session(SESSION_ID)["items"]
    compression_items = [item for item in items if item["kind"] == "compression"]
    assert compression_items, [item["kind"] for item in items]
    snapshot = request_snapshots.get(compression_items[0]["snapshot_id"])
    assert snapshot["session_id"] == SESSION_ID
    assert snapshot["umo"] == UMO
    # Manual compaction is not part of a chat turn.
    assert snapshot["turn_id"] is None


@pytest.mark.asyncio
async def test_large_tool_result_records_the_overflow_reference(tmp_path: Path):
    big_result = "x" * 120_000
    fixture = FakeRunnerFixture(FakeSnapshotProvider(tool_result=big_result))
    runner = await fixture.reset()
    runner.tool_result_overflow_dir = str(tmp_path)
    runner.read_tool = _tool_set().get_tool("read_file")

    events = [event async for event in runner.step_until_done(max_step=4)]
    execution = _stored(_snapshot_items(events[0]))["tool_executions"][0]

    ref = execution["result_ref"]
    assert ref is not None
    assert Path(ref).parent == tmp_path
    assert Path(ref).read_text(encoding="utf-8") == big_result
    # The preview and the byte count still describe the complete output.
    assert execution["result_bytes"] == len(big_result.encode("utf-8"))
    assert execution["result_preview"].startswith("x" * 100)


@pytest.mark.asyncio
async def test_multi_chunk_tool_result_preview_accumulates():
    executor = FakeSnapshotToolExecutor(chunks=["first part", "second part"])
    fixture = FakeRunnerFixture(
        FakeSnapshotProvider(),
        tool_executor=executor,
    )
    runner = await fixture.reset()

    events = [event async for event in runner.step_until_done(max_step=4)]
    execution = _stored(_snapshot_items(events[0]))["tool_executions"][0]

    assert execution["result_preview"] == "first part\n\nsecond part"
    assert execution["result_bytes"] == len(b"first part\n\nsecond part")


class FakeProvenanceProvider:
    """Minimal provider surface consumed by ``capture``."""

    provider_config = {"api_base": "https://api.example.com/v1"}

    def get_model(self) -> str:
        return "m1"

    def get_current_key(self) -> str:
        return "sk-secret-1234"

    def get_keys(self) -> list[str]:
        return ["sk-secret-1234"]


def _capture_system(system: str, *, session_id: str = SESSION_ID) -> dict:
    with request_snapshot_scope(
        session_id=session_id,
        umo=f"webchat:FriendMessage:{session_id}",
        turn_id="turn-1",
        invocation_id="inv-1",
    ):
        snapshot_id = request_snapshots.capture(
            FakeProvenanceProvider(),
            {
                "model": "m1",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "hi"},
                ],
            },
            adapter="openai_chat",
        )
    assert snapshot_id is not None
    snapshot = request_snapshots.get(snapshot_id)
    assert snapshot is not None
    return snapshot


def test_provenance_marks_exact_and_approximate_parts(monkeypatch: pytest.MonkeyPatch):
    _enable(monkeypatch)
    persona_block = "\n# Persona Instructions\n\nYou are A\n"
    skills_block = "\n# Skills\n\nSKILLS\n"
    record_provenance(SESSION_ID, "persona", persona_block)
    record_provenance(SESSION_ID, "skills", skills_block)

    # Repeated text can no longer be located uniquely, so it stays approximate.
    system = f"{persona_block}ENV\n{skills_block}{skills_block}"
    parts = _capture_system(system)["provenance"]

    assert [part["label"] for part in parts] == ["persona", "skills"]
    assert parts[0]["approx"] is False
    assert parts[0]["span"] == [0, len(persona_block)]
    assert parts[1]["approx"] is True
    assert parts[1]["span"] is None


def test_assembly_registers_prompt_provenance(monkeypatch: pytest.MonkeyPatch):
    _enable(monkeypatch)
    req = SimpleNamespace(
        session_id=UMO, conversation=SimpleNamespace(cid="conversation-1")
    )
    persona_block = "\n# Persona Instructions\n\nYou are B\n"

    astr_main_agent._record_prompt_provenance(req, "persona", persona_block)
    astr_main_agent._record_prompt_provenance(req, "persona", persona_block)
    parts = _capture_system(f"{persona_block}SYS")["provenance"]

    assert len(parts) == 1
    assert parts[0]["label"] == "persona"
    assert parts[0]["approx"] is False

    # No WebChat UMO means no session to attribute to.
    astr_main_agent._record_prompt_provenance(
        SimpleNamespace(session_id=None, conversation=None), "persona", "OTHER"
    )
    assert len(_capture_system("OTHER")["provenance"]) == 1
