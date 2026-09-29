import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from astrbot.core.computer.booters import local as local_booter
from astrbot.core.computer.booters.local import LocalShellComponent, resolve_local_shell
from astrbot.dashboard.services import config_service
from astrbot.dashboard.services.config_service import ConfigProfileService


def test_get_system_config_includes_effective_server_time() -> None:
    """Verify that the response includes server UTC time and configured offset."""
    fixed_time = datetime(2026, 8, 7, 2, 31, tzinfo=timezone.utc)
    service = ConfigProfileService(
        SimpleNamespace(
            astrbot_config_mgr=SimpleNamespace(
                confs={"default": {"timezone": "Asia/Shanghai"}}
            )
        ),
        runtime={"os": "windows", "sandbox": {"status": "unsupported"}},
    )

    with patch("astrbot.dashboard.services.config_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_time
        result = service.get_system_config()

    assert result["server_utc_time"] == "2026-08-07T02:31:00+00:00"
    assert result["server_utc_offset_minutes"] == 480
    assert result["config"]["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_profile_mutations_await_config_manager() -> None:
    """Verify profile mutations use the config manager's async methods."""
    config_manager = SimpleNamespace(
        create_conf=AsyncMock(return_value="profile-id"),
        update_conf_info=AsyncMock(return_value=True),
        delete_conf=AsyncMock(return_value=True),
    )
    lifecycle = SimpleNamespace(
        astrbot_config_mgr=config_manager,
        reload_pipeline_scheduler=AsyncMock(),
        pipeline_scheduler_mapping={"profile-id": object()},
    )
    service = ConfigProfileService(
        lifecycle, runtime={"os": "windows", "sandbox": {"status": "unsupported"}}
    )

    with pytest.raises(ValueError, match="Local permission member:") as exc:
        await service.create_profile(
            "Invalid",
            {
                "provider_settings": {
                    "computer_use_runtime": "local",
                    "computer_use_local_permissions": {
                        role: {"filesystem_scope": "workspace"}
                        for role in ("member", "admin")
                    },
                }
            },
        )
    assert "Local permission admin:" in str(exc.value)
    config_manager.create_conf.assert_not_awaited()
    lifecycle.reload_pipeline_scheduler.assert_not_awaited()

    result = await service.create_profile(
        "Profile", {"provider_settings": {"computer_use_runtime": "local"}}
    )
    await service.rename_profile("profile-id", "Renamed")
    await service.delete_profile("profile-id")

    assert result == {"conf_id": "profile-id"}
    config_manager.create_conf.assert_awaited_once_with(
        name="Profile",
        config={"provider_settings": {"computer_use_runtime": "local"}},
    )
    lifecycle.reload_pipeline_scheduler.assert_awaited_once_with("profile-id")
    config_manager.update_conf_info.assert_awaited_once_with(
        "profile-id",
        name="Renamed",
    )
    config_manager.delete_conf.assert_awaited_once_with("profile-id")
    assert "profile-id" not in lifecycle.pipeline_scheduler_mapping


def _seed_shell_session(
    shell: LocalShellComponent, out_dir, session_id: str, family: str
) -> None:
    """Register one managed shell session of a given family on the component."""
    output_path = out_dir / f"{session_id}.log"
    output_path.touch()
    shell._sessions[session_id] = local_booter._LocalShellSession(
        session_id=session_id,
        owner_id="umo",
        creator_id="user",
        creator_is_admin=True,
        sandboxed=False,
        process=object(),
        output_path=output_path,
        started_at=0.0,
        output_event=asyncio.Event(),
        reader_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
        shell_family=family,
    )


@pytest.mark.asyncio
async def test_update_profile_invalidates_sessions_of_the_previous_shell(
    monkeypatch, tmp_path
) -> None:
    """Saving the system config must drop stale sessions instead of raising.

    Regression test for the dashboard save path: it used to call the shell
    cleanup with a keyword the component no longer accepted, so once a local
    shell session had existed in the process every save failed with a
    TypeError after the configuration had already been written.
    """
    shell = LocalShellComponent()
    terminated = []

    async def fake_terminate(session):
        terminated.append(session.session_id)

    monkeypatch.setattr(shell, "_terminate_process", fake_terminate)

    out_dir = tmp_path / "shell"
    out_dir.mkdir()
    current_family = resolve_local_shell("powershell").family
    _seed_shell_session(shell, out_dir, "sh_current", current_family)
    _seed_shell_session(shell, out_dir, "sh_stale", "stale-family")

    monkeypatch.setattr(
        config_service.computer_client,
        "local_booter",
        SimpleNamespace(shell=shell),
    )
    monkeypatch.setattr(config_service, "save_config", lambda *_a, **_k: None)

    lifecycle = SimpleNamespace(
        astrbot_config_mgr=SimpleNamespace(confs={"default": {}}),
        reload_pipeline_scheduler=AsyncMock(),
    )
    service = ConfigProfileService(
        lifecycle, runtime={"os": "windows", "sandbox": {"status": "unsupported"}}
    )

    await service.update_profile(
        "default", {"provider_settings": {"computer_use_local_shell": "powershell"}}
    )

    assert terminated == ["sh_stale"]
    assert set(shell._sessions) == {"sh_current"}
    lifecycle.reload_pipeline_scheduler.assert_awaited_once_with("default")
