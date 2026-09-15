"""Tests for the configurable MCP stdio launcher allow/deny policy.

Author: elecvoid243 | 2026-09-16
"""

import pytest

from astrbot.core.agent import mcp_client
from astrbot.core.agent.mcp_client import (
    _DEFAULT_STDIO_COMMAND_ALLOWLIST,
    _DENIED_STDIO_COMMANDS,
    _get_stdio_command_allowlist,
    _get_stdio_command_denylist,
    validate_mcp_stdio_config,
)

ALLOWLIST_ENV = "ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep the allowlist env var out of every test unless set explicitly."""
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)


def _use_settings(monkeypatch, settings: object) -> None:
    """Point the policy resolver at a fake ``mcp_settings`` mapping."""
    monkeypatch.setattr(
        mcp_client,
        "_get_stdio_policy_overrides",
        lambda: settings if isinstance(settings, dict) else {},
    )


class TestBuiltinDefaults:
    def test_allowlist_falls_back_to_builtin_when_unset(self, monkeypatch):
        _use_settings(monkeypatch, {})

        assert _get_stdio_command_allowlist() == set(_DEFAULT_STDIO_COMMAND_ALLOWLIST)

    def test_denylist_falls_back_to_builtin_when_unset(self, monkeypatch):
        _use_settings(monkeypatch, {})

        assert _get_stdio_command_denylist() == set(_DENIED_STDIO_COMMANDS)

    def test_invalid_settings_fall_back_to_builtin(self, monkeypatch):
        _use_settings(monkeypatch, "not-a-mapping")

        assert _get_stdio_command_allowlist() == set(_DEFAULT_STDIO_COMMAND_ALLOWLIST)
        assert _get_stdio_command_denylist() == set(_DENIED_STDIO_COMMANDS)

    def test_empty_configured_lists_fall_back_to_builtin(self, monkeypatch):
        _use_settings(monkeypatch, {"stdio_allowlist": [], "stdio_denylist": []})

        assert _get_stdio_command_allowlist() == set(_DEFAULT_STDIO_COMMAND_ALLOWLIST)
        assert _get_stdio_command_denylist() == set(_DENIED_STDIO_COMMANDS)


class TestConfigOverrides:
    def test_configured_allowlist_replaces_builtin(self, monkeypatch):
        _use_settings(monkeypatch, {"stdio_allowlist": ["my-launcher"]})

        assert _get_stdio_command_allowlist() == {"my-launcher"}
        # A built-in entry is gone once the list is replaced.
        with pytest.raises(ValueError, match="is not allowed"):
            validate_mcp_stdio_config({"command": "uv", "args": []})

    def test_configured_allowlist_accepts_extra_launcher(self, monkeypatch):
        _use_settings(monkeypatch, {"stdio_allowlist": ["uv", "my-launcher"]})

        validate_mcp_stdio_config({"command": "my-launcher", "args": ["--stdio"]})

    def test_comma_separated_string_is_accepted(self, monkeypatch):
        _use_settings(monkeypatch, {"stdio_allowlist": "uv, my-launcher,"})

        assert _get_stdio_command_allowlist() == {"uv", "my-launcher"}

    def test_configured_names_are_normalized(self, monkeypatch):
        _use_settings(monkeypatch, {"stdio_allowlist": [r"C:\Tools\My-Launcher.EXE"]})

        assert _get_stdio_command_allowlist() == {"my-launcher"}
        # The launched command is normalized the same way (path, case, extension).
        validate_mcp_stdio_config({"command": "/opt/bin/MY-LAUNCHER", "args": []})

    def test_configured_denylist_replaces_builtin(self, monkeypatch):
        _use_settings(monkeypatch, {"stdio_denylist": ["my-launcher"]})

        denied = _get_stdio_command_denylist()
        assert denied == {"my-launcher"}
        # The built-in entries are gone once the list is replaced.
        assert "bash" not in denied and "powershell" not in denied
        # The allowlist is untouched by a denylist override.
        assert _get_stdio_command_allowlist() == set(_DEFAULT_STDIO_COMMAND_ALLOWLIST)


class TestDenylistPrecedence:
    def test_builtin_denylist_blocks_even_when_allowlisted(self, monkeypatch):
        _use_settings(
            monkeypatch,
            {"stdio_allowlist": ["bash"], "stdio_denylist": ["bash"]},
        )

        with pytest.raises(ValueError, match="`bash` is not allowed"):
            validate_mcp_stdio_config({"command": "bash", "args": []})

    def test_builtin_denylist_wins_over_allowlist_env(self, monkeypatch):
        monkeypatch.setenv(ALLOWLIST_ENV, "powershell")
        _use_settings(monkeypatch, {})

        with pytest.raises(ValueError, match="`powershell` is not allowed"):
            validate_mcp_stdio_config({"command": "powershell", "args": []})


class TestEnvPrecedence:
    def test_env_overrides_config_allowlist(self, monkeypatch):
        monkeypatch.setenv(ALLOWLIST_ENV, "env-launcher")
        _use_settings(monkeypatch, {"stdio_allowlist": ["config-launcher"]})

        assert _get_stdio_command_allowlist() == {"env-launcher"}

    def test_blank_env_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv(ALLOWLIST_ENV, "   ")
        _use_settings(monkeypatch, {"stdio_allowlist": ["config-launcher"]})

        assert _get_stdio_command_allowlist() == {"config-launcher"}


class TestConfigSourceIntegration:
    def test_reads_mcp_settings_from_running_config(self, monkeypatch):
        """The resolver reads the live ``astrbot_config`` mapping."""
        import astrbot.core as astrbot_core

        monkeypatch.setattr(
            astrbot_core,
            "astrbot_config",
            {"mcp_settings": {"stdio_allowlist": ["from-config"]}},
        )

        assert _get_stdio_command_allowlist() == {"from-config"}

    def test_non_dict_mcp_settings_are_ignored(self, monkeypatch):
        import astrbot.core as astrbot_core

        monkeypatch.setattr(astrbot_core, "astrbot_config", {"mcp_settings": []})

        assert _get_stdio_command_allowlist() == set(_DEFAULT_STDIO_COMMAND_ALLOWLIST)


class TestErrorHint:
    def test_error_points_to_settings_page_and_env(self, monkeypatch):
        _use_settings(monkeypatch, {})

        with pytest.raises(ValueError) as excinfo:
            validate_mcp_stdio_config({"command": "my-launcher", "args": []})

        message = str(excinfo.value)
        assert "Dashboard -> Settings -> Security" in message
        assert ALLOWLIST_ENV in message
