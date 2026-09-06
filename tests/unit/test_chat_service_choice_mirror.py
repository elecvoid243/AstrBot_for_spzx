"""The ``_consume_chat_run`` ask-user-choice mirror (spec §4.5).

Agent Teams runners collect member output from the conversation's
system-event subscribers, but the ask_user_choice plugin pushes its choice
payloads straight onto the run's back_queue. The primary consumer must
mirror those payloads to the system stream — additively only, the chat
page's own choice flow is untouched.
"""

# Author: astrbot-dev
# Date: 2026-09-06
# Plan: .superpowers/sdd/2026-09-05-agent-teams-refinements-3/task-2-brief.md

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
    webchat_queue_mgr,
)
from astrbot.dashboard.services.chat_service import ChatService


def _make_service() -> ChatService:
    service = ChatService.__new__(ChatService)
    service.db = MagicMock()
    service.core_lifecycle = MagicMock()
    service.conv_mgr = MagicMock()
    service.platform_history_mgr = MagicMock()
    service.umop_config_router = MagicMock()
    service.running_convs = {}
    service.chat_runs = {}
    service.chat_runs_by_session = {}
    service._branch_relations = None
    # A real record shape: the choice part makes the run flush a
    # message_saved payload, which reads id/created_at off the record.
    saved_record = MagicMock()
    saved_record.id = 1
    saved_record.created_at = datetime.now()
    service.save_bot_message = AsyncMock(return_value=saved_record)
    return service


@pytest.mark.asyncio
async def test_choice_payloads_are_mirrored_to_system_subscribers():
    service = _make_service()
    session_id = f"conv-{id(service)}-choice"
    run_id = f"choice-run-{id(service)}"
    await service.register_synthetic_chat_run(session_id, run_id, "alice")
    run = service.chat_runs[run_id]
    mirror = webchat_queue_mgr.subscribe_system(session_id)
    spec_payload = {
        "type": "plain",
        "data": json.dumps(
            {
                "request_id": "req-1",
                "spec": {
                    "type": "interactive_choice",
                    "prompt": "Pick one",
                    "options": [{"id": "A", "label": "alpha"}],
                },
            }
        ),
        "streaming": False,
        "chain_type": "interactive_choice",
        "message_id": run_id,
    }
    resolved_payload = {
        "type": "interactive_choice_resolved",
        "data": {"request_id": "req-1", "reason": "picked A", "umo": "webchat!d!c"},
        "streaming": False,
        "message_id": run_id,
    }
    try:
        await webchat_queue_mgr.put_back_queue(run_id, spec_payload)
        await webchat_queue_mgr.put_back_queue(run_id, resolved_payload)
        await webchat_queue_mgr.put_back_queue(
            run_id,
            {"type": "end", "data": "", "streaming": False, "message_id": run_id},
        )
        await asyncio.wait_for(run.task, timeout=2)
    finally:
        webchat_queue_mgr.unsubscribe_system(session_id, mirror)

    mirrored = []
    while not mirror.empty():
        mirrored.append(mirror.get_nowait())
    assert spec_payload in mirrored
    assert resolved_payload in mirrored
    # Additive only: run completion and cleanup are unchanged.
    assert run.status == "completed"
    assert run_id not in service.chat_runs


@pytest.mark.asyncio
async def test_non_choice_payloads_are_not_mirrored():
    """The mirror must stay scoped to choice payloads; ordinary run output
    keeps flowing only through the run stream."""
    service = _make_service()
    session_id = f"conv-{id(service)}-plain"
    run_id = f"plain-run-{id(service)}"
    await service.register_synthetic_chat_run(session_id, run_id, "alice")
    run = service.chat_runs[run_id]
    mirror = webchat_queue_mgr.subscribe_system(session_id)
    plain_payload = {
        "type": "plain",
        "data": "普通回复",
        "streaming": False,
        "message_id": run_id,
    }
    try:
        await webchat_queue_mgr.put_back_queue(run_id, plain_payload)
        await webchat_queue_mgr.put_back_queue(
            run_id,
            {"type": "end", "data": "", "streaming": False, "message_id": run_id},
        )
        await asyncio.wait_for(run.task, timeout=2)
    finally:
        webchat_queue_mgr.unsubscribe_system(session_id, mirror)

    assert mirror.empty()
