"""ChatService plumbing for the ChatUI developer-mode request snapshots.

Task 7 of the developer-mode plan: the run keeps the live envelopes it was
handed, binds the persisted bot row to the turn, and exposes read-only
readers for the (still unbuilt) API routes. The snapshot store itself is
covered by ``test_request_snapshot*.py``; these tests pin the service seam.

Author: elecvoid243, 2026-11-15
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.platform.sources.webchat import webchat_event
from astrbot.core.provider import request_snapshot
from astrbot.core.provider.request_snapshot import (
    request_snapshot_scope,
    request_snapshots,
)
from astrbot.dashboard.services.chat_service import (
    BotMessageAccumulator,
    ChatRunState,
    ChatService,
    ChatServiceError,
    _parse_snapshot_event_items,
    build_bot_history_content,
)

RUN_ID = "run-1"
SESSION_ID = "s1"
OWNER = "alice"


class _ConfigManager:
    """Minimal AstrBotConfigManager stand-in: only `confs` is read."""

    def __init__(self, conf: AstrBotConfig) -> None:
        self.confs = {"default": conf}


class _FakeDb:
    """Only the platform-session lookup used by the snapshot readers."""

    def __init__(self, sessions: dict[str, SimpleNamespace] | None = None) -> None:
        self.sessions = sessions or {}

    async def get_platform_session_by_id(self, session_id: str):
        return self.sessions.get(session_id)


def _session(session_id: str, creator: str) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        creator=creator,
        platform_id="webchat",
    )


def _service(
    tmp_path,
    *,
    developer_mode: bool = False,
    sessions: dict[str, SimpleNamespace] | None = None,
) -> tuple[ChatService, object]:
    """Build the service with a real config file and a fake session lookup."""
    config_path = tmp_path / "cmd_config.json"
    conf = AstrBotConfig(
        config_path=str(config_path),
        default_config={"chatui": copy.deepcopy(DEFAULT_CONFIG["chatui"])},
        schema=None,
    )
    if developer_mode:
        conf["chatui"]["developer_mode"] = True
    service = ChatService.__new__(ChatService)
    service.core_lifecycle = SimpleNamespace(astrbot_config_mgr=_ConfigManager(conf))
    service.db = _FakeDb(sessions)
    return service, config_path


class _FakeProvider:
    """Minimal provider surface consumed by ``request_snapshots.capture``."""

    provider_config = {
        "api_base": "https://api.example.com/v1",
        "proxy": "",
        "timeout": 60,
    }
    SNAPSHOT_ADAPTER = "openai_chat"

    def meta(self) -> SimpleNamespace:
        return SimpleNamespace(id="p1", type="openai_chat", model="m1")

    def get_model(self) -> str:
        return "m1"

    def get_current_key(self) -> str:
        return "sk-secret-1234"

    def get_keys(self) -> list[str]:
        return ["sk-secret-1234"]


@pytest.fixture(autouse=True)
def _isolated_recorder(monkeypatch: pytest.MonkeyPatch):
    """Serve the singleton recorder with developer mode on, then reset it."""
    monkeypatch.setattr(request_snapshot, "developer_mode_enabled", lambda: True)
    request_snapshots.reset()
    yield
    request_snapshots.reset()


def _capture(
    *,
    turn_id: str = "t1",
    session_id: str = SESSION_ID,
    raw: dict | None = None,
) -> str:
    body = raw if raw is not None else {"messages": [{"role": "user", "content": "hi"}]}
    with request_snapshot_scope(
        session_id=session_id,
        umo=f"webchat:FriendMessage:{session_id}",
        turn_id=turn_id,
        kind="chat",
    ):
        snapshot_id = request_snapshots.capture(_FakeProvider(), body)
    assert snapshot_id, "capture should have recorded the snapshot"
    return snapshot_id


def _run() -> ChatRunState:
    return ChatRunState(
        run_id=RUN_ID,
        username=OWNER,
        session_id=SESSION_ID,
        llm_checkpoint_id="ckpt-1",
        platform_history_id="webchat",
        back_queue=asyncio.Queue(),
    )


class _FakeHistoryManager:
    """Records the writes a run performs against the history store."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self._next_id = 1

    async def insert(self, **kwargs):
        row_id = self._next_id
        self._next_id += 1
        self.rows[row_id] = kwargs["content"]
        return SimpleNamespace(
            id=row_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            model_dump=lambda: {"id": row_id, **kwargs},
        )

    async def update(self, message_id, content=None, llm_checkpoint_id=None):
        if content is not None:
            self.rows[message_id] = content


def _text(text: str, msg_type: str = "plain") -> dict:
    return {
        "type": msg_type,
        "data": text,
        "streaming": True,
        "message_id": RUN_ID,
    }


def _snapshot_event(items: list[dict]) -> dict:
    return {
        "type": "plain",
        "chain_type": "llm_request_snapshots",
        "data": json.dumps({"items": items}, ensure_ascii=False),
        "streaming": False,
        "message_id": RUN_ID,
    }


async def _drive(payloads: list[dict]) -> tuple[ChatService, ChatRunState]:
    """Run one turn through the consumer and return service and run state."""
    service = ChatService.__new__(ChatService)
    service.platform_history_mgr = _FakeHistoryManager()
    service.running_convs = {}
    service.chat_runs = {}
    service.chat_runs_by_session = {}
    run = _run()
    for payload in payloads:
        await run.back_queue.put(payload)
    await service._consume_chat_run(run)
    return service, run


def test_merge_snapshot_event_dedupes_sets_turn_and_skips_history():
    service = ChatService.__new__(ChatService)
    run = _run()
    queue: asyncio.Queue = asyncio.Queue()
    run.subscribers.add(queue)
    first = {"snapshot_id": "snap-1", "turn_id": None, "status": "pending"}
    second = {"snapshot_id": "snap-2", "turn_id": "t1", "status": "ok"}

    service._merge_snapshot_event(run, [first, second])
    service._merge_snapshot_event(run, [{**second, "status": "error"}])

    assert set(run.dev_snapshots) == {"snap-1", "snap-2"}
    # A repeated id replaces the envelope instead of appending a duplicate.
    assert run.dev_snapshots["snap-2"]["status"] == "error"
    assert run.dev_turn_id == "t1"

    published = [queue.get_nowait()[1] for _ in range(queue.qsize())]
    assert [event["type"] for event in published] == ["llm_request_snapshots"] * 2
    assert published[0]["data"] == {"items": [first, second]}

    # Snapshot telemetry must never leak into the persisted bot content.
    run.history_parts.append({"type": "plain", "text": "hi"})
    content = build_bot_history_content(run.history_parts, agent_stats=run.agent_stats)
    assert "llm_request_snapshots" not in content
    assert "snap-1" not in json.dumps(content)


def test_merge_snapshot_event_ignores_malformed_items():
    service = ChatService.__new__(ChatService)
    run = _run()

    service._merge_snapshot_event(
        run,
        [None, "junk", {"status": "ok"}, {"snapshot_id": "snap-1"}],
    )

    assert set(run.dev_snapshots) == {"snap-1"}
    assert run.dev_turn_id is None


def test_snapshot_event_payload_parsing_never_raises():
    assert _parse_snapshot_event_items("not json") == []
    assert _parse_snapshot_event_items('{"items": "nope"}') == []
    assert _parse_snapshot_event_items(None) == []
    assert _parse_snapshot_event_items('{"items": [{"snapshot_id": "snap-1"}]}') == [
        {"snapshot_id": "snap-1"}
    ]


@pytest.mark.asyncio
async def test_message_binding_resolves_turn_and_expires_only_on_miss(tmp_path):
    first = _capture(turn_id="t1")
    _capture(turn_id="t2")
    request_snapshots.bind_message(7, "t1", SESSION_ID)
    service, _ = _service(
        tmp_path,
        sessions={SESSION_ID: _session(SESSION_ID, OWNER)},
    )

    hit = await service.get_llm_snapshots(OWNER, SESSION_ID, message_id=7)

    assert [item["snapshot_id"] for item in hit["items"]] == [first]
    assert hit["expired"] is False

    miss = await service.get_llm_snapshots(OWNER, SESSION_ID, message_id=999)

    assert miss["items"] == []
    assert miss["expired"] is True


@pytest.mark.asyncio
async def test_two_turns_in_one_session_stay_separate(tmp_path):
    _capture(turn_id="t1")
    _capture(turn_id="t2")
    service, _ = _service(
        tmp_path,
        sessions={SESSION_ID: _session(SESSION_ID, OWNER)},
    )

    first = await service.get_llm_snapshots(OWNER, SESSION_ID, turn_id="t1")
    second = await service.get_llm_snapshots(OWNER, SESSION_ID, turn_id="t2")
    everything = await service.get_llm_snapshots(OWNER, SESSION_ID)

    assert {item["turn_id"] for item in first["items"]} == {"t1"}
    assert {item["turn_id"] for item in second["items"]} == {"t2"}
    assert {item["turn_id"] for item in everything["items"]} == {"t1", "t2"}
    assert everything["enabled"] is False
    assert everything["limits"]["max_snapshots_per_session"] == 128


@pytest.mark.asyncio
async def test_list_enabled_flag_follows_the_developer_mode_config(tmp_path):
    _capture()
    on_service, _ = _service(
        tmp_path,
        developer_mode=True,
        sessions={SESSION_ID: _session(SESSION_ID, OWNER)},
    )

    listed = await on_service.get_llm_snapshots(OWNER, SESSION_ID)

    assert listed["enabled"] is True
    assert listed["items"]


@pytest.mark.asyncio
async def test_foreign_or_missing_session_is_denied(tmp_path):
    _capture()
    service, _ = _service(
        tmp_path,
        sessions={SESSION_ID: _session(SESSION_ID, OWNER)},
    )

    with pytest.raises(ChatServiceError, match="Permission denied"):
        await service.get_llm_snapshots("bob", SESSION_ID)
    with pytest.raises(ChatServiceError, match="Permission denied"):
        await service.get_llm_snapshots(OWNER, "ghost")


@pytest.mark.asyncio
async def test_snapshot_detail_splits_envelope_raw_and_view(tmp_path):
    raw = {
        "model": "m1",
        "messages": [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 32,
    }
    snapshot_id = _capture(raw=raw)
    service, _ = _service(
        tmp_path,
        sessions={SESSION_ID: _session(SESSION_ID, OWNER)},
    )

    detail = await service.get_llm_snapshot(OWNER, snapshot_id)

    assert set(detail) == {"envelope", "raw", "view"}
    # The raw payload lives in exactly one place: `raw`.
    assert "raw" not in detail["envelope"]
    assert detail["envelope"]["snapshot_id"] == snapshot_id
    assert detail["raw"]["messages"][-1]["content"] == "hello"
    assert detail["view"]["system"]["text"] == "be nice"
    assert [message["role"] for message in detail["view"]["messages"]] == [
        "system",
        "user",
    ]
    assert detail["view"]["params"]["max_tokens"] == 32


@pytest.mark.asyncio
async def test_snapshot_detail_rejects_foreign_and_unknown_snapshots(tmp_path):
    snapshot_id = _capture()
    foreign, _ = _service(
        tmp_path,
        sessions={SESSION_ID: _session(SESSION_ID, "bob")},
    )
    missing_session, _ = _service(tmp_path)

    with pytest.raises(ChatServiceError, match="Permission denied"):
        await foreign.get_llm_snapshot(OWNER, snapshot_id)
    with pytest.raises(ChatServiceError, match="Permission denied"):
        await missing_session.get_llm_snapshot(OWNER, snapshot_id)
    with pytest.raises(ChatServiceError, match="Snapshot not found"):
        await missing_session.get_llm_snapshot(OWNER, "unknown")


def test_developer_mode_toggle_preserves_sibling_chatui_keys(tmp_path):
    service, config_path = _service(tmp_path)

    assert service.get_chatui_developer_mode() is False
    assert service.set_chatui_developer_mode(True) == {"enabled": True}

    stored = json.loads(config_path.read_text(encoding="utf-8-sig"))["chatui"]
    assert stored["developer_mode"] is True
    assert stored["thinking_effort"]["active_preset"] == "deepseek-v4.1"
    assert stored["quick_messages"]["items"] == []
    assert service.get_chatui_developer_mode() is True

    with pytest.raises(ChatServiceError):
        service.set_chatui_developer_mode("yes")

    stored = json.loads(config_path.read_text(encoding="utf-8-sig"))["chatui"]
    assert stored["developer_mode"] is True


def test_snapshot_telemetry_never_becomes_a_literal_history_part():
    acc = BotMessageAccumulator()

    acc.add_plain(
        json.dumps({"items": [{"snapshot_id": "snap-1"}]}),
        chain_type="llm_request_snapshots",
        streaming=False,
    )

    assert acc.build_message_parts(include_pending_tool_calls=True) == []
    assert acc.has_content() is False


def test_orphan_telemetry_skip_includes_snapshots():
    source = inspect.getsource(webchat_event._persist_bot_reply_if_orphan)

    assert "llm_request_snapshots" in source


@pytest.mark.asyncio
async def test_snapshot_event_binds_the_bot_message_saved_afterwards():
    snapshot_id = _capture(turn_id="t1")
    item = {"snapshot_id": snapshot_id, "turn_id": "t1", "status": "ok"}

    service, run = await _drive(
        [
            _text("answer"),
            _snapshot_event([item]),
            _text("answer", msg_type="complete"),
            {"type": "end", "message_id": RUN_ID},
        ]
    )

    assert run.dev_turn_id == "t1"
    assert set(run.dev_snapshots) == {snapshot_id}
    # The row created by the save carries the turn binding.
    bound = request_snapshots.list_for_session(SESSION_ID, message_id=1)
    assert [entry["snapshot_id"] for entry in bound["items"]] == [snapshot_id]
    assert bound["expired"] is False
    assert "llm_request_snapshots" not in service.platform_history_mgr.rows[1]


@pytest.mark.asyncio
async def test_late_snapshot_event_binds_the_existing_history_record():
    snapshot_id = _capture(turn_id="t1")
    item = {"snapshot_id": snapshot_id, "turn_id": "t1", "status": "ok"}

    await _drive(
        [
            _text("answer"),
            _text("answer", msg_type="complete"),
            _snapshot_event([item]),
            {"type": "end", "message_id": RUN_ID},
        ]
    )

    bound = request_snapshots.list_for_session(SESSION_ID, message_id=1)
    assert [entry["snapshot_id"] for entry in bound["items"]] == [snapshot_id]
