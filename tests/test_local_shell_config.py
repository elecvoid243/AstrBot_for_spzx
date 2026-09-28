"""Tests for the computer_use_local_shell configuration surface."""

import sys
from pathlib import Path

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
