"""Preview and atomically migrate legacy definitions without their sessions."""

import copy
import hashlib
import json
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.agent_teams.graph import GraphDefinition, validate_graph
from astrbot.core.agent_teams.workflows import (
    AutonomousDefinition,
    WorkflowDefinition,
    WorkflowService,
)
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    AgentLegacyMigration,
    AgentRoleInstallation,
    AgentRoleVersion,
    AgentWorkflowVersion,
)
from astrbot.dashboard.services.agent_team_service import DEFAULT_TEAM_CONFIG


class LegacyMigrationRequest(BaseModel):
    """Confirm exactly the reviewed definition proposal, optionally pending."""

    model_config = ConfigDict(extra="forbid", strict=True)
    preview_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    save_pending: bool = False


class LegacyMigrationService:
    """Definition-only migration using existing roles, assets and launch checks."""

    def __init__(
        self, db: BaseDatabase, roles: RoleAssetService, workflows: WorkflowService
    ):
        """Connect the public definition services to shared persistence.

        Args:
            db: Existing application database.
            roles: Role configuration and local resource resolution.
            workflows: Versioned assets and independent run execution.
        """
        self.db = db
        self.roles = roles
        self.workflows = workflows

    async def preview(self, username: str, team_id: str) -> dict:
        """Show proposed definitions, effective configuration and pending items.

        Args:
            username: Authenticated owner of the legacy team.
            team_id: Legacy definition identity; never a running task identity.

        Returns:
            A read-only proposal and a token binding confirmation to its content.

        Raises:
            RoleAssetError: The team is inaccessible.
        """
        team = await self.db.get_agent_team(team_id)
        if team is None or team.owner_username != username:
            raise RoleAssetError("Legacy team not found", status=404)
        rows = await self.db.get_agent_team_workflows_by_team(team_id)
        legacy_config = {**DEFAULT_TEAM_CONFIG, **(team.config or {})}
        source = {
            "name": team.name,
            "coordinator": team.coordinator_member_id,
            "members": [
                {k: v for k, v in m.items() if k not in ("session_id", "umo")}
                for m in team.members
            ],
            "config": legacy_config,
            "workflows": [
                {
                    "id": row.workflow_id,
                    "name": row.name,
                    "graph": row.graph,
                    "layout": row.layout,
                }
                for row in rows
            ],
        }
        source_digest = hashlib.sha256(
            json.dumps(
                source, sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        catalog = await self.roles.get_resources()
        roles, assets, mapping = (
            [],
            [],
            {"members": {}, "workflows": {}, "node_roles": {}, "nodes": {}},
        )
        members = {m["member_id"]: m for m in team.members}
        for member_id, member in members.items():
            role = await self._convert_role(member, {}, catalog)
            role["source_id"] = member_id
            role["member_id"] = member_id
            role["role_id"] = uuid.uuid5(
                uuid.NAMESPACE_URL, f"legacy:{username}:{team_id}:member:{member_id}"
            ).hex
            roles.append(role)
            mapping["members"][member_id] = {"role_id": role["role_id"], "version": 1}
        coordinator = mapping["members"].get(team.coordinator_member_id, {})
        definition = {
            "kind": "autonomous",
            "name": team.name,
            "coordinator": {
                "role_id": coordinator.get("role_id", "missing"),
                "role_version": 1,
            },
            "allowed_roles": [
                {"role_id": value["role_id"], "role_version": 1}
                for key, value in mapping["members"].items()
                if key != team.coordinator_member_id
            ],
            "required_inputs": ["goal"],
            "max_members": max(2, len(members)),
            "max_parallel": legacy_config["max_parallel"],
        }
        asset = {
            "source_id": team_id,
            "kind": "team",
            "definition": definition,
            "asset_id": uuid.uuid5(
                uuid.NAMESPACE_URL, f"legacy:{username}:team:{team_id}"
            ).hex,
            "issues": [],
        }
        try:
            asset["definition"] = AutonomousDefinition.model_validate(
                definition
            ).model_dump(exclude_none=True)
        except ValueError as exc:
            asset["issues"].append(
                {"path": "team", "code": "INCOMPATIBLE", "message": str(exc)}
            )
        if not coordinator:
            asset["issues"].append(
                {
                    "path": "team.coordinator_member_id",
                    "code": "MISSING",
                    "message": "Choose an existing coordinator",
                }
            )
        for key in legacy_config:
            if key != "max_parallel":
                asset["issues"].append(
                    {
                        "path": f"team.config.{key}",
                        "code": "UNMAPPABLE",
                        "message": f"Review legacy {key}; the new coordinator does not use the legacy round protocol",
                    }
                )
        asset["legacy_definition"] = {
            "config": legacy_config,
            "coordinator_member_id": team.coordinator_member_id,
        }
        assets.append(asset)
        mapping["team"] = {"asset_id": asset["asset_id"], "version": 1}
        for row in rows:
            tasks, issues = [], []
            for key in ("failure_policy", "reply_timeout", "inject_max_length"):
                if legacy_config[key] != DEFAULT_TEAM_CONFIG[key]:
                    issues.append(
                        {
                            "path": f"team.config.{key}",
                            "code": "UNMAPPABLE",
                            "message": "Review this customized legacy execution policy in the new workflow",
                        }
                    )
            try:
                graph, positions, node_mapping = self._expand_graph(
                    row.graph,
                    row.layout,
                    {r.workflow_id: {**r.graph, "layout": r.layout} for r in rows},
                )
                mapping["nodes"].update(
                    {
                        f"{row.workflow_id}:{source}": target
                        for source, target in node_mapping.items()
                    }
                )
            except RoleAssetError as exc:
                graph, positions = row.graph, row.layout
                issues.extend(exc.fields)
            for key in sorted(
                set(graph)
                - {
                    "nodes",
                    "edges",
                    "loops",
                    "layout",
                    "required_inputs",
                    "max_parallel",
                    "max_depth",
                }
            ):
                issues.append(
                    {
                        "path": f"graph.{key}",
                        "code": "UNMAPPABLE",
                        "message": "Review this unsupported legacy graph setting",
                    }
                )
            for node in graph.get("nodes", []):
                for key in sorted(
                    set(node)
                    - {
                        "id",
                        "title",
                        "member_id",
                        "task",
                        "execution",
                        "position",
                        "inputs",
                        "output_contract",
                        "_input_aliases",
                        "_legacy_path",
                    }
                ):
                    issues.append(
                        {
                            "path": f"nodes.{node.get('id')}.{key}",
                            "code": "UNMAPPABLE",
                            "message": "Review this unsupported legacy node setting",
                        }
                    )
                member_id = node.get("member_id", "")
                member = members.get(member_id)
                if member is None:
                    issues.append(
                        {
                            "path": f"nodes.{node.get('id')}.member_id",
                            "code": "MISSING",
                            "message": "Legacy member is missing",
                        }
                    )
                    continue
                role = next(r for r in roles if r["source_id"] == member_id)
                if node.get("execution"):
                    candidate = await self._convert_role(
                        member, node["execution"], catalog
                    )
                    matched = next(
                        (
                            r
                            for r in roles
                            if r["member_id"] == member_id
                            and all(
                                r[k] == candidate[k]
                                for k in ("definition", "installation")
                            )
                        ),
                        None,
                    )
                    if matched is None:
                        candidate["source_id"] = f"{row.workflow_id}:{node['id']}"
                        candidate["member_id"] = member_id
                        config_digest = hashlib.sha256(
                            json.dumps(
                                {
                                    key: candidate[key]
                                    for key in ("definition", "installation")
                                },
                                sort_keys=True,
                            ).encode()
                        ).hexdigest()
                        candidate["role_id"] = uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"legacy:{username}:{team_id}:member:{member_id}:config:{config_digest}",
                        ).hex
                        roles.append(candidate)
                    role = matched or candidate
                mapping["node_roles"][f"{row.workflow_id}:{node['id']}"] = {
                    "role_id": role["role_id"],
                    "version": 1,
                }
                mapping["nodes"][f"{row.workflow_id}:{node['id']}"] = node["id"]
                inputs = copy.deepcopy(node.get("inputs", {}))
                task = str(node.get("task") or "")
                # Literal braces are escaped before translating legacy placeholders.
                task = task.replace("{", "{{").replace("}", "}}")
                reference_names = {}
                for reference in re.findall(
                    r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", str(node.get("task") or "")
                ):
                    name = (
                        reference
                        if reference.isidentifier()
                        else f"legacy_input_{len(reference_names)}"
                    )
                    reference_names[reference] = name
                    inputs[name] = (
                        {"input": "input"}
                        if reference == "input"
                        else {
                            "task_id": node.get("_input_aliases", {}).get(
                                reference, reference
                            ),
                            "field": "text",
                        }
                    )
                task = re.sub(
                    r"\{\{\{\{\s*([A-Za-z0-9_]+)\s*\}\}\}\}",
                    lambda match: "{" + reference_names[match[1]] + "}",
                    task,
                )
                # Legacy dependency edges carry predecessor results even without placeholders.
                for edge in graph.get("edges", []):
                    if edge.get("to") != node.get("id"):
                        continue
                    predecessor = edge.get("from")
                    if any(
                        ref.get("task_id") == predecessor for ref in inputs.values()
                    ):
                        continue
                    name = f"legacy_predecessor_{len(inputs)}"
                    while name in inputs:
                        name += "_"
                    inputs[name] = {"task_id": predecessor, "field": "text"}
                    task += "\n\n[Upstream result]\n{" + name + "}"
                tasks.append(
                    {
                        "id": node.get("id"),
                        "name": node.get("title")
                        or node.get("_legacy_path", str(node.get("id"))).rsplit("/", 1)[
                            -1
                        ],
                        "member": member_id,
                        "role_id": role["role_id"],
                        "role_version": 1,
                        "task": task,
                        "inputs": inputs,
                        **(
                            {"output_contract": node["output_contract"]}
                            if node.get("output_contract") is not None
                            else {}
                        ),
                        "position": positions.get(
                            node.get("id"), node.get("position", {})
                        ),
                    }
                )
            definition = {
                "name": row.name,
                "required_inputs": graph.get("required_inputs", ["input"]),
                "max_parallel": graph.get(
                    "max_parallel", legacy_config["max_parallel"]
                ),
                "max_depth": graph.get("max_depth", 3),
                "tasks": tasks,
                "edges": [
                    {
                        "source": e.get("from"),
                        "target": e.get("to"),
                        **({"condition": e["condition"]} if "condition" in e else {}),
                    }
                    for e in graph.get("edges", [])
                ],
                "loops": copy.deepcopy(graph.get("loops", [])),
            }
            for index, edge in enumerate(graph.get("edges", [])):
                if set(edge) - {"from", "to", "condition"}:
                    issues.append(
                        {
                            "path": f"edges.{index}",
                            "code": "UNMAPPABLE",
                            "message": "Review unsupported dependency semantics; the original edge is retained",
                        }
                    )
            try:
                validated = GraphDefinition.model_validate(definition)
                validate_graph(validated)
                for task in validated.tasks:
                    assert task.role_id is not None and task.role_version is not None
                    await self.workflows._validate_task_definition(
                        username,
                        WorkflowDefinition(
                            name=task.name,
                            role_id=task.role_id,
                            role_version=task.role_version,
                            task=task.task,
                            required_inputs=list(
                                dict.fromkeys(
                                    [*validated.required_inputs, *task.inputs]
                                )
                            ),
                            overrides=task.overrides,
                            output_contract=task.output_contract,
                        ),
                        package_roles={(role["role_id"], 1) for role in roles},
                    )
                definition = validated.model_dump(exclude_none=True)
            except ValueError as exc:
                issues.extend(
                    getattr(exc, "fields", [])
                    or [{"path": "graph", "code": "INCOMPATIBLE", "message": str(exc)}]
                )
            identity = uuid.uuid5(
                uuid.NAMESPACE_URL, f"legacy:{username}:workflow:{row.workflow_id}"
            ).hex
            assets.append(
                {
                    "source_id": row.workflow_id,
                    "kind": "workflow",
                    "asset_id": identity,
                    "definition": definition,
                    "issues": issues,
                    "legacy_definition": {"graph": row.graph, "layout": row.layout},
                }
            )
            mapping["workflows"][row.workflow_id] = {"asset_id": identity, "version": 1}
        proposal = {"roles": roles, "assets": assets, "mapping": mapping}
        token = hashlib.sha256(
            json.dumps(
                proposal, sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        runs = await self.db.get_agent_team_runs_by_team(team_id)
        async with self.db.get_db() as session:
            receipt = await session.get(AgentLegacyMigration, (username, team_id))
        return {
            **proposal,
            "preview_token": token,
            "source_digest": source_digest,
            "existing": receipt is not None,
            "active_runs": [
                {"run_id": run.run_id, "status": run.status}
                for run in runs
                if run.status not in ("completed", "stopped", "failed")
            ],
            "issues": [
                *[
                    {**issue, "path": f"roles.{role['source_id']}.{issue['path']}"}
                    for role in roles
                    for issue in role["issues"]
                ],
                *[
                    {**issue, "path": f"assets.{asset['source_id']}.{issue['path']}"}
                    for asset in assets
                    for issue in asset["issues"]
                ],
            ],
            "ready": not any(role["issues"] for role in roles)
            and not any(asset["issues"] for asset in assets),
        }

    def _expand_graph(
        self,
        graph: dict,
        layout: dict,
        workflows: dict,
        prefix: str = "",
        source_prefix: str = "",
        offset: tuple[float, float] = (0, 0),
        depth: int = 0,
    ) -> tuple[dict, dict, dict]:
        """Inline declarative single-result subflows while preserving every dependency.

        Args:
            graph: Legacy nodes, directed edges and optional bounded loops.
            layout: Legacy coordinates relative to the containing group.
            workflows: Owned saved definitions available to explicit workflow references.
            prefix: Namespace preventing child node collisions.
            source_prefix: Original nested path retained in the identity mapping.
            offset: Position of the containing group on the outer canvas.
            depth: Bounded definition traversal; no legacy execution is resumed.

        Returns:
            Flattened graph, absolute positions and old-path-to-task mapping.

        Raises:
            RoleAssetError: A subflow is ambiguous, missing, recursive or incompatible.
        """
        if depth > 10:
            raise RoleAssetError(
                "Legacy subflow nesting is not mappable",
                [
                    {
                        "path": "graph.subflow",
                        "code": "UNMAPPABLE",
                        "message": "Nested references are cyclic or exceed ten levels",
                    }
                ],
            )
        if depth and set(graph) - {"nodes", "edges", "loops", "layout"}:
            raise RoleAssetError(
                "Legacy subflow settings cannot be flattened",
                [
                    {
                        "path": f"nodes.{source_prefix}.subflow",
                        "code": "UNMAPPABLE",
                        "message": "Review nested input contracts and execution limits explicitly",
                    }
                ],
            )
        nodes, edges, positions, mapping, outputs, entries, bodies = (
            [],
            [],
            {},
            {},
            {},
            {},
            {},
        )
        loops = []
        for raw in graph.get("nodes", []):
            node = copy.deepcopy(raw)
            identity = str(node.get("id", ""))
            if not identity:
                raise RoleAssetError(
                    "Legacy node identity is missing",
                    [
                        {
                            "path": "graph.nodes.id",
                            "code": "MISSING",
                            "message": "Assign an explicit node identity",
                        }
                    ],
                )
            position = layout.get(identity, node.get("position", {}))
            x, y = offset[0] + position.get("x", 0), offset[1] + position.get("y", 0)
            child = node.get("subflow")
            if child is not None and node.get("workflow_id"):
                raise RoleAssetError(
                    "Legacy subflow reference is ambiguous",
                    [
                        {
                            "path": f"nodes.{identity}",
                            "code": "AMBIGUOUS",
                            "message": "Choose either the inline definition or the saved workflow reference",
                        }
                    ],
                )
            if node.get("workflow_id"):
                child = workflows.get(node["workflow_id"])
                if child is None:
                    raise RoleAssetError(
                        "Legacy subflow is missing",
                        [
                            {
                                "path": f"nodes.{identity}.workflow_id",
                                "code": "MISSING",
                                "message": "Restore the explicitly referenced owned workflow",
                            }
                        ],
                    )
            if child is not None:
                if not isinstance(child, dict) or set(node) - {
                    "id",
                    "title",
                    "subflow",
                    "workflow_id",
                    "position",
                }:
                    raise RoleAssetError(
                        "Legacy subflow has unsupported call semantics",
                        [
                            {
                                "path": f"nodes.{identity}",
                                "code": "UNMAPPABLE",
                                "message": "Only declarative subflows sharing root inputs can be inlined; review call overrides explicitly",
                            }
                        ],
                    )
                expanded, child_positions, child_mapping = self._expand_graph(
                    child,
                    child.get("layout", {}),
                    workflows,
                    prefix + identity + "-",
                    source_prefix + identity + "/",
                    (x, y),
                    depth + 1,
                )
                identifiers = [n["id"] for n in expanded["nodes"]]
                roots = [
                    i
                    for i in identifiers
                    if all(e.get("to") != i for e in expanded["edges"])
                ]
                terminals = [
                    i
                    for i in identifiers
                    if all(e.get("from") != i for e in expanded["edges"])
                ]
                if len(terminals) != 1 or not roots:
                    raise RoleAssetError(
                        "Legacy subflow result is ambiguous",
                        [
                            {
                                "path": f"nodes.{identity}.subflow",
                                "code": "UNMAPPABLE",
                                "message": "A mapped declarative subflow must have one terminal result and an acyclic entry",
                            }
                        ],
                    )
                for nested in expanded["nodes"]:
                    nested["title"] = (
                        f"{node.get('title') or identity} / {nested.get('title') or nested['_legacy_path'].rsplit('/', 1)[-1]}"
                    )
                nodes.extend(expanded["nodes"])
                edges.extend(expanded["edges"])
                loops.extend(expanded.get("loops", []))
                positions.update(child_positions)
                mapping.update(child_mapping)
                outputs[identity], entries[identity], bodies[identity] = (
                    terminals[0],
                    roots,
                    identifiers,
                )
            else:
                target = prefix + identity
                if not re.fullmatch(r"[a-zA-Z][\w-]*", target):
                    target = (
                        "task-"
                        + hashlib.sha256(
                            (source_prefix + identity).encode()
                        ).hexdigest()[:16]
                    )
                node["id"] = target
                node["_legacy_path"] = source_prefix + identity
                nodes.append(node)
                positions[target] = {"x": x, "y": y} if position else {}
                outputs[identity], entries[identity], bodies[identity] = (
                    target,
                    [target],
                    [target],
                )
            mapping[source_prefix + identity] = outputs[identity]
        for node in nodes:
            if "_input_aliases" not in node:
                node["_input_aliases"] = outputs
                for ref in node.get("inputs", {}).values():
                    if ref.get("task_id") in outputs:
                        ref["task_id"] = outputs[ref["task_id"]]
        for edge in graph.get("edges", []):
            for target in entries.get(str(edge.get("to")), [edge.get("to")]):
                edges.append(
                    {
                        **edge,
                        "from": outputs.get(str(edge.get("from")), edge.get("from")),
                        "to": target,
                    }
                )
        for original in graph.get("loops", []):
            loop = copy.deepcopy(original)
            loop["id"] = prefix + loop["id"]
            loop["body"] = [
                task
                for identity in loop["body"]
                for task in bodies.get(identity, [identity])
            ]
            loop["exit_task_id"] = outputs.get(
                loop["exit_task_id"], loop["exit_task_id"]
            )
            for feedback in loop.get("feedback", []):
                if len(entries.get(feedback["target"], [])) != 1:
                    raise RoleAssetError(
                        "Legacy feedback target is ambiguous",
                        [
                            {
                                "path": f"loops.{loop['id']}.feedback",
                                "code": "UNMAPPABLE",
                                "message": "Choose one explicit feedback target",
                            }
                        ],
                    )
                feedback["target"] = entries[feedback["target"]][0]
                feedback["source"] = outputs.get(feedback["source"], feedback["source"])
            loops.append(loop)
        return (
            {**graph, "nodes": nodes, "edges": edges, "loops": loops},
            positions,
            mapping,
        )

    async def _convert_role(self, member: dict, override: dict, catalog: dict) -> dict:
        """Translate and freeze inherited values without retaining session identity.

        Args:
            member: Legacy member definition, whose history is never copied.
            override: Legacy node execution settings taking precedence.
            catalog: Current machine inventory used for unambiguous references.

        Returns:
            Proposed role, binding, effective configuration and blocking issues.
        """
        config = {
            **member.get("runner_config", {}),
            "provider_id": member.get("provider_id"),
            "persona_id": member.get("persona_id"),
            **override,
        }
        profile_id = config.get("config_id") or "default"
        provider_id = config.get("provider_id")
        core = self.roles.core_lifecycle
        if not config.get("config_id") and core is not None and member.get("umo"):
            profile_id = core.astrbot_config_mgr.get_conf_info(member["umo"]).id
        if not provider_id and core is not None and member.get("umo"):
            from astrbot.core import sp
            from astrbot.core.provider.entities import ProviderType

            # A session selection precedes the profile's model in the legacy pipeline.
            provider_id = await sp.get_async(
                "umo",
                member["umo"],
                f"provider_perf_{ProviderType.CHAT_COMPLETION.value}",
                None,
            )
        if not provider_id and core is not None and config.get("config_id"):
            selected_config = core.astrbot_config_mgr.confs.get(profile_id, {})
            provider_id = (
                selected_config.get("agent_runner", {})
                .get("config", {})
                .get("model", {})
                .get("provider_id")
            )
        if not provider_id and core is not None and not config.get("config_id"):
            from astrbot.core.provider.entities import ProviderType

            provider = await core.provider_manager.get_using_provider_async(
                ProviderType.CHAT_COMPLETION, member.get("umo")
            )
            provider_id = provider.meta().id if provider else None
        definition = {"name": member["name"]}
        model = next((m for m in catalog["models"] if m["id"] == provider_id), {})
        definition["model_capabilities"] = sorted(
            set(model.get("capabilities", [])) & {"tool_use", "image", "audio"}
        )
        installation = {"profile_id": profile_id, "bindings": {}}
        if provider_id:
            installation["bindings"]["model"] = provider_id
        if config.get("persona_id"):
            definition["persona_slot"] = "persona"
            installation["bindings"]["persona"] = config["persona_id"]
        for key in ("max_steps", "tool_call_timeout", "context_length"):
            if config.get(key) is not None:
                definition[key] = config[key]
        for key, kind in (
            ("tools", "tools"),
            ("skills", "skills"),
            ("kb_names", "knowledge_bases"),
        ):
            if config.get(key) is None:
                continue
            definition[kind] = []
            for index, reference in enumerate(config[key]):
                slot = f"{kind}_{index}"
                matches = [
                    r
                    for r in catalog[kind]
                    if r["id"] == reference
                    or (kind == "knowledge_bases" and r.get("name") == reference)
                ]
                definition[kind].append(slot)
                installation["bindings"][slot] = (
                    matches[0]["id"] if len(matches) == 1 else reference
                )
        # The pinned persona snapshot supplies persona instructions separately.
        definition["prompt"] = member.get("system_prompt") or ""
        preview = await self.roles.preview_role(
            {"definition": definition, "installation": installation}
        )
        # Freeze the actual inherited lists and numeric settings into this role.
        for key in ("max_steps", "tool_call_timeout", "context_length"):
            if preview["effective"].get(key) is not None:
                definition[key] = preview["effective"][key]
        for kind in ("tools", "skills", "knowledge_bases"):
            if kind in definition:
                continue
            definition[kind] = []
            for index, resource in enumerate(preview["effective"][kind]):
                slot = f"{kind}_{index}"
                definition[kind].append(slot)
                if resource:
                    installation["bindings"][slot] = resource
        unsupported = [
            {
                "path": f"runner_config.{key}",
                "code": "UNMAPPABLE",
                "message": "Review this unsupported legacy execution setting",
            }
            for key in sorted(
                set(config)
                - {
                    "config_id",
                    "provider_id",
                    "persona_id",
                    "tools",
                    "skills",
                    "kb_names",
                    "max_steps",
                    "tool_call_timeout",
                    "context_length",
                }
            )
        ]
        for issue in preview["issues"]:
            if (
                issue["path"] == "installation.profile_id"
                or issue["path"] == "installation.bindings.persona"
            ):
                unsupported.append(
                    {
                        "path": issue["path"],
                        "code": "UNMAPPABLE",
                        "message": "The missing or ambiguous legacy profile/persona prevents freezing inherited configuration; restore it and review this role explicitly",
                    }
                )
        if unsupported:
            definition["migration"] = {
                "issues": unsupported,
                "source": {"execution": config},
            }
        frozen = await self.roles.preview_role(
            {"definition": definition, "installation": installation}
        )
        return {
            "definition": frozen["definition"],
            "installation": frozen["installation"],
            "effective": frozen["effective"],
            "sources": preview["sources"],
            "issues": frozen["issues"],
        }

    async def confirm(self, username: str, team_id: str, payload: dict) -> dict:
        """Commit reviewed definitions and their receipt in a single transaction.

        Args:
            username: Authenticated legacy owner.
            team_id: Source team identity.
            payload: Preview token and explicit permission to retain pending items.

        Returns:
            Durable old-to-new mapping and pending definitions; no run is started.

        Raises:
            RoleAssetError: Stale preview, pending items or an identity conflict.
        """
        try:
            request = LegacyMigrationRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError(
                "Invalid migration confirmation",
                [
                    {
                        "path": ".".join(map(str, issue["loc"])),
                        "code": issue["type"],
                        "message": issue["msg"],
                    }
                    for issue in exc.errors()
                ],
            ) from exc
        preview = await self.preview(username, team_id)
        async with self.db.get_db() as session:
            receipt = await session.get(AgentLegacyMigration, (username, team_id))
            if receipt is not None:
                if receipt.source_digest != preview["source_digest"]:
                    raise RoleAssetError(
                        "This legacy definition was already migrated; edit its versioned assets",
                        status=409,
                    )
                if request.preview_token not in {
                    receipt.preview_token,
                    preview["preview_token"],
                }:
                    raise RoleAssetError(
                        "Preview this legacy definition before confirmation", status=409
                    )
                return {**receipt.result, "existing": True}
            if request.preview_token != preview["preview_token"]:
                raise RoleAssetError(
                    "Legacy definitions changed; preview again", status=409
                )
            if not preview["ready"] and not request.save_pending:
                raise RoleAssetError(
                    "Review pending migration items before saving", preview["issues"]
                )
            for role in preview["roles"]:
                session.add(
                    AgentRoleVersion(
                        role_id=role["role_id"],
                        version=1,
                        owner_username=username,
                        definition=role["definition"],
                    )
                )
                session.add(
                    AgentRoleInstallation(
                        role_id=role["role_id"],
                        version=1,
                        installation=role["installation"],
                    )
                )
            pending = []
            for asset in preview["assets"]:
                definition = copy.deepcopy(asset["definition"])
                if asset["issues"]:
                    pending.append(asset)
                    definition["migration"] = {
                        "issues": asset["issues"],
                        "source": asset["legacy_definition"],
                    }
                session.add(
                    AgentWorkflowVersion(
                        asset_id=asset["asset_id"],
                        version=1,
                        owner_username=username,
                        definition=definition,
                    )
                )
            result = {
                "mapping": preview["mapping"],
                "pending": pending,
                "issues": preview["issues"],
                "ready": preview["ready"],
            }
            session.add(
                AgentLegacyMigration(
                    owner_username=username,
                    team_id=team_id,
                    preview_token=preview["preview_token"],
                    source_digest=preview["source_digest"],
                    result=result,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                receipt = await session.get(AgentLegacyMigration, (username, team_id))
                if (
                    receipt is not None
                    and receipt.preview_token == preview["preview_token"]
                ):
                    return {**receipt.result, "existing": True}
                raise RoleAssetError(
                    "Migration conflicted; preview again", status=409
                ) from exc
            except BaseException:
                await session.rollback()
                raise
        return {**result, "existing": False}
