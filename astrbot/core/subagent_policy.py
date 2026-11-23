"""Resolve subagent policy from a configuration profile."""

import math
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from astrbot.core.config.default import DEFAULT_CONFIG


def _name_set(value: object) -> frozenset[str]:
    """Read configured resource names, ignoring invalid imported entries."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(name for name in value if isinstance(name, str) and name)
    return frozenset()


@dataclass(frozen=True, slots=True)
class SubAgentPolicy:
    """The subagent settings effective for one dispatch."""

    enabled: bool
    agent_ids: frozenset[str]
    dynamic_enabled: bool
    max_subagent_count: int
    auto_cleanup_per_turn: bool
    default_provider_id: str
    execution_timeout: float
    context_inherit_mode: str
    router_system_prompt: str
    rule_prompt: str
    time_prompt_enabled: bool
    timezone: str | None
    remove_main_duplicate_tools: bool
    tools_blacklist: frozenset[str]
    tools_inherent: frozenset[str]
    mcp_blacklist: frozenset[str]
    mcp_inherent: frozenset[str]
    provider_pool: frozenset[str]

    @classmethod
    def from_profile(cls, config: Mapping) -> "SubAgentPolicy | None":
        """Read an independent policy without changing shared configuration.

        Args:
            config: The configuration effective for the current conversation.

        Returns:
            The policy, or None for a caller without a configured profile.
        """
        if "subagent_policy" not in config:
            return None
        stored = config.get("subagent_policy")
        raw = stored if isinstance(stored, dict) else {}
        defaults = DEFAULT_CONFIG["subagent_orchestrator"]
        dynamic = defaults["dynamic_agents"] | (
            raw["dynamic_agents"] if isinstance(raw.get("dynamic_agents"), dict) else {}
        )
        timeout = raw.get("execution_timeout", defaults["execution_timeout"])
        if type(timeout) not in (int, float) or not math.isfinite(timeout):
            timeout = defaults["execution_timeout"]
        count = dynamic["max_subagent_count"]
        if type(count) is not int or count < 0:
            count = defaults["dynamic_agents"]["max_subagent_count"]
        inheritance = raw.get("context_inherit_mode")
        return cls(
            enabled=raw.get("main_enable") is True,
            agent_ids=_name_set(raw.get("agent_ids")),
            dynamic_enabled=dynamic["enabled"] is True,
            max_subagent_count=count,
            auto_cleanup_per_turn=dynamic["auto_cleanup_per_turn"],
            default_provider_id=dynamic["default_provider_id"]
            if isinstance(dynamic["default_provider_id"], str)
            else "",
            execution_timeout=timeout,
            context_inherit_mode=inheritance
            if inheritance in ("normal", "fork", "auto")
            else "normal",
            router_system_prompt=str(raw.get("router_system_prompt") or ""),
            rule_prompt=dynamic["rule_prompt"],
            time_prompt_enabled=raw.get("time_prompt_enabled", True),
            timezone=config.get("timezone"),
            remove_main_duplicate_tools=raw.get("remove_main_duplicate_tools", False),
            tools_blacklist=_name_set(dynamic["tools_blacklist"]),
            tools_inherent=_name_set(dynamic["tools_inherent"]),
            mcp_blacklist=_name_set(dynamic["mcp_blacklist"]),
            mcp_inherent=_name_set(dynamic["mcp_inherent"]),
            provider_pool=_name_set(
                config.get("provider_settings", {}).get("provider_pool", ["*"])
            ),
        )


# New dispatches set this context value before starting a foreground or background task.
current_subagent_policy: ContextVar[SubAgentPolicy | None] = ContextVar(
    "current_subagent_policy", default=None
)
