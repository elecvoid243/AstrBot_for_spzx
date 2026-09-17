"""Unit tests for the dashboard-editable update sources.

Covers the read/write surface added to ``UpdateConfig`` for the settings page:
descriptor reporting (including environment-variable override state), the
preservation of unrelated keys when saving, and the input validation that keeps
a broken template from reaching the download path.
"""

import json
from pathlib import Path

import pytest

import astrbot.core.config.update_config as update_config_module
from astrbot.core.config.update_config import (
    DEFAULT_CONFIG,
    EDITABLE_UPDATE_SOURCES,
    UpdateConfig,
    validate_update_source,
)

DASHBOARD_REGISTRY_CUSTOM = "https://mirror.example.com/astrbot/{version}/dist.zip"


def _config_path(tmp_path: Path) -> str:
    return str(tmp_path / "update_config.json")


def test_get_update_sources_reports_builtin_defaults(tmp_path: Path) -> None:
    """A missing config file yields the built-in defaults, unlocked by env."""
    sources = UpdateConfig(config_path=_config_path(tmp_path)).get_update_sources()

    assert set(sources) == set(EDITABLE_UPDATE_SOURCES)
    for field, path in EDITABLE_UPDATE_SOURCES.items():
        expected = DEFAULT_CONFIG
        for key in path.split("."):
            expected = expected[key]
        assert sources[field]["value"] == expected
        assert sources[field]["default"] == expected
        assert sources[field]["env_locked"] is False


def test_get_update_sources_marks_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An environment variable wins over the file and is reported as locked."""
    config_path = _config_path(tmp_path)
    UpdateConfig(config_path=config_path).save_update_sources(
        {"core_release_api_url": "https://file.example.com/releases"}
    )
    monkeypatch.setenv(
        "ASTRBOT_CORE_RELEASE_API_URL", "https://env.example.com/releases"
    )

    sources = UpdateConfig(config_path=config_path).get_update_sources()

    assert (
        sources["core_release_api_url"]["value"] == "https://env.example.com/releases"
    )
    assert sources["core_release_api_url"]["env_locked"] is True
    assert sources["core_release_api_url"]["env_var"] == "ASTRBOT_CORE_RELEASE_API_URL"
    # Untouched fields stay file/default backed.
    assert sources["core_package_base_url"]["env_locked"] is False


def test_save_update_sources_preserves_unrelated_keys(tmp_path: Path) -> None:
    """Saving one source keeps every other key in the file byte-for-byte intact."""
    config_path = Path(_config_path(tmp_path))
    config_path.write_text(
        json.dumps(
            {
                "update_config_version": 1,
                "core_update": {
                    "release_api_url": "https://old.example.com/releases",
                    "github_archive_url_template": "https://mirror.example.com/{version}.zip",
                },
                "custom_section": {"keep": True},
            },
            indent=4,
        ),
        encoding="utf-8",
    )

    UpdateConfig(config_path=str(config_path)).save_update_sources(
        {"dashboard_registry_url_template": DASHBOARD_REGISTRY_CUSTOM}
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert (
        saved["dashboard_update"]["registry_url_template"] == DASHBOARD_REGISTRY_CUSTOM
    )
    assert saved["core_update"]["release_api_url"] == "https://old.example.com/releases"
    assert (
        saved["core_update"]["github_archive_url_template"]
        == "https://mirror.example.com/{version}.zip"
    )
    assert saved["custom_section"] == {"keep": True}
    # The built-in defaults must not be merged into the user file.
    assert "package_base_url" not in saved["core_update"]


def test_save_update_sources_strips_surrounding_whitespace(tmp_path: Path) -> None:
    """Leading/trailing whitespace from copy-paste is normalized before storing."""
    config_path = _config_path(tmp_path)
    config = UpdateConfig(config_path=config_path)

    config.save_update_sources(
        {"core_package_base_url": "  https://mirror.example.com/core  "}
    )

    saved = json.loads(Path(config_path).read_text(encoding="utf-8"))
    assert saved["core_update"]["package_base_url"] == "https://mirror.example.com/core"


def test_save_update_sources_recovers_from_corrupt_file(tmp_path: Path) -> None:
    """An unparseable file is replaced by a valid one holding the new value."""
    config_path = Path(_config_path(tmp_path))
    config_path.write_text("{ this is not json", encoding="utf-8")

    UpdateConfig(config_path=str(config_path)).save_update_sources(
        {"dashboard_registry_url_template": DASHBOARD_REGISTRY_CUSTOM}
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert (
        saved["dashboard_update"]["registry_url_template"] == DASHBOARD_REGISTRY_CUSTOM
    )


def test_save_update_sources_ignores_unknown_fields(tmp_path: Path) -> None:
    """Field names outside the editable allow-list are never written to disk."""
    config_path = Path(_config_path(tmp_path))
    original = {"proxy": {"url": "https://keep.example.com"}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    UpdateConfig(config_path=str(config_path)).save_update_sources(
        {"proxy_url": "https://attacker.example.com", "not_a_field": "x"}
    )

    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_save_update_sources_rejects_invalid_value_without_writing(
    tmp_path: Path,
) -> None:
    """A rejected value leaves the file untouched (no partial write)."""
    config_path = Path(_config_path(tmp_path))
    original = {"core_update": {"release_api_url": "https://keep.example.com/releases"}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(ValueError, match="必须保留"):
        UpdateConfig(config_path=str(config_path)).save_update_sources(
            {
                "core_package_base_url": "https://mirror.example.com/core",
                "dashboard_registry_url_template": "https://mirror.example.com/dist.zip",
            }
        )

    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_save_update_sources_leaves_no_temp_file(tmp_path: Path) -> None:
    """The atomic write must not leave the intermediate .tmp file behind."""
    config_path = Path(_config_path(tmp_path))

    UpdateConfig(config_path=str(config_path)).save_update_sources(
        {"core_release_api_url": "https://mirror.example.com/releases"}
    )

    assert not (tmp_path / "update_config.json.tmp").exists()


def test_builtin_defaults_pass_validation() -> None:
    """Every shipped default must be accepted, so "restore defaults" works."""
    for field, path in EDITABLE_UPDATE_SOURCES.items():
        default = DEFAULT_CONFIG
        for key in path.split("."):
            default = default[key]
        assert validate_update_source(field, default) is None


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("core_release_api_url", "", "不能为空"),
        ("core_release_api_url", "   ", "不能为空"),
        ("core_release_api_url", "mirror.example.com/releases", "http 或 https"),
        ("core_release_api_url", "ftp://mirror.example.com/releases", "http 或 https"),
        ("core_release_api_url", "https://mirror.example.com/a b", "空白字符"),
        (
            "core_release_api_url",
            "https://mirror.example.com/{version}",
            "不支持占位符",
        ),
        (
            "dashboard_registry_url_template",
            "https://mirror.example.com/dist.zip",
            "{version}",
        ),
        (
            "dashboard_registry_url_template",
            "https://mirror.example.com/{tag}/d.zip",
            "{version}",
        ),
        (
            "dashboard_registry_url_template",
            "https://mirror.example.com/{version}/{}/d.zip",
            "无法解析",
        ),
        (
            "dashboard_registry_url_template",
            "https://mirror.example.com/{version}/{0}/d.zip",
            "无法解析",
        ),
        ("not_a_field", "https://mirror.example.com/releases", "未知的更新源字段"),
    ],
)
def test_validate_update_source_rejects(field: str, value: str, expected: str) -> None:
    """Validation rejects malformed input with an actionable message."""
    message = validate_update_source(field, value)
    assert message is not None
    assert expected in message


def test_validate_update_source_accepts_common_mirrors() -> None:
    """Realistic mirror layouts must be accepted."""
    assert (
        validate_update_source(
            "dashboard_registry_url_template",
            "http://10.0.0.5:8080/astrbot/dashboard/{version}/dist.zip",
        )
        is None
    )
    assert (
        validate_update_source(
            "core_release_api_url",
            "https://mirror.example.com/api/v2/releases?channel=stable",
        )
        is None
    )


def test_update_sources_are_logged_when_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful save leaves an audit trail naming the changed fields."""
    messages: list[str] = []
    monkeypatch.setattr(
        update_config_module.logger,
        "info",
        lambda message, *args: messages.append(message % args if args else message),
    )

    UpdateConfig(config_path=_config_path(tmp_path)).save_update_sources(
        {"core_release_api_url": "https://mirror.example.com/releases"}
    )

    assert any("core_release_api_url" in message for message in messages)
