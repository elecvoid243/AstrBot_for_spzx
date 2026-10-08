from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.db.po import CronJob
from astrbot.dashboard.services.cron_service import CronService, CronServiceError


@pytest.mark.parametrize(
    (
        "include_timezone",
        "payload_timezone",
        "config_timezone",
        "session",
        "expected_timezone",
        "should_read_config",
    ),
    [
        (
            True,
            "America/New_York",
            "Asia/Shanghai",
            "test:private:session",
            "America/New_York",
            False,
        ),
        (
            True,
            "",
            "Asia/Shanghai",
            "test:private:session",
            "Asia/Shanghai",
            True,
        ),
        (
            False,
            None,
            "Asia/Shanghai",
            "test:private:session",
            "Asia/Shanghai",
            True,
        ),
        (False, None, "UTC", "", "UTC", True),
        (False, None, "", "", None, True),
    ],
)
@pytest.mark.asyncio
async def test_create_job_resolves_default_timezone(
    include_timezone: bool,
    payload_timezone: str | None,
    config_timezone: str,
    session: str,
    expected_timezone: str | None,
    should_read_config: bool,
) -> None:
    """Verify that new cron jobs inherit the configured timezone by default.

    Args:
        include_timezone: Whether the request includes the timezone field.
        payload_timezone: Timezone value supplied by the request.
        config_timezone: Timezone returned by the applicable AstrBot config.
        session: Target session supplied by the request.
        expected_timezone: Timezone expected by the cron manager.
        should_read_config: Whether configuration lookup should occur.
    """
    job = SimpleNamespace(
        job_id="job-1",
        name="test-job",
        payload={"note": "test"},
        run_once=False,
    )
    cron_manager = SimpleNamespace(
        add_active_job=AsyncMock(return_value=job),
    )
    config_manager = SimpleNamespace(
        get_conf=MagicMock(return_value={"timezone": config_timezone}),
    )
    service = CronService(
        SimpleNamespace(
            cron_manager=cron_manager,
            astrbot_config_mgr=config_manager,
        )
    )
    payload = {
        "name": "test-job",
        "note": "test",
        "cron_expression": "0 9 * * *",
        "session": session,
    }
    if include_timezone:
        payload["timezone"] = payload_timezone

    await service.create_job(payload)

    call_kwargs = cron_manager.add_active_job.await_args.kwargs
    assert call_kwargs["timezone"] == expected_timezone
    if should_read_config:
        config_manager.get_conf.assert_called_once_with(session or None)
    else:
        config_manager.get_conf.assert_not_called()


def _service_with_job(job=None, **manager_overrides):
    cron_manager = SimpleNamespace(
        add_active_job=AsyncMock(return_value=job),
        **manager_overrides,
    )
    config_manager = SimpleNamespace(get_conf=MagicMock(return_value={}))
    return CronService(
        SimpleNamespace(
            cron_manager=cron_manager,
            astrbot_config_mgr=config_manager,
        )
    ), cron_manager


@pytest.mark.asyncio
async def test_create_job_defaults_delivery_mode_to_proactive() -> None:
    """A job created without an explicit mode keeps the proactive delivery."""
    job = SimpleNamespace(job_id="job-1", name="test-job", payload={}, run_once=False)
    service, cron_manager = _service_with_job(job)

    await service.create_job(
        {
            "name": "test-job",
            "note": "test",
            "cron_expression": "0 9 * * *",
            "session": "aiocqhttp:FriendMessage:123456",
        }
    )

    payload = cron_manager.add_active_job.await_args.kwargs["payload"]
    assert payload["delivery_mode"] == "proactive"


@pytest.mark.asyncio
async def test_create_job_rejects_user_turn_for_non_webchat_session() -> None:
    """The user-turn mode is only meaningful for webchat delivery targets."""
    service, cron_manager = _service_with_job()

    with pytest.raises(CronServiceError, match="webchat"):
        await service.create_job(
            {
                "name": "test-job",
                "note": "test",
                "cron_expression": "0 9 * * *",
                "session": "aiocqhttp:FriendMessage:123456",
                "delivery_mode": "webchat_user_turn",
            }
        )

    cron_manager.add_active_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_job_persists_user_turn_mode() -> None:
    """A webchat target keeps the requested user-turn mode in the payload."""
    job = SimpleNamespace(job_id="job-1", name="test-job", payload={}, run_once=False)
    service, cron_manager = _service_with_job(job)

    await service.create_job(
        {
            "name": "test-job",
            "note": "test",
            "cron_expression": "0 9 * * *",
            "session": "webchat:FriendMessage:webchat!alice!conv-1",
            "delivery_mode": "webchat_user_turn",
        }
    )

    payload = cron_manager.add_active_job.await_args.kwargs["payload"]
    assert payload["delivery_mode"] == "webchat_user_turn"


@pytest.mark.asyncio
async def test_update_job_merges_user_turn_mode() -> None:
    """Switching an existing job to the user-turn mode updates its payload."""
    job = CronJob(
        job_id="job-1",
        name="test-job",
        job_type="active_agent",
        cron_expression="0 9 * * *",
        payload={
            "note": "test",
            "session": "webchat:FriendMessage:webchat!alice!conv-1",
        },
    )
    service, cron_manager = _service_with_job(
        job,
        db=SimpleNamespace(get_cron_job=AsyncMock(return_value=job)),
        update_job=AsyncMock(return_value=job),
    )

    await service.update_job("job-1", {"delivery_mode": "webchat_user_turn"})

    payload = cron_manager.update_job.await_args.kwargs["payload"]
    assert payload["delivery_mode"] == "webchat_user_turn"


def test_serialize_job_exposes_delivery_mode() -> None:
    """The dashboard reads the mode from the serialized job."""
    job = CronJob(
        job_id="job-1",
        name="test-job",
        job_type="active_agent",
        payload={"delivery_mode": "webchat_user_turn"},
    )

    assert CronService.serialize_job(job)["delivery_mode"] == "webchat_user_turn"

    job.payload = {}
    assert CronService.serialize_job(job)["delivery_mode"] == "proactive"
