import json
from types import SimpleNamespace

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent_team_execution import (
    NodeExecutionBinding,
    current_team_execution,
)
from astrbot.core.tools.computer_tools.shipyard_neo.browser import BrowserExecTool
from astrbot.core.tools.computer_tools.shipyard_neo.neo_skills import (
    GetExecutionHistoryTool,
)
from astrbot.core.tools.computer_tools.util import check_admin_permission


class _FakeBrowser:
    async def exec(self, **kwargs):
        return {
            "ok": True,
            "cmd": kwargs["cmd"],
        }


class _FakeSandbox:
    async def get_execution_history(self, **kwargs):
        return {
            "items": [],
            "limit": kwargs["limit"],
        }


def _make_run_context(require_admin: bool, role: str = "member") -> ContextWrapper:
    config_holder = SimpleNamespace(
        get_config=lambda umo: {  # noqa: ARG005
            "provider_settings": {
                "computer_use_require_admin": require_admin,
            }
        }
    )
    event = SimpleNamespace(
        role=role,
        unified_msg_origin="qq_official:friend:user-1",
        get_sender_id=lambda: "user-1",
    )
    astr_ctx = SimpleNamespace(context=config_holder, event=event)
    return ContextWrapper(context=astr_ctx)


@pytest.mark.asyncio
async def test_browser_tool_allows_non_admin_when_admin_requirement_disabled(
    monkeypatch,
):
    async def _fake_get_booter(_ctx, _session_id):
        return SimpleNamespace(browser=_FakeBrowser())

    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.shipyard_neo.browser.get_booter",
        _fake_get_booter,
    )

    result = await BrowserExecTool().call(
        _make_run_context(require_admin=False),
        cmd="open https://example.com",
    )

    assert json.loads(result)["ok"] is True


@pytest.mark.asyncio
async def test_neo_skill_tool_allows_non_admin_when_admin_requirement_disabled(
    monkeypatch,
):
    async def _fake_get_booter(_ctx, _session_id):
        return SimpleNamespace(
            bay_client=object(),
            sandbox=_FakeSandbox(),
        )

    monkeypatch.setattr(
        "astrbot.core.tools.computer_tools.shipyard_neo.neo_skills.get_booter",
        _fake_get_booter,
    )

    result = await GetExecutionHistoryTool().call(
        _make_run_context(require_admin=False),
        limit=5,
    )

    payload = json.loads(result)
    assert payload["items"] == []
    assert payload["limit"] == 5


@pytest.mark.asyncio
async def test_browser_tool_still_denies_non_admin_when_admin_requirement_enabled():
    result = await BrowserExecTool().call(
        _make_run_context(require_admin=True),
        cmd="open https://example.com",
    )

    assert "Permission denied" in result
    assert "Using browser tools is only allowed for admin users" in result
    assert "User's ID is: user-1" in result


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("role", ["member", "admin"])
@pytest.mark.parametrize("listed", [False, True])
def test_live_administrator_authority_does_not_promote_the_event(frozen, role, listed):
    """Revalidate frozen admin events while preserving existing role boundaries.

    Args:
        frozen: Whether the invocation has a v2 profile snapshot.
        role: Event role assigned before the tool call.
        listed: Whether the sender is still a current administrator.
    """
    context = _make_run_context(require_admin=True, role=role)
    config = {
        "admins_id": ["user-1"] if listed else [],
        "provider_settings": {"computer_use_require_admin": True},
    }
    context.context.context.get_config = lambda umo: config
    token = current_team_execution.set(
        NodeExecutionBinding(
            run_id="run",
            team_id="team",
            member_id="member",
            node_id="node",
            umo=context.context.event.unified_msg_origin,
            owner_username="user-1",
            config_id="default",
            profile_snapshot={"admins_id": ["user-1"]} if frozen else None,
        )
    )
    try:
        result = check_admin_permission(context, "Using computer tools")
        assert (result is None) == (role == "admin" and (not frozen or listed))
    finally:
        current_team_execution.reset(token)
