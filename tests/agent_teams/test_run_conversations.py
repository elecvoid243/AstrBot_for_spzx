"""Check Dashboard Chat run conversations through the public API."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from astrbot.core.db.po import AgentWorkflowRun, PlatformSession
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.dashboard.api import chat, open_api
from astrbot.dashboard.api.auth import AuthContext
from astrbot.dashboard.services.chat_service import ChatService
from astrbot.dashboard.services.open_api_service import OpenApiService


@pytest.mark.asyncio
async def test_run_conversations_share_paging_and_enforce_dashboard_ownership(tmp_path):
    """Open one session per root and archive it without changing the run."""
    db = SQLiteDatabase(str(tmp_path / "run-conversations.db"))
    first, second, child, foreign, retry = (letter * 32 for letter in "abcde")
    legacy = "workflow-run-project-a"
    collision = f"workflow-run-{first}"
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with db.get_db() as session, session.begin():
        session.add(
            PlatformSession(
                session_id=legacy,
                creator="alice",
                display_name="Ordinary chat",
                updated_at=when + timedelta(days=1),
            )
        )
        session.add(
            PlatformSession(
                session_id=collision,
                creator="alice",
                display_name="Legacy collision",
                updated_at=when - timedelta(days=1),
            )
        )
        for index, (run_id, root_id, owner) in enumerate(
            [
                (first, first, "alice"),
                (second, second, "alice"),
                (child, first, "alice"),
                (foreign, foreign, "bob"),
                (retry, first, "alice"),
            ]
        ):
            session.add(
                AgentWorkflowRun(
                    run_id=run_id,
                    owner_username=owner,
                    snapshot={
                        "root_run_id": root_id,
                        **(
                            {"parent_run_id": first} if run_id in (child, retry) else {}
                        ),
                        "asset_id": "research",
                        "asset_version": 2,
                        "definition": {"name": "Research"},
                    },
                    updated_at=when + timedelta(days=index),
                )
            )
    app = FastAPI()
    lifecycle = SimpleNamespace(
        conversation_manager=None,
        platform_message_history_manager=PlatformMessageHistoryManager(db),
        umop_config_router=None,
        platform_manager=None,
        astrbot_config_mgr=SimpleNamespace(confs={}, get_conf_list=lambda: []),
    )
    open_api_service = OpenApiService(db, lifecycle)
    app.state.services = SimpleNamespace(
        open_api=open_api_service,
        chat=ChatService(db, lifecycle),
    )
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(open_api.router, prefix="/api/v1")

    def auth():
        return identity

    app.dependency_overrides[chat.require_chat_scope] = auth
    identity = AuthContext("alice", ["chat"], role="admin")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/api/v1/chat/sessions"
        listing = {"cursor": "start", "page_size": 2, "include_run_conversations": True}
        page = (await client.get(path, params=listing)).json()["data"]
        assert {entry["session_id"] for entry in page["items"]} == {
            legacy,
            f"workflow-run-{second}",
        }
        link = f"/api/v1/chat/run-conversations/{first}"
        for _ in range(2):
            detail = (await client.get(link)).json()["data"]["session"]
            assert detail["session_type"] == "workflow_run"
            assert detail["run_conversation"] == {
                "run_id": first,
                "asset_id": "research",
                "asset_version": 2,
                "status": "running",
            }
        listing["cursor"] = page["next_cursor"]
        remainder = (await client.get(path, params=listing)).json()["data"]
        assert [
            (entry["session_id"], entry.get("session_type"))
            for entry in remainder["items"]
        ] == [
            (collision, "workflow_run"),
            (collision, None),
        ]
        assert remainder["next_cursor"] is None
        for ordinary_id in (legacy, collision):
            ordinary = (await client.get(f"{path}/{ordinary_id}")).json()["data"][
                "session"
            ]
            assert (
                ordinary["session_id"] == ordinary_id
                and "run_conversation" not in ordinary
            )
            assert await open_api_service.prepare_chat_send(
                {
                    "username": "alice",
                    "session_id": ordinary_id,
                    "message": "Still works",
                },
                [],
            ) == ("alice", ordinary_id, None)
        for scoped_run in (child, retry):
            assert (
                await client.get(f"/api/v1/chat/run-conversations/{scoped_run}")
            ).status_code == 404

        identity = AuthContext("bob", ["chat"], role="admin")
        assert (await client.get(link)).status_code == 404
        assert (await client.get(path, params=listing)).json()["data"]["expired"]
        identity = AuthContext("alice", ["chat"], role="user")
        assert (await client.get(link)).status_code == 404
        listing["cursor"] = "start"
        result = (await client.get(path, params=listing)).json()["data"]
        assert [entry["session_id"] for entry in result["items"]] == [legacy, collision]
        session_id = f"workflow-run-{second}"
        send = {
            "session_id": session_id,
            "message": "Ordinary text",
            "username": "alice",
        }
        identity = AuthContext("alice", ["chat"], role="admin")
        res = await client.post(
            "/api/v1/chat/send",
            json={"session_id": session_id, "content": "Ordinary text"},
        )
        assert res.status_code == 400 and "Run conversations" in res.json()["message"]
        res = await client.post("/api/v1/chat", json=send)
        assert res.json()["status"] == "error"
        assert "Run conversations" in res.json()["message"]
        for method, url, payload in (
            ("PUT", "/config", {"config_id": "default"}),
            ("POST", "/stop", None),
            ("POST", "/messages/1/regenerate", {}),
            ("DELETE", "", None),
        ):
            res = await client.request(
                method, f"{path}/{session_id}" + url, json=payload
            )
            assert res.json()["status"] == "error"
        res = await client.post(
            path + "/batch-delete", json={"session_ids": [session_id]}
        )
        assert res.json()["data"]["deleted_count"] == 0

        identity = AuthContext("alice", ["chat"], via="api_key", role="admin")
        res = await client.post("/api/v1/chat", json=send)
        assert res.json()["status"] == "error"
        assert "Run conversations" in res.json()["message"]
        key_list = (
            await client.get(path, params={**listing, "username": "alice"})
        ).json()["data"]
        assert [row["session_id"] for row in key_list["sessions"]] == [
            legacy,
            collision,
        ]
        assert (await client.get(link)).status_code == 404
        identity = AuthContext("alice", ["chat"], role="admin")

        assert (await client.patch(link, json={"archived": True})).json()[
            "status"
        ] == "ok"
        active = (await client.get(path, params={**listing, "page_size": 10})).json()[
            "data"
        ]
        assert [
            (row["session_id"], row.get("session_type")) for row in active["items"]
        ] == [
            (legacy, None),
            (session_id, "workflow_run"),
            (collision, None),
        ]
        archived = (await client.get(path + "/archived", params=listing)).json()["data"]
        assert [entry["session_id"] for entry in archived["items"]] == [
            f"workflow-run-{first}"
        ]
        assert (await client.get(link)).json()["data"]["session"]["run_conversation"][
            "status"
        ] == "running"
        restored = await client.post(
            path + "/batch-unarchive", json={"session_ids": [], "run_ids": [first]}
        )
        assert restored.json()["data"]["unarchived_count"] == 1
    await db.engine.dispose()
