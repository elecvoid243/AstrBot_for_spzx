"""Public role configuration behavior with direct resource identities."""

import pytest

from astrbot.core.agent_teams.assets import RoleAssetError, RoleAssetService
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_direct_resources_versions_and_overrides(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    catalog = {
        "profiles": [
            {
                "id": "default",
                "persona_id": "default-persona",
                "tools": ["shared"],
                "skills": ["shared"],
                "knowledge_bases": ["kb-id"],
            }
        ],
        "models": [{"id": "shared", "capabilities": ["tool_use"]}],
        "personas": [
            {"id": "default-persona", "prompt": "Wrong default"},
            {
                "id": "person",
                "prompt": "Persona text",
                "tools": ["shared"],
                "skills": ["shared"],
            },
        ],
        "tools": [{"id": "shared"}],
        "skills": [{"id": "shared"}],
        "knowledge_bases": [{"id": "kb-id", "name": "Displayed name"}],
    }

    async def load():
        return catalog

    service = RoleAssetService(db, catalog_loader=load)
    try:
        definition = {
            "name": "Role",
            "prompt": "Responsibility",
            "provider_id": "shared",
            "persona_id": "person",
            "tools": ["shared"],
            "skills": ["shared"],
            "knowledge_bases": ["kb-id"],
        }
        role = await service.save_role("alice", {"definition": definition})
        result = await service.resolve_role("alice", role["role_id"], 1)
        assert result["ready"], result["issues"]
        assert result["effective"]["prompt"] == "Responsibility"
        assert result["effective"]["persona_prompt"] == "Persona text"
        assert result["effective"]["knowledge_bases"] == ["kb-id"]
        assert result["sources"]["persona_prompt"] == "persona:person"
        cleared = await service.resolve_role(
            "alice",
            role["role_id"],
            1,
            {"persona_id": None, "tools": [], "skills": None},
        )
        assert cleared["effective"]["persona_id"] is None
        assert cleared["effective"]["persona_prompt"] is None
        assert cleared["effective"]["tools"] == []
        assert cleared["effective"]["skills"] == ["shared"]
        assert cleared["sources"]["tools"] == "task"
        cleared_model = await service.resolve_role(
            "alice", role["role_id"], 1, {"provider_id": None}
        )
        assert cleared_model["effective"]["provider_id"] is None
        assert any(
            i["path"] == "overrides.provider_id" for i in cleared_model["issues"]
        )
        changed = {
            **definition,
            "persona_id": None,
            "tools": None,
            "skills": [],
            "knowledge_bases": [],
        }
        second = await service.save_role(
            "alice", {"definition": changed, "expected_version": 1}, role["role_id"]
        )
        assert second["version"] == 2
        assert (await service.get_role("alice", role["role_id"], 1))[
            "definition"
        ] == role["definition"]
        preview = await service.resolve_role("alice", role["role_id"], 2)
        assert preview["effective"]["persona_id"] is None
        assert preview["effective"]["persona_prompt"] is None
        assert preview["effective"]["tools"] == ["shared"]
        assert preview["effective"]["skills"] == []
        with pytest.raises(RoleAssetError) as exc:
            await service.save_role(
                "alice", {"definition": changed, "expected_version": 1}, role["role_id"]
            )
        assert exc.value.status == 409
        assert (await service.list_roles("bob"))["roles"] == []
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_strict_fields_and_resource_diagnostics(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))

    async def load():
        return {
            "profiles": [{"id": "default", "provider_pool": ["allowed"]}],
            "models": [{"id": "denied"}],
            "personas": [],
            "tools": [{"id": "tool", "enabled_profiles": []}],
            "skills": [],
            "knowledge_bases": [{"id": "id", "name": "display"}],
        }

    service = RoleAssetService(db, catalog_loader=load)
    try:
        for payload, path in [
            (
                {"definition": {"name": "X", "model_slot": "model"}},
                "definition.model_slot",
            ),
            (
                {"definition": {"name": "X", "persona_slot": "persona"}},
                "definition.persona_slot",
            ),
            ({"definition": {"name": "X", "migration": {}}}, "definition.migration"),
            (
                {"definition": {"name": "X"}, "installation": {"bindings": {}}},
                "installation.bindings",
            ),
            (
                {"definition": {"name": "X", "tools": ["tool", "tool"]}},
                "definition.tools.1",
            ),
        ]:
            with pytest.raises(RoleAssetError) as exc:
                await service.preview_role(payload)
            assert path in [item["path"] for item in exc.value.fields]
        result = await service.preview_role(
            {
                "definition": {
                    "name": "X",
                    "prompt": "Duty",
                    "provider_id": "denied",
                    "tools": ["tool"],
                    "knowledge_bases": ["display"],
                }
            }
        )
        errors = {issue["path"]: issue["code"] for issue in result["issues"]}
        assert errors["definition.provider_id"] == "FORBIDDEN"
        assert errors["definition.tools.0"] == "FORBIDDEN"
        assert errors["definition.knowledge_bases.0"] == "MISSING"
        assert not result["ready"]
        assert any(
            i["path"] == "definition.provider_id"
            for i in (await service.preview_role({"definition": {"name": "X"}}))[
                "issues"
            ]
        )
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_persona_allowlist_and_frozen_resource_permissions(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    catalog = {
        "profiles": [{"id": "default"}],
        "models": [{"id": "m"}],
        "personas": [
            {"id": "p", "prompt": "Persona", "tools": ["allowed"], "skills": []}
        ],
        "tools": [
            {"id": "allowed"},
            {"id": "denied"},
            {"id": "builtin", "persona_restricted": False},
        ],
        "skills": [{"id": "skill"}],
        "knowledge_bases": [],
    }

    async def load():
        return catalog

    service = RoleAssetService(db, catalog_loader=load)
    try:
        invalid = await service.preview_role(
            {
                "definition": {
                    "name": "X",
                    "prompt": "Duty",
                    "provider_id": "m",
                    "persona_id": "p",
                    "tools": ["denied", "builtin"],
                    "skills": ["skill"],
                }
            }
        )
        assert {i["path"] for i in invalid["issues"]} == {
            "definition.tools.0",
            "definition.skills.0",
        }
        assert invalid["effective"]["tools"] == ["builtin"]
        role = await service.save_role(
            "alice",
            {
                "definition": {
                    "name": "X",
                    "prompt": "Duty",
                    "provider_id": "m",
                    "persona_id": "p",
                    "tools": ["allowed"],
                    "skills": [],
                }
            },
        )
        frozen = await service.resolve_role("alice", role["role_id"], 1)
        assert frozen["ready"]
        await service.validate_frozen_role(frozen)
        catalog["profiles"][0]["provider_pool"] = []
        with pytest.raises(RoleAssetError) as exc:
            await service.validate_frozen_role(frozen)
        assert any(i["path"] == "effective.provider_id" for i in exc.value.fields)
        with pytest.raises(RoleAssetError) as exc:
            await service.resolve_role(
                "alice", role["role_id"], 1, {"tools": ["allowed", "allowed"]}
            )
        assert exc.value.fields[0]["path"] == "overrides.tools.1"
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_old_stored_format_is_explicitly_rejected(tmp_path):
    from astrbot.core.db.po import AgentRoleInstallation, AgentRoleVersion

    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    service = RoleAssetService(db)
    try:
        async with db.get_db() as session:
            session.add(
                AgentRoleVersion(
                    role_id="legacy",
                    version=1,
                    owner_username="alice",
                    definition={"name": "Old", "model_slot": "model"},
                )
            )
            session.add(
                AgentRoleInstallation(
                    role_id="legacy",
                    version=1,
                    installation={"profile_id": "default", "bindings": {"model": "m"}},
                )
            )
            await session.commit()
        with pytest.raises(RoleAssetError) as exc:
            await service.get_role("alice", "legacy", 1)
        assert {i["path"] for i in exc.value.fields} == {
            "definition.model_slot",
            "installation.bindings",
        }
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_runner_and_file_tool_mode_diagnostics(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    catalog = {
        "profiles": [{"id": "default", "file_tool_mode": "patch"}],
        "models": [{"id": "m"}],
        "personas": [],
        "tools": [{"id": "astrbot_apply_patch"}, {"id": "astrbot_file_write_tool"}],
        "skills": [],
        "knowledge_bases": [],
    }

    async def load():
        return catalog

    service = RoleAssetService(db, catalog_loader=load)
    try:
        data = {"definition": {"name": "R", "provider_id": "m", "prompt": "Duty"}}
        assert (await service.preview_role(data))["effective"]["tools"] == [
            "astrbot_apply_patch"
        ]
        data["definition"]["tools"] = ["astrbot_file_write_tool"]
        result = await service.preview_role(data)
        assert any(
            issue["path"] == "definition.tools.0" and issue["code"] == "INCOMPATIBLE"
            for issue in result["issues"]
        )
        catalog["profiles"][0]["runner_type"] = "dify"
        catalog["profiles"][0]["runner_config"] = {}
        result = await service.preview_role(data)
        assert any(
            issue["path"] == "definition.provider_id"
            and issue["code"] == "INCOMPATIBLE"
            for issue in result["issues"]
        )
        assert any(
            issue["path"] == "definition.tools" and issue["code"] == "INCOMPATIBLE"
            for issue in result["issues"]
        )
        assert result["effective"]["provider_id"] is None
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_save_has_single_winner(tmp_path):
    import asyncio

    db = SQLiteDatabase(str(tmp_path / "roles.db"))
    service = RoleAssetService(db)
    try:
        role = await service.save_role("alice", {"definition": {"name": "R"}})

        async def revise(name):
            try:
                return await service.save_role(
                    "alice",
                    {"definition": {"name": name}, "expected_version": 1},
                    role["role_id"],
                )
            except RoleAssetError as exc:
                return exc

        results = await asyncio.gather(revise("A"), revise("B"))
        assert sum(isinstance(result, dict) for result in results) == 1
        assert [
            result.status for result in results if isinstance(result, RoleAssetError)
        ] == [409]
        assert (await service.get_role("alice", role["role_id"]))["version"] == 2
    finally:
        await db.engine.dispose()
