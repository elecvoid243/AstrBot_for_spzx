"""Transcript repo tests against a real on-disk SQLite database."""

import pytest

from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_transcript_roundtrip_order_before_id_and_limit(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()

    for i in range(5):
        await db.append_agent_team_run_message(
            run_id="r1",
            member_id="m1",
            node_id="n1" if i % 2 else None,
            round=1 if i % 2 else None,
            turn_id=f"turn-{i}",
            direction="sent" if i == 0 else "reply",
            text=None if i == 0 else f"回复 {i}",
            parts=[{"type": "interactive_choice", "options": [1, 2]}]
            if i == 3
            else None,
            metadata={"reason": "test"} if i == 4 else None,
        )

    rows = await db.get_agent_team_run_transcript("r1", "m1", before_id=None)
    assert [r.id for r in rows] == [5, 4, 3, 2, 1]  # newest first (id DESC)
    assert rows[-1].direction == "sent"
    # None-able fields and JSON columns roundtrip
    assert rows[0].meta == {"reason": "test"}
    assert rows[1].parts == [{"type": "interactive_choice", "options": [1, 2]}]
    assert rows[1].node_id == "n1" and rows[1].round == 1
    assert rows[-1].node_id is None and rows[-1].round is None

    # before_id is exclusive; ordering stays id DESC
    page = await db.get_agent_team_run_transcript("r1", "m1", before_id=3)
    assert [r.id for r in page] == [2, 1]

    page = await db.get_agent_team_run_transcript("r1", "m1", before_id=None, limit=2)
    assert [r.id for r in page] == [5, 4]

    # Transcripts are scoped per (run_id, member_id)
    await db.append_agent_team_run_message(
        run_id="r1",
        member_id="m2",
        node_id=None,
        round=None,
        turn_id="turn-0",
        direction="sent",
        text="other member",
        parts=None,
        metadata=None,
    )
    assert await db.get_agent_team_run_transcript("r1", "m2", before_id=None) != []
    rows = await db.get_agent_team_run_transcript("r1", "m1", before_id=None)
    assert [r.id for r in rows] == [5, 4, 3, 2, 1]
    assert all(r.member_id == "m1" for r in rows)


@pytest.mark.asyncio
async def test_trim_keeps_newest_rows_per_member(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "t.db"))
    await db.initialize()
    for i in range(7):
        await db.append_agent_team_run_message(
            run_id="r1",
            member_id="m1",
            node_id=None,
            round=None,
            turn_id=f"turn-{i}",
            direction="reply",
            text=f"t{i}",
            parts=None,
            metadata=None,
        )
    # Another member's transcript must be untouched by m1's trim.
    await db.append_agent_team_run_message(
        run_id="r1",
        member_id="m2",
        node_id=None,
        round=None,
        turn_id="turn-0",
        direction="sent",
        text="keep me",
        parts=None,
        metadata=None,
    )

    deleted = await db.trim_agent_team_run_transcript("r1", "m1", keep=5)
    assert deleted == 2

    rows = await db.get_agent_team_run_transcript("r1", "m1", before_id=None)
    assert [r.id for r in rows] == [7, 6, 5, 4, 3]  # newest 5 kept
    # Trimming again below the current size deletes nothing.
    assert await db.trim_agent_team_run_transcript("r1", "m1", keep=500) == 0
    assert len(await db.get_agent_team_run_transcript("r1", "m2", before_id=None)) == 1
