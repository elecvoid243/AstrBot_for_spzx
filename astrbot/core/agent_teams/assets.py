"""Versioned role assets and local installations without conversation state."""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import exists
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select, update

from astrbot.core.config.agent_runner import (
    get_agent_runner_capabilities,
    get_agent_runner_capability_issues,
)
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    AgentAssetDraft,
    AgentAssetDraftPublication,
    AgentRoleInstallation,
    AgentRoleVersion,
    AgentWorkflowVersion,
)
from astrbot.core.tools.file_tool_mode import (
    DEFAULT_FILE_TOOL_MODE,
    file_tool_allowed,
    resolve_file_tool_mode,
)

ResourceId = Annotated[str, Field(min_length=1, max_length=128)]


class HumanWaitPolicy(BaseModel):
    """Designer-selected deadline behavior, independent of ordinary chat timeouts."""

    model_config = ConfigDict(extra="forbid", strict=True)
    timeout_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    timeout_action: Literal["wait", "use_recommendation"] = "wait"

    @model_validator(mode="after")
    def require_automatic_deadline(self):
        """Require an explicit deadline before allowing automatic decisions.

        Returns:
            The validated waiting policy.

        Raises:
            ValueError: Automatic recommendation use has no configured deadline.
        """
        if self.timeout_action == "use_recommendation" and self.timeout_seconds is None:
            raise ValueError(
                "Set a deadline before using a recommendation automatically"
            )
        return self


class RoleOverrides(BaseModel):
    """Optional configuration; lists inherit on null and disable when empty."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    prompt: str | None = Field(default=None, max_length=100_000)
    provider_id: ResourceId | None = None
    persona_id: ResourceId | None = None
    tools: list[ResourceId] | None = None
    skills: list[ResourceId] | None = None
    knowledge_bases: list[ResourceId] | None = None
    max_steps: int | None = Field(default=None, ge=1, le=200)
    tool_call_timeout: float | None = Field(default=None, gt=0, le=3600)
    context_length: int | None = Field(default=None, ge=1, le=1_000_000)
    max_instances: int | None = Field(default=None, ge=1, le=100)
    max_parallel: int | None = Field(default=None, ge=1, le=100)
    max_iterations: int | None = Field(default=None, ge=1, le=100)
    max_depth: int | None = Field(default=None, ge=1, le=20)
    human_wait: HumanWaitPolicy | None = None


class RoleDefinition(RoleOverrides):
    """Role content with direct resource identities."""

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    model_capabilities: list[Literal["tool_use", "image", "audio"]] = Field(
        default_factory=list
    )
    runner_capabilities: list[
        Literal["internal_checkpoint", "remote_query", "task_tools", "task_result"]
    ] = Field(default_factory=list)


class RoleInstallation(BaseModel):
    """Machine-local configuration stored separately from the definition."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    profile_id: ResourceId = "default"


class RoleSaveRequest(BaseModel):
    """A complete replacement definition and its local installation."""

    model_config = ConfigDict(extra="forbid", strict=True)
    definition: RoleDefinition
    installation: RoleInstallation = Field(default_factory=RoleInstallation)
    expected_version: int | None = Field(default=None, ge=1)


class RoleAssetError(ValueError):
    """User-visible asset error with field paths and an HTTP-compatible status."""

    def __init__(
        self, message: str, fields: list[dict] | None = None, status: int = 400
    ):
        """Create an error that dashboard callers can present directly.

        Args:
            message: User-visible description of the failure.
            fields: Optional diagnostics containing path, code and message keys.
            status: HTTP response status for this failure, defaulting to 400.
        """
        super().__init__(message)
        self.fields = fields or []
        self.status = status


class RoleAssetService:
    """Public asset boundary shared by dashboard and future run consumers."""

    def __init__(
        self,
        db: BaseDatabase,
        core_lifecycle=None,
        catalog_loader: Callable[[], Awaitable[dict]] | None = None,
    ):
        """Connect persistence and the current machine's resource inventory.

        Args:
            db: Database used to persist immutable versions and installations.
            core_lifecycle: Optional running core whose managers supply local
                resources. Without it or a loader, the resource lists are empty.
            catalog_loader: Optional asynchronous inventory source, taking
                precedence over core_lifecycle. Returns profiles, models,
                personas, tools, skills and knowledge_bases lists. Each resource
                has id and name keys, plus the configuration and availability
                metadata consumed by preview_role.
        """
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.catalog_loader = catalog_loader

    async def get_resources(
        self, *, file_tool_modes: dict[str, str] | None = None
    ) -> dict:
        """Read the current machine's inventory without exposing credentials.

        Args:
            file_tool_modes: Frozen behavior modes for selected profiles during
                recovery validation. Current resource authorization still applies.

        Returns:
            Profiles, live models, personas, tools, skills and knowledge bases.
        """
        if self.catalog_loader is not None:
            return await self.catalog_loader()
        catalog: dict[str, list] = {
            key: []
            for key in (
                "profiles",
                "models",
                "personas",
                "tools",
                "skills",
                "knowledge_bases",
            )
        }
        core = self.core_lifecycle
        if core is None:
            return catalog
        for profile_id, config in core.astrbot_config_mgr.confs.items():
            misc = config.get("agent_runner", {}).get("config", {}).get("misc", {})
            settings = config.get("provider_settings", {})
            runner = config.get("agent_runner", {})
            runner_config = runner.get("config", {})
            persona_id = (
                runner_config.get("persona", {}).get("persona_id", "default")
                if runner.get("runner_type") == "local"
                else runner_config.get("persona_id", "default")
            )
            catalog["profiles"].append(
                {
                    "id": profile_id,
                    "name": profile_id,
                    "runner_type": runner.get("runner_type", "local"),
                    "runner_config": get_agent_runner_capabilities(runner)["binding"],
                    "runner_issues": [
                        {
                            "path": "installation.profile_id.runner_config." + field,
                            "code": "MISSING",
                            "message": f"Configure {field} in profile {profile_id}",
                        }
                        for field in {
                            "dify": ["dify_api_key"],
                            "coze": ["coze_api_key", "bot_id"],
                            "dashscope": [
                                "dashscope_api_key",
                                "dashscope_app_id",
                                "dashscope_app_type",
                            ],
                        }.get(runner.get("runner_type"), [])
                        if not runner_config.get(field)
                    ],
                    "provider_pool": settings.get("provider_pool", ["*"]),
                    "persona_pool": settings.get("persona_pool", ["*"]),
                    # Implicit Webchat defaults do not apply a stored persona.
                    "persona_id": None
                    if persona_id in ("default", "[%None]")
                    else persona_id,
                    "max_steps": misc.get("max_steps", 30),
                    "tool_call_timeout": misc.get("tool_call_timeout", 60),
                    "context_length": config.get("agent_runner", {})
                    .get("config", {})
                    .get("compression", {})
                    .get("fallback_max_tokens", 128000),
                    "knowledge_bases": config.get("kb_names", []),
                    "skills": None,
                    "tools": None,
                    "file_tool_mode": (file_tool_modes or {}).get(
                        profile_id, resolve_file_tool_mode(settings)
                    ),
                }
            )
        for provider in core.provider_manager.get_insts():
            meta = provider.meta()
            config = provider.provider_config
            catalog["models"].append(
                {
                    "id": meta.id,
                    "name": f"{meta.id} / {meta.model or ''}",
                    "capabilities": config.get("modalities", []),
                    "enabled": config.get("enable", True),
                    # Compare declared adapter/model/source, never display names
                    # or credentials. Unidentified sources require manual review.
                    "fingerprint": hashlib.sha256(
                        json.dumps(
                            {
                                "adapter": meta.type,
                                "model": meta.model,
                                "source": config.get("api_base")
                                or config.get("api_base_url"),
                            },
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                    if meta.model
                    and (config.get("api_base") or config.get("api_base_url"))
                    else None,
                }
            )
        for persona in core.persona_mgr.personas_v3:
            catalog["personas"].append(
                {
                    "id": persona["name"],
                    "name": persona["name"],
                    "prompt": persona.get("prompt", ""),
                    "tools": persona.get("tools"),
                    "skills": persona.get("skills"),
                }
            )
        tool_mgr = core.provider_manager.llm_tools
        from astrbot.core.agent.mcp_client import MCPTool
        from astrbot.core.star.star import star_map
        from astrbot.core.tools.registry import get_builtin_tool_config_statuses

        tools = list(tool_mgr.func_list)
        registered = {tool.name for tool in tools}
        tools.extend(
            tool
            for tool in tool_mgr.iter_builtin_tools()
            if tool.name not in registered
        )
        for tool in tools:
            statuses = get_builtin_tool_config_statuses(
                tool.name,
                [
                    {"conf_id": profile_id, "conf_name": profile_id, "config": config}
                    for profile_id, config in core.astrbot_config_mgr.confs.items()
                ],
            )
            enabled_profiles = (
                [status["conf_id"] for status in statuses if status["enabled"]]
                if statuses
                else list(core.astrbot_config_mgr.confs)
            )
            enabled_profiles = [
                profile_id
                for profile_id in enabled_profiles
                if file_tool_allowed(
                    tool.name,
                    (file_tool_modes or {}).get(
                        profile_id,
                        resolve_file_tool_mode(
                            core.astrbot_config_mgr.confs[profile_id].get(
                                "provider_settings", {}
                            )
                        ),
                    ),
                )
            ]
            plugin = star_map.get(tool.handler_module_path)
            # Preserve MCP, unowned and reserved tools, as the main pipeline does.
            if plugin and not plugin.reserved and not isinstance(tool, MCPTool):
                enabled_profiles = [
                    profile_id
                    for profile_id in enabled_profiles
                    if core.astrbot_config_mgr.confs[profile_id].get(
                        "plugin_set", ["*"]
                    )
                    == ["*"]
                    or plugin.name
                    in core.astrbot_config_mgr.confs[profile_id].get(
                        "plugin_set", ["*"]
                    )
                ]
            catalog["tools"].append(
                {
                    "id": tool.name,
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                    "enabled": tool.active,
                    "persona_restricted": not tool_mgr.is_builtin_tool(tool.name),
                    "enabled_profiles": enabled_profiles,
                }
            )
        from astrbot.core.skills.skill_manager import SkillManager
        from astrbot.core.star.star import star_registry

        skill_manager = SkillManager()
        skills_by_name: dict[str, dict] = {}
        plugins = {plugin.root_dir_name: plugin for plugin in star_registry}
        # Match the main pipeline's runtime and plugin availability per profile.
        for profile_id, config in core.astrbot_config_mgr.confs.items():
            runtime = config.get("provider_settings", {}).get(
                "computer_use_runtime", "none"
            )
            plugin_set = config.get("plugin_set", ["*"])
            for skill in skill_manager.list_skills(runtime=runtime):
                resource = skills_by_name.setdefault(
                    skill.name,
                    {
                        "id": skill.name,
                        "name": skill.name,
                        "description": skill.description,
                        "enabled": skill.active,
                        "enabled_profiles": [],
                    },
                )
                if skill.source_type == "plugin":
                    plugin = plugins.get(skill.plugin_name)
                    if not plugin or not plugin.activated:
                        continue
                    if (
                        not plugin.reserved
                        and isinstance(plugin_set, list)
                        and "*" not in plugin_set
                        and plugin.name not in {str(name) for name in plugin_set}
                    ):
                        continue
                resource["enabled_profiles"].append(profile_id)
        catalog["skills"] = [skills_by_name[name] for name in sorted(skills_by_name)]
        for kb in await core.kb_manager.list_kbs():
            catalog["knowledge_bases"].append(
                {
                    "id": kb.kb_id,
                    "name": kb.kb_name,
                    "description": kb.description or "",
                }
            )
        return catalog

    async def resolve_role(
        self,
        username: str,
        role_id: str,
        version: int,
        overrides: dict | None = None,
    ) -> dict:
        """Preview inheritance and validate every required local binding.

        Args:
            username: Authenticated owner.
            role_id: Stable role identity.
            version: Fixed definition version.
            overrides: Optional task configuration; never saved to the role.

        Returns:
            Role, effective configuration, provenance and field-level issues.

        Raises:
            RoleAssetError: Invalid overrides or inaccessible role version.
        """
        role = await self.get_role(username, role_id, version)
        return {**role, **await self.preview_role(role, overrides)}

    async def validate_frozen_role(
        self, role: dict, profile_snapshot: dict | None = None
    ) -> None:
        """Check current resources against a frozen task role's requirements.

        Args:
            role: Pinned definition and effective configuration.
            profile_snapshot: Original behavior settings. Only the file tool mode
                affects catalog selection; live runtime and resource permissions
                cannot be restored by this snapshot.

        Raises:
            RoleAssetError: A frozen resource is unavailable or incompatible.
        """
        profile_id = role["installation"]["profile_id"]
        catalog = await self.get_resources(
            file_tool_modes={
                profile_id: resolve_file_tool_mode(
                    profile_snapshot.get("provider_settings", {})
                )
            }
            if profile_snapshot is not None
            else None
        )
        effective = role["effective"]
        profiles = [p for p in catalog["profiles"] if p["id"] == profile_id]
        profile = profiles[0] if len(profiles) == 1 else {}
        issues = []
        issues.extend(profile.get("runner_issues", []))
        if not profile:
            issues.append(
                {
                    "path": "installation.profile_id",
                    "code": "MISSING",
                    "message": "Bound profile is missing or ambiguous",
                }
            )
        runner = get_agent_runner_capabilities(
            {
                "runner_type": profile.get("runner_type", "local"),
                "config": profile.get("runner_config", {}),
            }
        )
        issues.extend(
            get_agent_runner_capability_issues(
                runner, {"definition.role_id": "task_result"}
            )
        )
        if effective.get("runner", runner) != runner:
            issues.append(
                {
                    "path": "installation.profile_id",
                    "code": "INCOMPATIBLE",
                    "message": "Bound profile no longer has the frozen runner capabilities",
                }
            )
        persona = next(
            (p for p in catalog["personas"] if p["id"] == effective.get("persona_id")),
            {},
        )
        for field, kind in (
            ("provider_id", "models"),
            ("persona_id", "personas"),
            ("tools", "tools"),
            ("skills", "skills"),
            ("knowledge_bases", "knowledge_bases"),
        ):
            value = effective.get(field)
            ids = value if isinstance(value, list) else [value] if value else []
            for resource_id in ids:
                resources = [r for r in catalog[kind] if r["id"] == resource_id]
                pool = profile.get(
                    "provider_pool" if kind == "models" else "persona_pool", ["*"]
                )
                if (
                    len(resources) != 1
                    or not resources[0].get("enabled", True)
                    or profile_id
                    not in resources[0].get("enabled_profiles", [profile_id])
                    or (
                        kind == "tools"
                        and not file_tool_allowed(
                            resource_id,
                            profile.get("file_tool_mode", DEFAULT_FILE_TOOL_MODE),
                        )
                    )
                    or (
                        kind in ("models", "personas")
                        and "*" not in pool
                        and resource_id not in pool
                    )
                    or (
                        kind in ("tools", "skills")
                        and persona.get(kind) is not None
                        and resource_id not in persona[kind]
                        and (
                            kind == "skills"
                            or resources[0].get("persona_restricted", True)
                        )
                    )
                ):
                    issues.append(
                        {
                            "path": f"effective.{field}",
                            "code": "UNAVAILABLE",
                            "message": f"Bound resource is unavailable: {resource_id}",
                        }
                    )
                elif kind == "models" and not set(
                    role["definition"]["model_capabilities"]
                ).issubset(resources[0].get("capabilities", [])):
                    issues.append(
                        {
                            "path": "effective.provider_id",
                            "code": "INCOMPATIBLE",
                            "message": "Bound model no longer declares the required capabilities",
                        }
                    )
        if issues:
            raise RoleAssetError("Frozen resources are no longer available", issues)

    async def preview_role(self, payload: dict, overrides: dict | None = None) -> dict:
        """Resolve unsaved configuration without creating an asset or session.

        Args:
            payload: Definition and local installation to preview.
            overrides: Optional task overrides, applied only to this preview.

        Returns:
            Effective values, their sources, export content and readiness issues.

        Raises:
            RoleAssetError: Invalid definition, installation or overrides.
        """
        request = self._validate_request(
            {
                key: payload[key]
                for key in ("definition", "installation")
                if key in payload
            }
        )
        role = {
            "definition": request.definition.model_dump(),
            "installation": request.installation.model_dump(),
        }
        try:
            task = RoleOverrides.model_validate(overrides or {}).model_dump(
                exclude_unset=True
            )
            task = {
                key: value
                for key, value in task.items()
                if value is not None
                or key in ("persona_id", "provider_id", "human_wait")
            }
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid task overrides",
                [
                    {
                        "path": "overrides." + ".".join(map(str, e["loc"])),
                        "code": e["type"],
                        "message": e["msg"],
                    }
                    for e in exc.errors()
                ],
            ) from exc
        for field in ("tools", "skills", "knowledge_bases"):
            seen = set()
            for index, resource_id in enumerate(task.get(field) or []):
                if resource_id in seen:
                    raise RoleAssetError(
                        "Resource identities must be unique within each list",
                        [
                            {
                                "path": f"overrides.{field}.{index}",
                                "code": "CONFLICT",
                                "message": f"Duplicate resource: {resource_id}",
                            }
                        ],
                    )
                seen.add(resource_id)
        definition = role["definition"]
        installation = role["installation"]
        catalog = await self.get_resources()
        issues: list[dict] = []
        sources: dict[str, str] = {}
        effective: dict = {}
        profile_id = installation.get("profile_id", "default")
        profiles = [p for p in catalog["profiles"] if p["id"] == profile_id]
        profile = profiles[0] if len(profiles) == 1 else {}
        issues.extend(profile.get("runner_issues", []))
        runner = get_agent_runner_capabilities(
            {
                "runner_type": profile.get("runner_type", "local"),
                "config": profile.get("runner_config", {}),
            }
        )
        effective["runner"] = runner
        sources["runner"] = f"profile:{profile_id}"
        issues.extend(
            get_agent_runner_capability_issues(
                runner,
                {
                    f"definition.runner_capabilities.{index}": capability
                    for index, capability in enumerate(
                        definition["runner_capabilities"]
                    )
                },
            )
        )
        if not profile:
            issues.append(
                {
                    "path": "installation.profile_id",
                    "code": "MISSING",
                    "message": f"Profile is missing: {profile_id}",
                }
            )
        persona: dict = {}
        provider_id = task.get("provider_id", definition["provider_id"])
        persona_id = task.get("persona_id", definition["persona_id"])
        requirements = [("provider_id", "models", provider_id)]
        effective["persona_id"] = persona_id
        sources["persona_id"] = "task" if "persona_id" in task else "role"
        if runner["type"] != "local":
            requirements = []
            effective["provider_id"] = None
            sources["provider_id"] = f"profile:{profile_id}"
            if provider_id:
                issues.append(
                    {
                        "path": "overrides.provider_id"
                        if "provider_id" in task
                        else "definition.provider_id",
                        "code": "INCOMPATIBLE",
                        "message": f"{runner['label']} does not use a local model",
                    }
                )
            for index, capability in enumerate(definition["model_capabilities"]):
                if capability not in runner["modalities"]:
                    issues.append(
                        {
                            "path": f"definition.model_capabilities.{index}",
                            "code": "INCOMPATIBLE",
                            "message": f"{runner['label']} does not support {capability}",
                        }
                    )
        if persona_id:
            requirements.insert(0, ("persona_id", "personas", persona_id))
        for field, kind, resource_id in requirements:
            matches = (
                [r for r in catalog[kind] if r["id"] == resource_id]
                if resource_id
                else []
            )
            path = f"{'overrides' if field in task else 'definition'}.{field}"
            effective[field] = resource_id
            sources[field] = "task" if field in task else "role"
            if len(matches) != 1:
                code = "AMBIGUOUS" if len(matches) > 1 else "MISSING"
                issues.append(
                    {
                        "path": path,
                        "code": code,
                        "message": f"Resource is {'ambiguous' if len(matches) > 1 else 'missing'}: {resource_id or field}",
                    }
                )
                continue
            resource = matches[0]
            pool = profile.get(
                "provider_pool" if kind == "models" else "persona_pool", ["*"]
            )
            if (
                resource.get("enabled", True) is False
                or profile_id not in resource.get("enabled_profiles", [profile_id])
                or ("*" not in pool and resource_id not in pool)
            ):
                issues.append(
                    {
                        "path": path,
                        "code": "FORBIDDEN",
                        "message": f"Resource is disabled or excluded by profile: {resource_id}",
                    }
                )
            if kind == "personas":
                persona = resource
            elif not set(definition["model_capabilities"]).issubset(
                resource.get("capabilities", [])
            ):
                issues.append(
                    {
                        "path": path,
                        "code": "INCOMPATIBLE",
                        "message": "Model does not declare the required capabilities",
                    }
                )
        effective["persona_prompt"] = persona.get("prompt")
        sources["persona_prompt"] = f"persona:{persona_id}" if persona else "role"
        effective["human_wait"] = (
            task.get("human_wait", definition.get("human_wait"))
            or HumanWaitPolicy().model_dump()
        )
        sources["human_wait"] = "task" if "human_wait" in task else "role"
        defaults = {
            "max_instances": 1,
            "max_parallel": 1,
            "max_depth": 3,
            "max_iterations": 20,
            "max_steps": 30,
            "tool_call_timeout": 60,
            "context_length": 128000,
            "prompt": None,
            "tools": None,
            "skills": None,
            "knowledge_bases": [],
        }
        for field, default in defaults.items():
            value = default if field == "prompt" else profile.get(field, default)
            if runner["type"] != "local" and field in (
                "tools",
                "skills",
                "knowledge_bases",
            ):
                value = []
            source = "role" if field == "prompt" else f"profile:{profile_id}"
            if runner["type"] != "local" and field in (
                "max_steps",
                "tool_call_timeout",
                "context_length",
            ):
                value, source = None, f"runner:{runner['type']}"
                if definition.get(field) is not None or field in task:
                    issues.append(
                        {
                            "path": f"overrides.{field}"
                            if field in task
                            else f"definition.{field}",
                            "code": "INCOMPATIBLE",
                            "message": f"{runner['label']} does not enforce the local runner setting {field}",
                        }
                    )
            if (
                runner["type"] == "local"
                and field in ("tools", "skills")
                and persona.get(field) is not None
            ):
                value, source = persona[field], f"persona:{persona_id}"
            logical = False
            if definition.get(field) is not None:
                value, source, logical = definition[field], "role", True
            if field in task:
                value, source, logical = task[field], "task", True
            if (
                runner["type"] != "local"
                and field in ("tools", "skills", "knowledge_bases")
                and value
            ):
                issues.append(
                    {
                        "path": f"{'overrides' if field in task else 'definition'}.{field}",
                        "code": "INCOMPATIBLE",
                        "message": f"{runner['label']} does not execute local {field}",
                    }
                )
            if field in ("tools", "skills", "knowledge_bases"):
                # Null expands only enabled resources; [] explicitly disables.
                selected = (
                    value
                    if value is not None
                    else [
                        r["id"]
                        for r in catalog[field]
                        if r.get("enabled", True)
                        and profile_id in r.get("enabled_profiles", [profile_id])
                        and (
                            field != "tools"
                            or file_tool_allowed(
                                r["id"],
                                profile.get("file_tool_mode", DEFAULT_FILE_TOOL_MODE),
                            )
                        )
                    ]
                )
                resolved = []
                for index, reference in enumerate(selected):
                    resource_id = reference
                    matches = [
                        r
                        for r in catalog[field]
                        if r["id"] == resource_id
                        or (
                            field == "knowledge_bases"
                            and not logical
                            and r["name"] == reference
                        )
                    ]
                    path = (
                        f"definition.{field}.{index}"
                        if source == "role"
                        else f"overrides.{field}.{index}"
                        if logical
                        else f"effective.{field}.{index}"
                    )
                    if (
                        len(matches) != 1
                        or not matches[0].get("enabled", True)
                        or profile_id
                        not in matches[0].get("enabled_profiles", [profile_id])
                        or (
                            field == "tools"
                            and not file_tool_allowed(
                                resource_id,
                                profile.get("file_tool_mode", DEFAULT_FILE_TOOL_MODE),
                            )
                        )
                    ):
                        file_mode = profile.get(
                            "file_tool_mode", DEFAULT_FILE_TOOL_MODE
                        )
                        mode_excluded = field == "tools" and not file_tool_allowed(
                            resource_id or reference, file_mode
                        )
                        code = (
                            "AMBIGUOUS"
                            if len(matches) > 1
                            else "INCOMPATIBLE"
                            if mode_excluded
                            else "FORBIDDEN"
                            if matches
                            else "MISSING"
                        )
                        issues.append(
                            {
                                "path": path,
                                "code": code,
                                "message": (
                                    f"Tool {resource_id or reference} is excluded by "
                                    f"file_tool_mode={file_mode} "
                                    f"in profile {profile_id}"
                                    if mode_excluded
                                    else f"Resource is unavailable: {resource_id or reference}"
                                ),
                            }
                        )
                    # Invocation allowlists narrow persona capabilities; they do
                    # not restore skills or registered tools removed by a persona.
                    if (
                        len(matches) == 1
                        and field in ("tools", "skills")
                        and persona.get(field) is not None
                        and resource_id not in persona[field]
                        and (
                            field == "skills"
                            or matches[0].get("persona_restricted", True)
                        )
                    ):
                        issues.append(
                            {
                                "path": path,
                                "code": "FORBIDDEN",
                                "message": f"Resource is excluded by persona: {resource_id}",
                            }
                        )
                        continue
                    resolved.append(
                        matches[0]["id"] if len(matches) == 1 else resource_id
                    )
                value = resolved
            effective[field], sources[field] = value, source
        if (
            not str(effective["prompt"] or "").strip()
            and not str(persona.get("prompt") or "").strip()
        ):
            issues.append(
                {
                    "path": "definition.prompt",
                    "code": "REQUIRED",
                    "message": "Provide a responsibility prompt or bind a persona",
                }
            )
        return {
            **role,
            "effective": effective,
            "sources": sources,
            "issues": issues,
            "ready": not issues,
            "export_content": {
                "prompt": definition.get("prompt"),
                "persona_id": definition.get("persona_id"),
                "persona_prompt": persona.get("prompt"),
            },
        }

    async def list_roles(self, username: str) -> dict:
        """List the latest version of each role owned by a user.

        Args:
            username: Authenticated owner.

        Returns:
            The role directory, excluding other users' assets.
        """
        async with self.db.get_db() as session:
            identities = (
                (
                    await session.execute(
                        select(AgentRoleVersion.role_id)
                        .where(
                            AgentRoleVersion.owner_username == username,
                        )
                        .distinct()
                        .order_by(AgentRoleVersion.role_id)
                    )
                )
                .scalars()
                .all()
            )
        return {
            "roles": [
                await self.get_role(username, identity) for identity in identities
            ]
        }

    @staticmethod
    def _validate_draft_body(kind: str, body: dict) -> None:
        """Limit draft size and reject obsolete or unknown configuration fields.

        Args:
            kind: Asset category.
            body: Potentially incomplete editor state.

        Raises:
            RoleAssetError: The draft is oversized or contains unknown fields.
        """
        if kind not in {"role", "workflow", "autonomous"}:
            raise RoleAssetError("Invalid draft kind")
        if (
            not isinstance(body, dict)
            or set(body) - {"definition", "installation", "editor"}
            or len(json.dumps(body).encode("utf-8")) > 1_000_000
        ):
            raise RoleAssetError("Invalid or oversized draft")
        from astrbot.core.agent_teams.graph import GraphDefinition, GraphTask
        from astrbot.core.agent_teams.workflows import (
            AutonomousDefinition,
            TeamRole,
            WorkflowDefinition,
        )

        definition = body.get("definition", {})
        if not isinstance(definition, dict):
            raise RoleAssetError("Invalid draft definition")
        if kind == "role":
            allowed = set(RoleDefinition.model_fields)
        elif kind == "autonomous":
            allowed = set(AutonomousDefinition.model_fields)
        elif kind == "workflow":
            allowed = set(GraphDefinition.model_fields) | set(
                WorkflowDefinition.model_fields
            )
        else:
            raise RoleAssetError("Invalid draft kind")
        if set(definition) - allowed:
            raise RoleAssetError("Unknown draft definition fields")
        installation = body.get("installation", {})
        if not isinstance(installation, dict) or set(installation) - set(
            RoleInstallation.model_fields
        ):
            raise RoleAssetError("Unknown draft installation fields")
        if "editor" in body and (
            not isinstance(body["editor"], dict)
            or set(body["editor"]) - {"approval", "contract"}
        ):
            raise RoleAssetError("Unknown draft editor fields")
        entries = []
        if kind == "workflow" and isinstance(definition.get("tasks"), list):
            entries = definition["tasks"]
            entry_fields = set(GraphTask.model_fields)
        elif kind == "autonomous":
            entries = [
                definition.get("coordinator"),
                *(
                    definition.get("allowed_roles")
                    if isinstance(definition.get("allowed_roles"), list)
                    else []
                ),
            ]
            entry_fields = set(TeamRole.model_fields)
        else:
            entry_fields = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if set(entry) - entry_fields:
                raise RoleAssetError("Unknown draft member fields")
            for field, model in (
                ("role", RoleDefinition),
                ("installation", RoleInstallation),
                ("overrides", RoleOverrides),
            ):
                value = entry.get(field)
                if isinstance(value, dict) and set(value) - set(model.model_fields):
                    raise RoleAssetError("Unknown draft member configuration fields")

    async def create_draft(
        self, username: str, kind: str, body: dict, asset_id: str | None = None
    ) -> dict:
        """Create a persistent editing snapshot with a stable asset identity.

        Args:
            username: Authenticated owner.
            kind: Role, workflow or autonomous team.
            body: Incomplete editing snapshot.
            asset_id: Existing published identity, when editing one.

        Returns:
            The saved draft.

        Raises:
            RoleAssetError: Invalid content, identity or duplicate draft.
        """
        self._validate_draft_body(kind, body)
        base_version = None
        if asset_id:
            if kind == "role":
                base_version = (await self.get_role(username, asset_id))["version"]
            else:
                async with self.db.get_db() as session:
                    row = (
                        (
                            await session.execute(
                                select(AgentWorkflowVersion)
                                .where(
                                    AgentWorkflowVersion.asset_id == asset_id,
                                    AgentWorkflowVersion.owner_username == username,
                                )
                                .order_by(col(AgentWorkflowVersion.version).desc())
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if row is None or (row.definition.get("kind") == "autonomous") != (
                        kind == "autonomous"
                    ):
                        raise RoleAssetError("Asset not found", status=404)
                    base_version = row.version
        else:
            asset_id = uuid.uuid4().hex
        async with self.db.get_db() as session:
            row = AgentAssetDraft(
                kind=kind,
                asset_id=asset_id,
                owner_username=username,
                body=body,
                base_version=base_version,
            )
            session.add(row)
            try:
                await session.flush()
                saved = row.model_dump()
                await session.commit()
            except IntegrityError as exc:
                raise RoleAssetError("Draft already exists", status=409) from exc
        return saved

    async def get_draft(self, username: str, kind: str, asset_id: str) -> dict:
        """Read an owner-scoped draft.

        Args:
            username: Authenticated owner.
            kind: Asset kind.
            asset_id: Stable asset identity.

        Returns:
            The persisted draft fields.

        Raises:
            RoleAssetError: The draft is inaccessible.
        """
        async with self.db.get_db() as session:
            row = await session.get(AgentAssetDraft, (kind, asset_id))
            if row is None or row.owner_username != username:
                raise RoleAssetError("Draft not found", status=404)
            return row.model_dump()

    async def list_drafts(self, username: str, kind: str) -> list[dict]:
        """List an owner's editing snapshots for an asset kind.

        Args:
            username: Authenticated owner.
            kind: Asset kind.

        Returns:
            Drafts including assets without published versions.
        """
        async with self.db.get_db() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentAssetDraft).where(
                            AgentAssetDraft.owner_username == username,
                            AgentAssetDraft.kind == kind,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [row.model_dump() for row in rows]

    async def update_draft(
        self, username: str, kind: str, asset_id: str, revision: int, body: dict
    ) -> dict:
        """Compare and update a draft without overwriting concurrent edits.

        Args:
            username: Authenticated owner.
            kind: Asset kind.
            asset_id: Stable asset identity.
            revision: Expected current revision.
            body: New incomplete editing snapshot.

        Returns:
            The updated draft.

        Raises:
            RoleAssetError: Invalid content or conflicting revision.
        """
        self._validate_draft_body(kind, body)
        async with self.db.get_db() as session:
            result = await session.execute(
                update(AgentAssetDraft)
                .where(
                    AgentAssetDraft.kind == kind,
                    AgentAssetDraft.asset_id == asset_id,
                    AgentAssetDraft.owner_username == username,
                    AgentAssetDraft.revision == revision,
                )
                .values(body=body, revision=revision + 1)
                .returning(AgentAssetDraft)
            )
            row = result.scalars().first()
            if row is None:
                raise RoleAssetError("Draft was edited elsewhere", status=409)
            saved = row.model_dump()
            await session.commit()
        return saved

    async def rebase_draft(
        self, username: str, kind: str, asset_id: str, revision: int, version: int
    ) -> dict:
        """Explicitly move a saved draft onto a reviewed published version.

        Args:
            username: Authenticated owner.
            kind: Asset kind.
            asset_id: Stable asset identity.
            revision: Expected current draft revision.
            version: Latest published version reviewed by the caller.

        Returns:
            The updated draft without changing its body.

        Raises:
            RoleAssetError: The draft or reviewed published version changed.
        """
        if kind not in {"role", "workflow", "autonomous"}:
            raise RoleAssetError("Invalid draft kind")
        model = AgentRoleVersion if kind == "role" else AgentWorkflowVersion
        identity = model.role_id if kind == "role" else model.asset_id
        matching = exists().where(
            identity == asset_id,
            model.owner_username == username,
            model.version == version,
        )
        newer = exists().where(
            identity == asset_id,
            model.owner_username == username,
            model.version > version,
        )
        async with self.db.get_db() as session:
            result = await session.execute(
                update(AgentAssetDraft)
                .where(
                    AgentAssetDraft.kind == kind,
                    AgentAssetDraft.asset_id == asset_id,
                    AgentAssetDraft.owner_username == username,
                    AgentAssetDraft.revision == revision,
                    matching,
                    ~newer,
                )
                .values(base_version=version, revision=revision + 1)
                .returning(AgentAssetDraft)
            )
            row = result.scalars().first()
            if row is None:
                raise RoleAssetError("Draft or reviewed version changed", status=409)
            saved = row.model_dump()
            await session.commit()
        return saved

    async def save_role(
        self,
        username: str,
        payload: dict,
        role_id: str | None = None,
        draft_revision: int | None = None,
    ) -> dict:
        """Create a role or append a version without overwriting history.

        Args:
            username: Authenticated owner.
            payload: Complete definition, installation and expected version.
            role_id: Existing identity when appending a version.
            draft_revision: Trusted draft revision to publish, when supplied.

        Returns:
            The persisted role version and local installation.

        Raises:
            RoleAssetError: Invalid fields, inaccessible role or stale version.
        """
        draft = None
        if draft_revision is not None:
            if role_id is None:
                raise RoleAssetError("Draft identity is required")
            try:
                draft = await self.get_draft(username, "role", role_id)
            except RoleAssetError as exc:
                if exc.status == 404:
                    raise RoleAssetError("Draft is inaccessible", status=409) from exc
                raise
            self._validate_draft_body("role", draft["body"])
            async with self.db.get_db() as session:
                published = await session.get(
                    AgentAssetDraftPublication, ("role", role_id, draft_revision)
                )
                published_version = published.version if published else None
            if published_version is not None:
                return await self.get_role(username, role_id, published_version)
            if draft["revision"] != draft_revision:
                raise RoleAssetError("Draft was edited elsewhere", status=409)
            payload = {
                **{k: v for k, v in draft["body"].items() if k != "editor"},
                "expected_version": draft["base_version"],
            }
        request = self._validate_request(payload)
        version = 1
        if role_id is not None and (draft is None or draft["base_version"] is not None):
            current = await self.get_role(username, role_id)
            if request.expected_version != current["version"]:
                raise RoleAssetError(
                    "Role was edited elsewhere; reload first", status=409
                )
            version = current["version"] + 1
        elif role_id is None:
            role_id = uuid.uuid4().hex
        async with self.db.get_db() as session:
            if draft is not None:
                result = await session.execute(
                    update(AgentAssetDraft)
                    .where(
                        AgentAssetDraft.kind == "role",
                        AgentAssetDraft.asset_id == role_id,
                        AgentAssetDraft.owner_username == username,
                        AgentAssetDraft.revision == draft_revision,
                        AgentAssetDraft.base_version == draft["base_version"],
                    )
                    .values(
                        base_version=version,
                        published_revision=draft_revision,
                        published_version=version,
                    )
                )
                if result.rowcount != 1:
                    await session.rollback()
                    published = await session.get(
                        AgentAssetDraftPublication, ("role", role_id, draft_revision)
                    )
                    if published is not None:
                        return await self.get_role(username, role_id, published.version)
                    raise RoleAssetError("Draft was edited elsewhere", status=409)
                session.add(
                    AgentAssetDraftPublication(
                        kind="role",
                        asset_id=role_id,
                        revision=draft_revision,
                        version=version,
                    )
                )
            session.add(
                AgentRoleVersion(
                    role_id=role_id,
                    version=version,
                    owner_username=username,
                    definition=request.definition.model_dump(),
                )
            )
            session.add(
                AgentRoleInstallation(
                    role_id=role_id,
                    version=version,
                    installation=request.installation.model_dump(),
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                raise RoleAssetError(
                    "Role was edited elsewhere; reload first", status=409
                ) from exc
        return await self.get_role(username, role_id, version)

    @staticmethod
    def _validate_request(payload: dict) -> RoleSaveRequest:
        """Validate field shapes and resource identity uniqueness within each list.

        Args:
            payload: Complete role save or preview request.

        Returns:
            Validated definition and installation.

        Raises:
            RoleAssetError: A field is invalid or contains duplicate identities.
        """
        try:
            request = RoleSaveRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid role configuration",
                [
                    {
                        "path": ".".join(map(str, e["loc"])),
                        "code": e["type"],
                        "message": e["msg"],
                    }
                    for e in exc.errors()
                ],
            ) from exc
        for field in ("tools", "skills", "knowledge_bases"):
            slots = set()
            for index, slot in enumerate(getattr(request.definition, field) or []):
                if slot in slots:
                    raise RoleAssetError(
                        "Resource identities must be unique within each list",
                        [
                            {
                                "path": f"definition.{field}.{index}",
                                "code": "CONFLICT",
                                "message": f"Duplicate resource: {slot}",
                            }
                        ],
                    )
                slots.add(slot)
        return request

    async def get_role(
        self, username: str, role_id: str, version: int | None = None
    ) -> dict:
        """Read one immutable definition and its local installation.

        Args:
            username: Authenticated owner.
            role_id: Stable role identity.
            version: Pinned version, or the latest when omitted.

        Returns:
            Definition and installation with available historical versions.

        Raises:
            RoleAssetError: The identity or version is not accessible.
        """
        async with self.db.get_db() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentRoleVersion)
                        .where(
                            AgentRoleVersion.role_id == role_id,
                            AgentRoleVersion.owner_username == username,
                        )
                        .order_by(col(AgentRoleVersion.version).desc())
                    )
                )
                .scalars()
                .all()
            )
            row = next(
                (r for r in rows if version is None or r.version == version), None
            )
            if row is None:
                raise RoleAssetError("Role version not found", status=404)
            installation = await session.get(
                AgentRoleInstallation, (role_id, row.version)
            )
            self._validate_request(
                {
                    "definition": row.definition,
                    "installation": installation.installation if installation else {},
                }
            )
            return {
                "role_id": role_id,
                "version": row.version,
                "definition": row.definition,
                "installation": installation.installation if installation else {},
                "versions": [r.version for r in rows],
            }
