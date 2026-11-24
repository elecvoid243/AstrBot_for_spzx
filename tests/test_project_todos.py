import asyncio
import time
from contextlib import suppress
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text

from astrbot.core.db.po import DashboardAccount, ProjectTodo
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.utils.auth_password import hash_dashboard_password
from astrbot.dashboard.api.project_todos import Reminder, run_reminders
from astrbot.dashboard.services.chat_service import ChatServiceError
from tests.fixtures.dashboard_accounts import PASSWORD
from tests.fixtures.dashboard_accounts import account_dashboard as account_dashboard
from tests.fixtures.dashboard_accounts import fake_core_lifecycle as fake_core_lifecycle


@pytest.mark.asyncio
async def test_existing_project_board_upgrades_in_place(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "existing.db"))
    try:
        project = await db.create_chatui_project(
            creator="owner", title="Existing project"
        )
        card_id = str(uuid4())
        async with db.get_db() as session, session.begin():
            session.add(
                ProjectTodo(
                    id=card_id, project_id=project.project_id, title="Existing task"
                )
            )
        async with db.engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE chatui_project_todos DROP COLUMN checklist")
            )
            await conn.execute(
                text("ALTER TABLE chatui_project_todos DROP COLUMN labels")
            )
            await conn.execute(
                text("ALTER TABLE chatui_projects DROP COLUMN todo_lane_order")
            )
        await db.initialize()
        await db.initialize()
        upgraded = await db.get_chatui_project_by_id(project.project_id)
        assert upgraded.todo_lane_order == ["todo", "doing", "done"]
        async with db.get_db() as session:
            card = await session.get(ProjectTodo, card_id)
            assert (card.title, card.labels, card.checklist) == (
                "Existing task",
                [],
                [],
            )
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("once", None), ("daily", 9), ("weekly", 11)],
)
def test_missed_calendar_reminders_keep_the_local_hour(kind, expected):
    zone = ZoneInfo("America/New_York")
    start = int(datetime(2026, 3, 6, 9, tzinfo=zone).timestamp())
    now = int(datetime(2026, 3, 8, 18, tzinfo=zone).timestamp())
    rule = Reminder(start=start, kind=kind, timezone=zone.key, weekdays=[2, 4])
    expected_time = (
        int(datetime(2026, 3, expected, 9, tzinfo=zone).timestamp())
        if expected
        else None
    )
    assert rule.next_after(now) == expected_time


@pytest.mark.asyncio
async def test_project_board_ownership_dispatch_retry_and_reminders(account_dashboard):
    server, core, db = account_dashboard
    app = server.asgi_app
    admin = await db.create_chatui_project(
        creator="original-admin", title="Admin board"
    )
    user = DashboardAccount(
        username="member", role="user", password_hash=hash_dashboard_password(PASSWORD)
    )
    async with db.get_db() as session, session.begin():
        session.add(user)
    project = await db.create_chatui_project(
        creator=user.account_id, title="Member board"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:

        async def login(name):
            response = await client.post(
                "/api/v1/auth/login", json={"username": name, "password": PASSWORD}
            )
            return {"Authorization": "Bearer " + response.json()["data"]["token"]}

        member_headers = await login("member")
        admin_headers = await login("original-admin")
        prefix = "/api/project-todos"
        now = int(time.time())
        body = {
            "title": "Review layout",
            "description": "Verify both themes",
            "checklist": [{"id": str(uuid4()), "title": "Mobile", "done": False}],
            "labels": [{"id": str(uuid4()), "name": "Design", "color": "purple"}],
            "reminder": {"start": now - 9 * 3600, "kind": "hours", "hours": 4},
        }
        cid = str(uuid4())
        member_card = f"{prefix}/{project.project_id}/{cid}"
        admin_card = f"{prefix}/{admin.project_id}/{uuid4()}"
        assert (
            await client.put(member_card, json=body, headers=member_headers)
        ).status_code == 200
        assert (
            await client.put(admin_card, json=body, headers=admin_headers)
        ).status_code == 200
        assert (
            await client.put(admin_card, json=body, headers=member_headers)
        ).status_code == 404
        assert (
            await client.get(f"{prefix}/{admin.project_id}", headers=member_headers)
        ).status_code == 404
        delivered = asyncio.Event()
        core.notify_project_todo = AsyncMock(
            side_effect=lambda notices: delivered.set()
        )
        worker = asyncio.create_task(run_reminders(app))
        try:
            async with asyncio.timeout(5):
                await delivered.wait()
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        core.notify_project_todo.assert_awaited_once()
        desktop = core.notify_project_todo.await_args.args[0]
        assert [(n["project_id"], n["description"]) for n in desktop] == [
            (admin.project_id, body["description"])
        ]
        listed = (
            await client.get(f"{prefix}/{project.project_id}", headers=member_headers)
        ).json()["data"][0]
        assert listed["next_due"] == now + 3 * 3600
        assert listed["checklist"] == body["checklist"]
        assert listed["labels"] == body["labels"]
        board_url = f"{prefix}/{project.project_id}"
        layout = {
            "order": ["done", "todo", "doing"],
            "previous": ["todo", "doing", "done"],
        }
        assert (
            await client.post(
                board_url + "/layout", json=layout, headers=member_headers
            )
        ).status_code == 200
        assert (await client.get(board_url, headers=member_headers)).json()[
            "lane_order"
        ] == layout["order"]
        assert (
            await client.post(
                board_url + "/layout",
                json={**layout, "order": ["doing", "todo", "done"]},
                headers=member_headers,
            )
        ).status_code == 409
        assert (
            await client.post(
                board_url + "/layout",
                json={**layout, "order": ["todo"] * 3},
                headers=member_headers,
            )
        ).status_code == 422
        assert (
            await client.post(
                f"{prefix}/{admin.project_id}/layout",
                json=layout,
                headers=member_headers,
            )
        ).status_code == 404
        notices = (
            await client.get(f"{prefix}/reminders", headers=member_headers)
        ).json()["data"]
        assert [c["id"] for c in notices] == [cid]
        assert notices[0]["description"] == body["description"]
        # Opening a card and dismissing an older occurrence must both leave it present.
        await client.get(board_url, headers=member_headers)
        await client.post(
            member_card + "/read",
            params={"at": listed["unread_at"] - 1},
            headers=member_headers,
        )
        assert (await client.get(f"{prefix}/reminders", headers=member_headers)).json()[
            "data"
        ] == notices
        await client.post(
            member_card + "/read",
            params={"at": listed["unread_at"]},
            headers=member_headers,
        )
        send = app.state.services.chat.start_unified_chat_run
        assert (await client.get(f"{prefix}/reminders", headers=member_headers)).json()[
            "data"
        ] == []

        async def fail_once_then_send(owner, payload):
            if inject.await_count == 1:
                raise ChatServiceError("busy")
            return await send(owner, payload)

        inject = AsyncMock(side_effect=fail_once_then_send)
        app.state.services.chat.start_unified_chat_run = inject
        request = {"request_id": str(uuid4()), "revision": 1}
        assert (
            await client.post(
                member_card + "/send", json=request, headers=member_headers
            )
        ).status_code == 409
        assert (
            await client.get(f"{prefix}/{project.project_id}", headers=member_headers)
        ).json()["data"][0]["status"] == "todo"
        sent = await client.post(
            member_card + "/send", json=request, headers=member_headers
        )
        assert sent.status_code == 200, sent.text
        sid = sent.json()["data"]["session_id"]
        assert [call.args[1]["session_id"] for call in inject.await_args_list] == [
            sid,
            sid,
        ]
        assert (
            inject.await_args.args[1]["message"]
            == "Review layout\n\nVerify both themes\n\n- [ ] Mobile"
        )
        assert (
            await client.post(
                member_card + "/send", json=request, headers=member_headers
            )
        ).json() == sent.json()
        assert inject.await_count == 2
        other_session = await db.create_platform_session(
            creator=user.account_id, platform_id="webchat"
        )
        guided = {
            "request_id": str(uuid4()),
            "session_id": other_session.session_id,
            "revision": 2,
            "guidance": "  Check keyboard focus first.\nReport any issues.  ",
        }
        response = await client.post(
            member_card + "/send", json=guided, headers=member_headers
        )
        assert response.status_code == 200, response.text
        assert inject.await_args.args[1]["message"] == (
            "Check keyboard focus first.\nReport any issues.\n\nReview layout\n\nVerify both themes\n\n- [ ] Mobile"
        )
        assert (
            await client.post(
                member_card + "/send", json=guided, headers=member_headers
            )
        ).json() == response.json()
        assert inject.await_count == 3
        targets = (
            await client.get(prefix + "/targets", headers=member_headers)
        ).json()["data"]
        assert {(t["id"], t["project"]) for t in targets} == {
            (sid, "Member board"),
            (other_session.session_id, None),
        }
        move = await client.post(
            member_card + "/move",
            json={"status": "done", "revision": 3},
            headers=member_headers,
        )
        assert move.status_code == 200, move.text
        card = (
            await client.get(f"{prefix}/{project.project_id}", headers=member_headers)
        ).json()["data"][0]
        assert (card["status"], card["next_due"], card["unread_at"]) == (
            "done",
            None,
            None,
        )
        legacy = {"title": "Renamed", "status": "done", "revision": 4}
        assert (
            await client.put(member_card, json=legacy, headers=member_headers)
        ).status_code == 200
        edited = (await client.get(board_url, headers=member_headers)).json()["data"][0]
        assert (
            edited["labels"] == body["labels"]
            and edited["checklist"] == body["checklist"]
        )
        assert (
            await client.delete(
                member_card, params={"revision": 5}, headers=member_headers
            )
        ).status_code == 200
        assert not (
            await client.get(f"{prefix}/{project.project_id}", headers=member_headers)
        ).json()["data"]
