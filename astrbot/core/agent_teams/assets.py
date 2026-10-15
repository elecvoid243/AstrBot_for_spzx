"""Versioned role assets and local installations without conversation state."""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from astrbot.core.config.agent_runner import get_agent_runner_capabilities
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import AgentRoleInstallation, AgentRoleVersion

SlotName = Annotated[str, Field(min_length=1, max_length=128)]


class RoleOverrides(BaseModel):
    """Optional configuration; null inherits and an empty list disables."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    prompt: str | None = Field(default=None, max_length=100_000)
    tools: list[SlotName] | None = None
    skills: list[SlotName] | None = None
    knowledge_bases: list[SlotName] | None = None
    max_steps: int | None = Field(default=None, ge=1, le=200)
    tool_call_timeout: float | None = Field(default=None, gt=0, le=3600)
    context_length: int | None = Field(default=None, ge=1, le=1_000_000)
    max_instances: int | None = Field(default=None, ge=1, le=100)
    max_parallel: int | None = Field(default=None, ge=1, le=100)
    max_iterations: int | None = Field(default=None, ge=1, le=100)
    max_depth: int | None = Field(default=None, ge=1, le=20)


class RoleDefinition(RoleOverrides):
    """Portable role content; resource names refer to logical slots."""

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    model_slot: SlotName = "model"
    persona_slot: SlotName | None = None
    model_capabilities: list[Literal["tool_use", "image", "audio"]] = Field(
        default_factory=list
    )
    runner_capabilities: list[
        Literal["internal_checkpoint", "remote_query", "task_tools", "task_result"]
    ] = Field(default_factory=list)


class RoleInstallation(BaseModel):
    """Machine-local configuration stored separately from the definition."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    profile_id: SlotName = "default"
    bindings: dict[SlotName, SlotName] = Field(default_factory=dict)


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

    async def get_resources(self) -> dict:
        """Read the current machine's inventory without exposing credentials.

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
            catalog["knowledge_bases"].append({"id": kb.kb_id, "name": kb.kb_name})
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

    async def validate_frozen_role(self, role: dict) -> None:
        """Check current access and compatibility without changing saved values.

        Args:
            role: Pinned role definition, installation and effective configuration.

        Raises:
            RoleAssetError: A frozen binding is unavailable or incompatible.
        """
        catalog = await self.get_resources()
        profile_id = role["installation"]["profile_id"]
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
                exclude_none=True
            )
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
        definition = role["definition"]
        installation = role["installation"]
        bindings = installation.get("bindings", {})
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
        for index, capability in enumerate(definition["runner_capabilities"]):
            if capability not in runner["capabilities"]:
                issues.append(
                    {
                        "path": f"definition.runner_capabilities.{index}",
                        "code": "INCOMPATIBLE",
                        "message": f"{runner['label']} does not support {capability}",
                    }
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
        requirements = [("provider_id", "models", definition["model_slot"])]
        if runner["type"] != "local":
            requirements = []
            effective["provider_id"] = None
            sources["provider_id"] = f"profile:{profile_id}"
            for index, capability in enumerate(definition["model_capabilities"]):
                if capability not in runner["modalities"]:
                    issues.append(
                        {
                            "path": f"definition.model_capabilities.{index}",
                            "code": "INCOMPATIBLE",
                            "message": f"{runner['label']} does not support {capability}",
                        }
                    )
        if definition.get("persona_slot"):
            requirements.insert(
                0, ("persona_id", "personas", definition["persona_slot"])
            )
        elif profile.get("persona_id"):
            requirements.insert(0, ("persona_id", "personas", profile["persona_id"]))
        for field, kind, slot in requirements:
            inherited_persona = field == "persona_id" and not definition.get(
                "persona_slot"
            )
            resource_id = slot if inherited_persona else bindings.get(slot)
            matches = (
                [r for r in catalog[kind] if r["id"] == resource_id]
                if resource_id
                else []
            )
            path = (
                "installation.profile_id"
                if inherited_persona
                else f"installation.bindings.{slot}"
            )
            effective[field] = resource_id
            sources[field] = (
                f"profile:{profile_id}" if inherited_persona else f"installation:{slot}"
            )
            if len(matches) != 1:
                code = "AMBIGUOUS" if len(matches) > 1 else "MISSING"
                issues.append(
                    {
                        "path": path,
                        "code": code,
                        "message": f"Resource is {'ambiguous' if len(matches) > 1 else 'missing'}: {resource_id or slot}",
                    }
                )
                continue
            resource = matches[0]
            pool = profile.get(
                "provider_pool" if kind == "models" else "persona_pool", ["*"]
            )
            if resource.get("enabled", True) is False or (
                "*" not in pool and resource_id not in pool
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
            value = profile.get(field, default)
            if runner["type"] != "local" and field in (
                "tools",
                "skills",
                "knowledge_bases",
            ):
                value = []
            source = f"profile:{profile_id}"
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
            if field in ("prompt", "tools", "skills") and (
                definition.get("persona_slot") or (field != "prompt" and persona)
            ):
                value, source = (
                    persona.get(field),
                    f"persona:{definition['persona_slot']}"
                    if definition.get("persona_slot")
                    else f"profile:{profile_id}",
                )
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
                        "path": f"effective.{field}",
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
                    ]
                )
                resolved = []
                for index, reference in enumerate(selected):
                    resource_id = bindings.get(reference) if logical else reference
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
                        f"installation.bindings.{reference}"
                        if logical
                        else f"effective.{field}.{index}"
                    )
                    if (
                        len(matches) != 1
                        or not matches[0].get("enabled", True)
                        or profile_id
                        not in matches[0].get("enabled_profiles", [profile_id])
                    ):
                        code = (
                            "AMBIGUOUS"
                            if len(matches) > 1
                            else "FORBIDDEN"
                            if matches
                            else "MISSING"
                        )
                        issues.append(
                            {
                                "path": path,
                                "code": code,
                                "message": f"Resource is unavailable: {resource_id or reference}",
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
        if not str(effective["prompt"] or "").strip():
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
                "persona_slot": definition.get("persona_slot"),
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

    async def save_role(
        self, username: str, payload: dict, role_id: str | None = None
    ) -> dict:
        """Create a role or append a version without overwriting history.

        Args:
            username: Authenticated owner.
            payload: Complete definition, installation and expected version.
            role_id: Existing identity when appending a version.

        Returns:
            The persisted role version and local installation.

        Raises:
            RoleAssetError: Invalid fields, inaccessible role or stale version.
        """
        request = self._validate_request(payload)
        version = 1
        if role_id is not None:
            current = await self.get_role(username, role_id)
            if request.expected_version != current["version"]:
                raise RoleAssetError(
                    "Role was edited elsewhere; reload first", status=409
                )
            version = current["version"] + 1
        else:
            role_id = uuid.uuid4().hex
        async with self.db.get_db() as session:
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
        """Validate field shapes and cross-field logical slot uniqueness.

        Args:
            payload: Complete role save or preview request.

        Returns:
            Validated definition and installation.

        Raises:
            RoleAssetError: A field is invalid or slots have conflicting kinds.
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
        slots = {request.definition.model_slot: "model_slot"}
        if request.definition.persona_slot:
            if request.definition.persona_slot in slots:
                raise RoleAssetError(
                    "Logical slots must have one resource kind",
                    [
                        {
                            "path": "definition.persona_slot",
                            "code": "CONFLICT",
                            "message": "Slot already used by model_slot",
                        }
                    ],
                )
            slots[request.definition.persona_slot] = "persona_slot"
        for field in ("tools", "skills", "knowledge_bases"):
            for index, slot in enumerate(getattr(request.definition, field) or []):
                if slot in slots:
                    raise RoleAssetError(
                        "Logical slots must be unique",
                        [
                            {
                                "path": f"definition.{field}.{index}",
                                "code": "CONFLICT",
                                "message": f"Slot already used by {slots[slot]}",
                            }
                        ],
                    )
                slots[slot] = field
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
            return {
                "role_id": role_id,
                "version": row.version,
                "definition": row.definition,
                "installation": installation.installation if installation else {},
                "versions": [r.version for r in rows],
            }
