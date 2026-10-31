"""Optional skill store resolution and availability checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from astrbot.dashboard.services import skills_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,content_type,frame,available",
    [
        (200, "text/html", "", True),
        (404, "text/html", "", False),
        (200, "application/json", "", False),
        (200, "text/html", "DENY", False),
    ],
)
async def test_store_uses_release_source_parent(
    monkeypatch, status, content_type, frame, available
):
    monkeypatch.setattr(
        skills_service,
        "UpdateConfig",
        lambda: SimpleNamespace(
            get_core_release_api_url=lambda: (
                "http://127.0.0.1:11000/custom/releases?token=ignored"
            ),
        ),
    )
    response = SimpleNamespace(
        status=status, content_type=content_type, headers={"X-Frame-Options": frame}
    )
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    client = MagicMock()
    client.get.return_value = request
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=client)
    monkeypatch.setattr(
        skills_service.aiohttp, "ClientSession", lambda **kwargs: session
    )
    result = await skills_service.SkillsService.__new__(
        skills_service.SkillsService
    ).get_store()
    assert result == {
        "available": available,
        "url": "http://127.0.0.1:11000/custom/skills" if available else "",
    }
    client.get.assert_called_once_with(
        "http://127.0.0.1:11000/custom/skills", allow_redirects=True
    )


@pytest.mark.asyncio
async def test_unreachable_store_is_hidden(monkeypatch):
    session = MagicMock()
    session.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError())
    monkeypatch.setattr(
        skills_service.aiohttp, "ClientSession", lambda **kwargs: session
    )
    result = await skills_service.SkillsService.__new__(
        skills_service.SkillsService
    ).get_store()
    assert result == {"available": False, "url": ""}
