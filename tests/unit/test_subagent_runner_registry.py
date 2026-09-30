import pytest

from astrbot.core.subagent_manager import SubAgentManager, SubAgentRunHandle

UMO = "webchat:FriendMessage:webchat!u!s"


@pytest.fixture(autouse=True)
def _clean_sessions():
    SubAgentManager._sessions.clear()
    yield
    SubAgentManager._sessions.clear()


def _make_handle(runner=None) -> SubAgentRunHandle:
    return SubAgentRunHandle(
        umo=UMO,
        subagent_run_id="sa_abc",
        agent_name="coder",
        runner=runner if runner is not None else object(),
        sink=None,
    )


def test_register_get_unregister_subagent_runner():
    handle = _make_handle()
    SubAgentManager.register_subagent_runner(UMO, handle)
    assert SubAgentManager.get_subagent_run_handle(UMO, "sa_abc") is handle
    # Identity check: a different runner must not unregister the entry.
    SubAgentManager.unregister_subagent_runner(UMO, "sa_abc", object())
    assert SubAgentManager.get_subagent_run_handle(UMO, "sa_abc") is handle
    SubAgentManager.unregister_subagent_runner(UMO, "sa_abc", handle.runner)
    assert SubAgentManager.get_subagent_run_handle(UMO, "sa_abc") is None


def test_subagent_runner_lookup_isolated_by_session():
    SubAgentManager.register_subagent_runner(UMO, _make_handle())
    other = "webchat:FriendMessage:webchat!other!s"
    assert SubAgentManager.get_subagent_run_handle(other, "sa_abc") is None
