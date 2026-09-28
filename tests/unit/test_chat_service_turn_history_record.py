"""One agent turn must persist as a single history record.

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
turn-wide.
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
        return SimpleNamespace(id=row_id, created_at=datetime.now(timezone.utc))

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
