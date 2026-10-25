"""Plugin deprecation guard tests.

When a plugin whose feature the kernel now provides (see
``MIGRATED_PLUGIN_FEATURES``) sits in the plugin store, the loader must skip
it entirely — no module import, no class instantiation — record it as a
failed plugin with the deprecation notice (surfaced by the dashboard), and
report the deprecation on a targeted reload instead of pretending it
succeeded.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import yaml

from astrbot.core.star.star_manager import PluginManager, star_map

DEPRECATED_PLUGIN_NAME = "astrbot_plugin_compact"
DEPRECATED_PLUGIN_DIR = "astrbot_plugin_compact"


def _write_deprecated_plugin(plugin_path: Path) -> None:
    """Create a plugin whose metadata name matches a migrated feature."""
    plugin_path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "name": DEPRECATED_PLUGIN_NAME,
        "version": "1.0.0",
        "author": "test",
        "desc": "deprecated plugin",
    }
    (plugin_path / "metadata.yaml").write_text(
        yaml.dump(metadata),
        encoding="utf-8",
    )
    # The deprecation guard must prevent this module from ever executing.
    (plugin_path / "main.py").write_text(
        "raise RuntimeError('deprecated plugin must not be imported')\n",
        encoding="utf-8",
    )


@pytest.fixture
def plugin_manager_pm(tmp_path, monkeypatch):
    """An isolated PluginManager whose plugin store lives in tmp_path."""
    plugin_dir = tmp_path / "astrbot_root" / "data" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    class MockContext:
        def __init__(self):
            self.stars = []

        def get_all_stars(self):
            return self.stars

    mock_context = MockContext()
    pm = PluginManager(cast(Any, mock_context), cast(Any, {}))

    monkeypatch.setattr(pm, "plugin_store_path", str(plugin_dir))
    monkeypatch.setattr(
        "astrbot.core.star.star_manager.sp.global_get",
        AsyncMock(return_value=[]),
    )
    return pm


def test_deprecated_plugin_is_not_loaded(plugin_manager_pm, tmp_path):
    _write_deprecated_plugin(
        tmp_path / "astrbot_root" / "data" / "plugins" / DEPRECATED_PLUGIN_DIR,
    )

    success, error = asyncio.run(plugin_manager_pm.load())

    # Bulk startup load does not report an error for an expected skip...
    assert success is True
    assert error is None
    # ...but the plugin is recorded with the deprecation notice...
    record = plugin_manager_pm.failed_plugin_dict[DEPRECATED_PLUGIN_DIR]
    assert "已弃用" in record["error"]
    assert "已迁移至" in record["error"]
    # ...never imported, and never registered as a star.
    module_path = f"data.plugins.{DEPRECATED_PLUGIN_DIR}.main"
    assert module_path not in sys.modules
    assert module_path not in star_map


def test_reload_of_deprecated_plugin_reports_deprecation(plugin_manager_pm, tmp_path):
    _write_deprecated_plugin(
        tmp_path / "astrbot_root" / "data" / "plugins" / DEPRECATED_PLUGIN_DIR,
    )
    # Startup load records the deprecation first; the dashboard reload button
    # operates on that record.
    asyncio.run(plugin_manager_pm.load())

    success, error = asyncio.run(
        plugin_manager_pm.reload_failed_plugin(DEPRECATED_PLUGIN_DIR),
    )

    assert success is False
    assert "已弃用" in error
    record = plugin_manager_pm.failed_plugin_dict[DEPRECATED_PLUGIN_DIR]
    assert "已弃用" in record["error"]
