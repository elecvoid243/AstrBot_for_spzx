from __future__ import annotations

import copy
import math
import traceback
import uuid

from astrbot.core import logger
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.provider.provider import Provider


class SubAgentServiceError(Exception):
    pass


class SubAgentService:
    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.core_lifecycle = core_lifecycle

    def get_config(self, config_id: str = "default") -> dict:
        """Read a profile policy and the shared static catalogue.

        Args:
            config_id: The configuration profile to inspect.

        Returns:
            The selected policy and the availability of catalogue entries.

        Raises:
            SubAgentServiceError: The profile does not exist.
        """
        profiles = self.core_lifecycle.astrbot_config_mgr.confs
        profile = profiles.get(config_id)
        if profile is None:
            raise SubAgentServiceError("Configuration profile does not exist")
        raw_catalogue = (
            profiles["default"].get("subagent_orchestrator", {}).get("agents")
        )
        catalogue = (
            [
                agent
                for agent in raw_catalogue
                if isinstance(agent, dict)
                and isinstance(agent.get("id"), str)
                and isinstance(agent.get("name"), str)
            ]
            if isinstance(raw_catalogue, list)
            else []
        )
        policy = copy.deepcopy(
            profile.get("subagent_policy") or DEFAULT_CONFIG["subagent_orchestrator"]
        )
        pool = profile.get("provider_settings", {}).get("provider_pool", ["*"])
        providers = self.core_lifecycle.provider_manager.inst_map
        personas = self.core_lifecycle.persona_mgr
        available = []
        for agent in catalogue:
            provider_id = agent.get("provider_id")
            persona_id = agent.get("persona_id")
            status = "available"
            if not agent.get("enabled", True):
                status = "disabled"
            elif agent.get("runtime") not in (None, "", "astrbot"):
                status = "runtime_unavailable"
            elif provider_id and (
                not isinstance(providers.get(provider_id), Provider)
                or ("*" not in pool and provider_id not in pool)
            ):
                status = "provider_unavailable"
            elif persona_id and personas.get_persona_v3_by_id(persona_id) is None:
                status = "persona_unavailable"
            available.append(
                {"id": agent["id"], "name": agent["name"], "status": status}
            )
        return self._normalize_config(
            {
                **policy,
                "agents": copy.deepcopy(catalogue) if config_id == "default" else [],
                "available_agents": available,
            }
        )

    async def update_config(self, data: object, config_id: str = "default") -> None:
        """Save one profile policy and, for the default profile, catalogue edits.

        Args:
            data: Submitted policy fields and optional static catalogue.
            config_id: Profile that owns the policy.

        Raises:
            SubAgentServiceError: A policy is malformed or the profile does not exist.
        """
        if not isinstance(data, dict):
            raise SubAgentServiceError("Subagent policy must be an object")
        profiles = self.core_lifecycle.astrbot_config_mgr.confs
        profile = profiles.get(config_id)
        if profile is None:
            raise SubAgentServiceError("Configuration profile does not exist")
        default = profiles["default"]
        legacy = default.get("subagent_orchestrator", {})
        catalogue = legacy.get("agents", [])
        proposed = data.get("agents")
        if proposed is not None:
            if config_id != "default":
                raise SubAgentServiceError(
                    "Edit the static catalogue in the default profile"
                )
            previous_by_id = (
                {
                    agent["id"]: agent
                    for agent in catalogue
                    if isinstance(agent, dict) and isinstance(agent.get("id"), str)
                }
                if isinstance(catalogue, list)
                else {}
            )
            ids_by_name = {
                agent["name"]: agent_id
                for agent_id, agent in previous_by_id.items()
                if isinstance(agent.get("name"), str)
            }
            catalogue = []
            ids = set()
            names = set()
            for item in proposed:
                name = str(item.get("name", "")).strip()
                agent_id = item.get("id") or ids_by_name.get(name) or uuid.uuid4().hex
                if (
                    not name
                    or name in names
                    or not isinstance(agent_id, str)
                    or agent_id in ids
                ):
                    raise SubAgentServiceError(
                        "Static subagents require unique names and IDs"
                    )
                names.add(name)
                ids.add(agent_id)
                catalogue.append(
                    {
                        **previous_by_id.get(agent_id, {}),
                        **item,
                        "name": name,
                        "id": agent_id,
                    }
                )

        current = profile.get("subagent_policy") or {}
        policy = {
            **current,
            **{
                k: v for k, v in data.items() if k not in ("agents", "available_agents")
            },
        }
        if "enable" in data and "main_enable" not in data:
            policy["main_enable"] = data["enable"]
        if proposed is not None and "agent_ids" not in data:
            policy["agent_ids"] = [
                agent["id"] for agent in catalogue if agent.get("enabled", True)
            ]
        timeout = policy.get("execution_timeout", 1200)
        dynamic = policy.get("dynamic_agents", {})
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not isinstance(dynamic, dict)
            or type(dynamic.get("max_subagent_count", 5)) is not int
            or dynamic.get("max_subagent_count", 5) < 0
        ):
            raise SubAgentServiceError(
                "Invalid subagent execution timeout or count limit"
            )
        if proposed is not None:
            default["subagent_orchestrator"] = {**legacy, "agents": catalogue}
        profile["subagent_policy"] = policy
        profile.save_config()
        if proposed is not None and self.core_lifecycle.subagent_orchestrator:
            await self.core_lifecycle.subagent_orchestrator.reload_from_config(
                default["subagent_orchestrator"]
            )

    def get_available_tools(self) -> list[dict]:
        try:
            tool_mgr = self.core_lifecycle.provider_manager.llm_tools
            tools = []
            for tool in tool_mgr.func_list:
                if self._is_subagent_internal_tool(tool):
                    continue
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "active": tool.active,
                        "handler_module_path": tool.handler_module_path,
                    }
                )
            return tools
        except Exception as exc:
            logger.error(traceback.format_exc())
            raise SubAgentServiceError(f"获取可用工具失败: {exc!s}") from exc

    @staticmethod
    def _normalize_config(data: object) -> dict:
        if not isinstance(data, dict):
            data = {
                "main_enable": False,
                "remove_main_duplicate_tools": False,
                "agents": [],
            }

        if "main_enable" not in data and "enable" in data:
            data["main_enable"] = bool(data.get("enable", False))

        data.setdefault("main_enable", False)
        data.setdefault("remove_main_duplicate_tools", False)
        data.setdefault("agents", [])

        agents = data.get("agents")
        if isinstance(agents, list):
            for agent in agents:
                if isinstance(agent, dict):
                    agent.setdefault("provider_id", None)
                    agent.setdefault("persona_id", None)

        return data

    @staticmethod
    def _is_subagent_internal_tool(tool) -> bool:
        return (
            isinstance(tool, HandoffTool)
            or tool.handler_module_path == "core.subagent_orchestrator"
        )
