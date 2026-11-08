from types import SimpleNamespace

import pytest

from astrbot.dashboard.api.chat import active_chat_runs


@pytest.mark.asyncio
async def test_active_runs_reports_the_callers_own_work_only() -> None:
    """The update prompt must not wait on another account's chat runs."""
    service = SimpleNamespace(
        chat_runs={
            "alice-run": SimpleNamespace(username="alice"),
            "bob-run": SimpleNamespace(username="bob"),
        }
    )

    alice = await active_chat_runs(
        auth=SimpleNamespace(username="alice"), service=service
    )
    bob = await active_chat_runs(auth=SimpleNamespace(username="bob"), service=service)

    assert alice["data"]["running"] is True
    assert bob["data"]["running"] is True


@pytest.mark.asyncio
async def test_active_runs_is_false_once_no_run_remains() -> None:
    """Finished runs are popped from chat_runs, so an empty map means idle."""
    result = await active_chat_runs(
        auth=SimpleNamespace(username="alice"),
        service=SimpleNamespace(chat_runs={}),
    )

    assert result["data"]["running"] is False
