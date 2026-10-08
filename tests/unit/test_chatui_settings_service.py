"""Persistence round-trip for the ChatUI thinking-effort settings.

These tests exercise the ChatService helpers against a real
:class:`AstrBotConfig` on a temporary path, so they cover the part the pure
normalization tests cannot: that editing through the service actually lands
in ``cmd_config.json``.

Author: elecvoid243, 2026-10-08
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.dashboard.services.chat_service import ChatService, ChatServiceError

BASE_CONFIG = {"chatui": copy.deepcopy(DEFAULT_CONFIG["chatui"])}


class _ConfigManager:
    """Minimal AstrBotConfigManager stand-in: only `confs` is read."""

    def __init__(self, conf: AstrBotConfig) -> None:
        self.confs = {"default": conf}


def _service(tmp_path: Path) -> tuple[ChatService, Path]:
    config_path = tmp_path / "cmd_config.json"
    conf = AstrBotConfig(
        config_path=str(config_path),
        default_config=BASE_CONFIG,
        schema=None,
    )
    service = ChatService.__new__(ChatService)
    service.core_lifecycle = SimpleNamespace(
        astrbot_config_mgr=_ConfigManager(conf),
    )
    return service, config_path


def _stored_effort(config_path: Path) -> dict:
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    return data["chatui"]["thinking_effort"]


def test_default_config_seeds_the_builtin_presets(tmp_path):
    service, config_path = _service(tmp_path)

    settings = service.get_chatui_thinking_effort()

    assert [preset["id"] for preset in settings["presets"]] == [
        "deepseek-v4",
        "deepseek-v4.1",
        "qwen-3.8",
    ]
    assert _stored_effort(config_path)["active_preset"] == "deepseek-v4"


def test_value_round_trip_writes_the_config_file(tmp_path):
    service, config_path = _service(tmp_path)

    settings = service.set_chatui_thinking_effort_value("  high  ")

    assert settings["value"] == "high"
    assert _stored_effort(config_path)["value"] == "high"


def test_value_rejects_blank_without_touching_the_file(tmp_path):
    service, config_path = _service(tmp_path)
    before = _stored_effort(config_path)

    with pytest.raises(ChatServiceError):
        service.set_chatui_thinking_effort_value("   ")

    assert _stored_effort(config_path) == before


def test_replace_presets_normalizes_a_ghost_active_id(tmp_path):
    service, config_path = _service(tmp_path)
    presets = [
        {
            "id": "custom",
            "name": "Custom",
            "mode": "levels",
            "levels": [{"name": "低", "value": "low"}],
            "slider": {"min": 1, "max": 100, "step": 1, "snaps": []},
        },
    ]

    settings = service.replace_chatui_thinking_effort(presets, "ghost", "low")

    assert settings["active_preset"] == "custom"
    assert settings["value"] == "low"
    stored = _stored_effort(config_path)
    assert [preset["id"] for preset in stored["presets"]] == ["custom"]
    assert stored["active_preset"] == "custom"


def test_empty_preset_list_is_persisted(tmp_path):
    service, config_path = _service(tmp_path)

    settings = service.replace_chatui_thinking_effort([], "", "max")

    assert settings == {"active_preset": "", "value": "max", "presets": []}
    assert _stored_effort(config_path)["presets"] == []


def test_omitted_value_keeps_the_stored_selection(tmp_path):
    service, _ = _service(tmp_path)
    service.set_chatui_thinking_effort_value("37")

    settings = service.replace_chatui_thinking_effort([], "", None)

    assert settings["value"] == "37"


def test_invalid_presets_raise_without_touching_the_file(tmp_path):
    service, config_path = _service(tmp_path)
    before = _stored_effort(config_path)

    with pytest.raises(ChatServiceError):
        service.replace_chatui_thinking_effort(
            [
                {
                    "id": "p",
                    "name": "P",
                    "mode": "slider",
                    "slider": {"min": 5, "max": 1, "step": 1},
                }
            ],
            "p",
            None,
        )

    assert _stored_effort(config_path) == before


def test_quick_messages_round_trip(tmp_path):
    service, config_path = _service(tmp_path)
    items = [{"id": "qm-1", "content": "继续"}]

    settings = service.replace_chatui_quick_messages(items)

    assert settings == {"items": items}
    stored = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert stored["chatui"]["quick_messages"]["items"] == items


def test_sibling_write_keeps_thinking_effort_intact(tmp_path):
    """The whole `chatui` section is handed to save_config at once."""
    service, config_path = _service(tmp_path)

    service.replace_chatui_quick_messages([{"id": "qm-1", "content": "继续"}])
    service.set_chatui_thinking_effort_value("42")

    stored = json.loads(config_path.read_text(encoding="utf-8-sig"))["chatui"]
    assert stored["thinking_effort"]["value"] == "42"
    assert [preset["id"] for preset in stored["thinking_effort"]["presets"]] == [
        "deepseek-v4",
        "deepseek-v4.1",
        "qwen-3.8",
    ]
    assert stored["quick_messages"]["items"][0]["content"] == "继续"


def test_quick_messages_reject_invalid_payload_without_touching_siblings(tmp_path):
    service, config_path = _service(tmp_path)

    with pytest.raises(ChatServiceError):
        service.replace_chatui_quick_messages([{"id": "qm-1", "content": "  "}])

    stored = json.loads(config_path.read_text(encoding="utf-8-sig"))["chatui"]
    assert stored["quick_messages"]["items"] == []
    assert stored["thinking_effort"]["active_preset"] == "deepseek-v4"
