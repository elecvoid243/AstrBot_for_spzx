"""Tests for the computer_use_local_shell configuration surface."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot.core.config.default import DEFAULT_CONFIG  # noqa: E402


def test_local_shell_default_is_auto():
    provider_settings = DEFAULT_CONFIG["provider_settings"]

    assert provider_settings["computer_use_local_shell"] == "auto"


def test_local_shell_schema_lists_every_family():
    from astrbot.core.config.default import CONFIG_METADATA_3

    schema = CONFIG_METADATA_3["ai_group"]["metadata"]["agent_computer_use"]["items"]
    entry = schema["provider_settings.computer_use_local_shell"]

    assert entry["options"] == ["auto", "git_bash", "pwsh", "powershell", "cmd"]
    assert len(entry["labels"]) == len(entry["options"])
    assert entry["condition"] == {
        "provider_settings.computer_use_runtime": "local",
    }


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ru-RU", "ja-JP"])
def test_local_shell_has_translations_for_every_locale(locale):
    """The dashboard renders the i18n text, not the schema's inline fallback."""
    from astrbot.core.config.default import CONFIG_METADATA_3

    options = CONFIG_METADATA_3["ai_group"]["metadata"]["agent_computer_use"]["items"][
        "provider_settings.computer_use_local_shell"
    ]["options"]
    path = (
        Path(__file__).resolve().parents[1]
        / "dashboard"
        / "src"
        / "i18n"
        / "locales"
        / locale
        / "features"
        / "config-metadata.json"
    )
    metadata = json.loads(path.read_text(encoding="utf-8"))
    entry = metadata["ai_group"]["agent_computer_use"]["provider_settings"][
        "computer_use_local_shell"
    ]

    assert entry["description"]
    assert entry["hint"]
    assert len(entry["labels"]) == len(options)
