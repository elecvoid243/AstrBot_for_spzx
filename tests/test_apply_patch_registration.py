"""Registration checks that do not import the tool module directly."""

from __future__ import annotations

from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.tools.registry import get_builtin_tool_class


def test_tool_is_registered():
    tool_cls = get_builtin_tool_class("astrbot_apply_patch")
    assert tool_cls is not None
    assert tool_cls.name == "astrbot_apply_patch"


def test_tool_is_in_inherent_list():
    inherent = DEFAULT_CONFIG["subagent_orchestrator"]["dynamic_agents"][
        "tools_inherent"
    ]
    assert "astrbot_apply_patch" in inherent


def test_default_subagent_blacklist_excludes_removed_dag_tool():
    blacklist = DEFAULT_CONFIG["subagent_orchestrator"]["dynamic_agents"][
        "tools_blacklist"
    ]
    assert "orchestrate_tasks" not in blacklist
