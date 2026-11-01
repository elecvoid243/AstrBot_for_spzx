"""Keep turn saves together, splitting only at chronological user boundaries.

Author: elecvoid243
Date: 2026-09-28

A turn saves mid-way whenever the agent calls ``ask_user_choice``: the
plugin's ``interactive_choice`` event lands in ``BotMessageAccumulator``, and
the consumer persists it immediately so the question survives a hard refresh
while it waits for an answer.

Each of those mid-turn saves used to start a *new* history row. The live
stream accumulates the whole turn into one bubble, so the ChatUI only showed
the split after a refresh: one bubble — each with its own "worked for ..."
capsule — per save, while the agent had run a single turn. The row is now
created once and rewritten by every later save, with the parts accumulated
segment-wide. Follow-ups deliberately start a new segment so live display
and restored history share the same chronology.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from astrbot.dashboard.services.chat_service import ChatRunState, ChatService

RUN_ID = "run-1"


class _FakeHistoryManager:
    """Records the writes a run performs against the history store."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.inserts = 0
        self.updates = 0
        self._next_id = 1

    async def insert(self, **kwargs):
        self.inserts += 1
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
        self.updates += 1
        if content is not None:
            self.rows[message_id] = content


def _choice_event(request_id: str) -> dict:
    """One plugin-emitted box event (no ``streaming`` flag, like the plugin)."""
    return {
        "type": "plain",
        "chain_type": "interactive_choice",
        "data": json.dumps(
            {
                "request_id": request_id,
                "spec": {
                    "type": "interactive_choice",
                    "prompt": f"question {request_id}",
                    "options": [
                        {"id": "A", "label": "alpha"},
                        {"id": "B", "label": "beta"},
                    ],
                },
            },
            ensure_ascii=False,
        ),
        "message_id": RUN_ID,
    }


def _think(text: str) -> dict:
    return {
        "type": "plain",
        "chain_type": "reasoning",
        "data": text,
        "streaming": True,
        "message_id": RUN_ID,
    }


def _text(text: str, msg_type: str = "plain") -> dict:
    return {
        "type": msg_type,
        "data": text,
        "streaming": True,
        "message_id": RUN_ID,
    }


async def _drive_turn(payloads: list[dict]) -> _FakeHistoryManager:
    """Run one turn through the consumer and return the history manager."""
    service = ChatService.__new__(ChatService)
    manager = _FakeHistoryManager()
    service.platform_history_mgr = manager
    service.running_convs = {}
    service.chat_runs = {}
    service.chat_runs_by_session = {}

    run = ChatRunState(
        run_id=RUN_ID,
        username="user",
        session_id="session-1",
        llm_checkpoint_id="ckpt-1",
        platform_history_id="webchat",
        back_queue=asyncio.Queue(),
    )
    for payload in payloads:
        await run.back_queue.put(payload)

    await service._consume_chat_run(run)
    return manager


@pytest.mark.asyncio
async def test_turn_with_several_choices_writes_one_history_record() -> None:
    manager = await _drive_turn(
        [
            _think("think 1"),
            _choice_event("r1"),
            _think("think 2"),
            _choice_event("r2"),
            _think("think 3"),
            _text("final answer"),
            _text("final answer", msg_type="complete"),
            {"type": "end", "message_id": RUN_ID},
        ]
    )

    assert manager.inserts == 1
    assert manager.updates == 2
    [content] = manager.rows.values()
    parts = content["message"]
    assert [part["type"] for part in parts] == [
        "think",
        "interactive_choice",
        "think",
        "interactive_choice",
        "think",
        "plain",
    ]
    # Both boxes survive in the single row the turn owns.
    assert [
        part["request_id"] for part in parts if part["type"] == "interactive_choice"
    ] == ["r1", "r2"]


@pytest.mark.asyncio
async def test_turn_without_a_mid_turn_save_writes_one_record_without_updates() -> None:
    manager = await _drive_turn(
        [
            _think("think"),
            _text("final answer"),
            _text("final answer", msg_type="complete"),
            {"type": "end", "message_id": RUN_ID},
        ]
    )

    assert manager.inserts == 1
    assert manager.updates == 0
    [content] = manager.rows.values()
    assert [part["type"] for part in content["message"]] == ["think", "plain"]


@pytest.mark.asyncio
async def test_followups_split_stream_into_chronological_durable_rows() -> None:
    completions = [asyncio.get_running_loop().create_future() for _ in range(2)]
    boundaries = [
        {
            "type": "_user_message_boundary",
            "completion": completion,
            "history": {
                "platform_id": "webchat",
                "user_id": "session-1",
                "content": {"type": "user", "message": [{"type": "plain", "text": text}]},
            },
        }
        for completion, text in zip(completions, ["follow-up 1", "follow-up 2"])
    ]
    manager = await _drive_turn([
        _text("before"), boundaries[0], _text("middle"), boundaries[1],
        _text("after"), _text("beforemiddleafter", msg_type="complete"),
        {"type": "end", "message_id": RUN_ID},
    ])
    assert [row["type"] for row in manager.rows.values()] == ["bot", "user", "bot", "user", "bot"]
    assert [row["message"][0]["text"] for row in manager.rows.values()] == [
        "before", "follow-up 1", "middle", "follow-up 2", "after",
    ]
    assert all(completion.done() and completion.exception() is None for completion in completions)


@pytest.mark.asyncio
async def test_boundary_preserves_a_previously_saved_choice_segment() -> None:
    completion = asyncio.get_running_loop().create_future()
    manager = await _drive_turn([
        _choice_event("r1"),
        {"type": "_user_message_boundary", "completion": completion, "history": {
            "platform_id": "webchat", "user_id": "session-1",
            "content": {"type": "user", "message": [{"type": "plain", "text": "follow-up"}]},
        }},
        _text("answer"), {"type": "end", "message_id": RUN_ID},
    ])
    assert [row["type"] for row in manager.rows.values()] == ["bot", "user", "bot"]
    assert manager.rows[1]["message"][0]["request_id"] == "r1"


@pytest.mark.asyncio
@pytest.mark.parametrize("followup_count", [1, 2])
async def test_pending_tool_survives_followups_in_stream_snapshot_and_history(
    followup_count: int,
) -> None:
    """Keep tool identity while boundaries arrive before its result."""
    service = ChatService.__new__(ChatService)
    manager = _FakeHistoryManager()
    service.platform_history_mgr = manager
    service.running_convs = {}
    service.chat_runs_by_session = {"session-1": {RUN_ID}}
    run = ChatRunState(
        run_id=RUN_ID,
        username="user",
        session_id="session-1",
        llm_checkpoint_id="ckpt-1",
        platform_history_id="webchat",
        back_queue=asyncio.Queue(),
    )
    service.chat_runs = {RUN_ID: run}
    subscriber = asyncio.Queue()
    run.subscribers.add(subscriber)
    tool = {
        "id": "python-1",
        "name": "astrbot_execute_python",
        "args": {"code": "import time; time.sleep(20)"},
        "ts": 1,
    }
    run.back_queue.put_nowait(_think("Run the first program."))
    run.back_queue.put_nowait({
        **_text(json.dumps(tool)), "chain_type": "tool_call",
    })
    run.task = asyncio.create_task(service._consume_chat_run(run))
    segments = []
    try:
        for index in range(followup_count):
            completion = asyncio.get_running_loop().create_future()
            run.back_queue.put_nowait({
                "type": "_user_message_boundary", "completion": completion,
                "history": {
                    "platform_id": "webchat", "user_id": "session-1",
                    "content": {"type": "user", "message": [{"type": "plain", "text": f"follow-up {index}"}]},
                },
            })
            await asyncio.wait_for(completion, timeout=1)
            while True:
                _, payload = await asyncio.wait_for(subscriber.get(), timeout=1)
                if payload["type"] == "message_segment":
                    break
            segments.append(payload)
            expected = [{"type": "tool_call", "tool_calls": [tool]}]
            assert payload["data"]["content"]["message"] == expected
            [snapshot] = service.get_active_chat_runs("user", "session-1")
            assert snapshot["content"]["message"] == expected

        run.back_queue.put_nowait({
            **_text(json.dumps({"id": "python-1", "result": "slept", "ts": 2})),
            "chain_type": "tool_call_result",
        })
        second = {**tool, "id": "python-2", "args": {"code": "print('Hello world')"}, "ts": 3}
        run.back_queue.put_nowait({**_text(json.dumps(second)), "chain_type": "tool_call"})
        run.back_queue.put_nowait({
            **_text(json.dumps({"id": "python-2", "result": "Hello world", "ts": 4})),
            "chain_type": "tool_call_result",
        })
        run.back_queue.put_nowait({"type": "end", "message_id": RUN_ID})
        await asyncio.wait_for(run.task, timeout=1)
        calls = [
            call for row in manager.rows.values() for part in row["message"]
            for call in part.get("tool_calls", [])
        ]
        assert calls == [
            {**tool, "result": "slept", "finished_ts": 2},
            {**second, "result": "Hello world", "finished_ts": 4},
        ]
        assert all("result" not in segment["data"]["content"]["message"][0]["tool_calls"][0] for segment in segments)
    finally:
        if not run.task.done():
            run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
