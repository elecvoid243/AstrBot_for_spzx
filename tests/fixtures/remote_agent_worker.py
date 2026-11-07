"""Isolated TCP dashboard worker for real multi-instance admission regressions."""

import argparse
import asyncio
import os
from pathlib import Path


async def main():
    """Host production APIs with test-only controls for deterministic Agent turns."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    os.environ["ASTRBOT_ROOT"] = str(args.root)
    from types import SimpleNamespace

    import httpx
    import uvicorn
    from fastapi import FastAPI, Request

    from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
    from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
        webchat_queue_mgr,
    )
    from astrbot.core.utils.webchat_access import DashboardPrincipal
    from tests.fixtures.dashboard_accounts import PASSWORD, account_dashboard
    from tests.test_fastapi_v1_dashboard import fake_core_lifecycle
    from tests.test_multi_user_chat import login

    fixture = account_dashboard.__wrapped__(
        fake_core_lifecycle.__wrapped__(), args.root
    )
    server, core, db = await anext(fixture)
    app = FastAPI()
    service = server.asgi_app.state.services.chat
    from astrbot.dashboard.services import remote_chat_service

    blocked, outbound = set(), []

    async def network_guard(request):
        """Model NAT by refusing worker-initiated traffic to blocked listeners.

        Args:
            request: Real outbound HTTP request, with secrets excluded from evidence.

        Raises:
            httpx.ConnectError: The test firewall blocks this destination port.
        """
        outbound.append(request.url.port)
        if request.url.port in blocked:
            raise httpx.ConnectError(
                "Test firewall: outbound destination is unreachable"
            )

    real_client = httpx.AsyncClient
    remote_chat_service.httpx = SimpleNamespace(**vars(httpx))
    remote_chat_service.httpx.AsyncClient = lambda **kwargs: real_client(
        event_hooks={"request": [network_guard]}, **kwargs
    )
    core.astrbot_config["chatui"]["remote_sessions_enabled"] = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.asgi_app), base_url="http://test"
    ) as client:
        account = await login(client, "original-admin", PASSWORD)
        headers = {"Authorization": "Bearer " + account["token"]}
        credential = (
            await client.post(
                "/api/v1/chat/remote-access/credentials",
                headers=headers,
                json={"name": "Instance " + str(args.port)},
            )
        ).json()["data"]
        await client.put(
            "/api/v1/chat/remote-access/exposure", headers=headers, json={"mode": "all"}
        )
    principal = DashboardPrincipal(
        account["account_id"], account["resource_owner"], "admin", account["username"]
    )
    adapter = WebChatAdapter({}, {}, asyncio.Queue())
    events, listeners = {}, []

    @app.post("/test/session")
    async def session(request: Request):
        payload = await request.json()
        sid = (
            await db.create_platform_session(
                creator=principal.resource_owner,
                display_name=payload.get("title", "Test session"),
            )
        ).session_id
        events[sid] = adapter.create_event(
            await adapter.convert_message(
                (
                    principal.resource_owner,
                    sid,
                    {
                        "message": [{"type": "plain", "text": "Source turn"}],
                        "message_id": sid,
                        "_dashboard_account": principal,
                    },
                )
            )
        )

        async def listen():
            """Consume ordinary input and end deterministic recipient execution."""
            while True:
                item = await webchat_queue_mgr.get_or_create_queue(sid).get()
                events[sid] = adapter.create_event(await adapter.convert_message(item))
                await webchat_queue_mgr.get_or_create_back_queue(
                    item[2]["message_id"], sid
                ).put({"type": "end", "data": "", "message_id": item[2]["message_id"]})

        listeners.append(asyncio.create_task(listen()))
        return {"session_id": sid}

    @app.post("/test/send")
    async def send(request: Request):
        payload = await request.json()
        source = events[payload.pop("source")]
        try:
            if "connection_id" in payload:
                return await service.remote_chat.send_agent_message(source, **payload)
            return await service.send_session_message(source, **payload)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    @app.post("/test/policy")
    async def policy(request: Request):
        payload = await request.json()
        core.astrbot_config["session_messages"].update(payload)
        return {"ok": True}

    @app.get("/test/state")
    async def state():
        return {
            "outbound_ports": outbound,
            "sessions": {
                sid: {
                    "count": getattr(
                        event.get_extra("_session_message_chain"), "count", None
                    ),
                    "source": event.get_extra("_agent_message_source").public_data()
                    if event.get_extra("_agent_message_source")
                    else None,
                }
                for sid, event in events.items()
            },
        }

    @app.post("/test/network")
    async def network(request: Request):
        payload = await request.json()
        blocked.update(payload.get("blocked_ports", []))
        blocked.difference_update(payload.get("allow_ports", []))
        return {"ok": True}

    @app.get("/test/ready")
    async def ready():
        return {"token": account["token"], "key": credential["key"]}

    app.mount("/", server.asgi_app)
    try:
        await uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
        ).serve()
    finally:
        for task in [*listeners, *service.remote_chat.control_tasks]:
            task.cancel()
        await asyncio.gather(
            *listeners, *service.remote_chat.control_tasks, return_exceptions=True
        )
        await fixture.aclose()


if __name__ == "__main__":
    asyncio.run(main())
