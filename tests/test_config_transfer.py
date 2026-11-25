from __future__ import annotations

import copy
import io
import json
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.subagent_orchestrator import SubAgentOrchestrator
from astrbot.core.subagent_policy import SubAgentPolicy
from astrbot.dashboard.services import config_transfer_service as transfer_module
from astrbot.dashboard.services.config_transfer_service import (
    ConfigTransferError,
    ConfigTransferService,
)


class FakeConfig(dict):
    def __init__(self, config_path: Path, data: dict) -> None:
        super().__init__(copy.deepcopy(data))
        self.config_path = str(config_path)

    def save_config(self, replace_config=None, *, indent: int = 2) -> None:
        if replace_config:
            self.update(replace_config)
        Path(self.config_path).write_text(
            json.dumps(dict(self), ensure_ascii=False),
            encoding="utf-8",
        )


class FakeACM:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.confs: dict[str, FakeConfig] = {}
        self.mapping: dict[str, dict] = {}
        self.confs["default"] = FakeConfig(
            config_dir / "cmd_config.json",
            DEFAULT_CONFIG,
        )

    def get_conf_list(self) -> list[dict]:
        return [
            {"id": conf_id, "name": meta["name"], "path": meta["path"]}
            for conf_id, meta in self.mapping.items()
        ] + [{"id": "default", "name": "default", "path": "cmd_config.json"}]

    async def create_conf(
        self,
        config: dict,
        name: str | None = None,
        conf_id: str | None = None,
    ) -> str:
        conf_id = conf_id or str(uuid.uuid4())
        if conf_id in self.mapping:
            raise ValueError(f"Config profile {conf_id} already exists")
        path = self.config_dir / f"abconf_{conf_id}.json"
        conf = FakeConfig(path, config)
        conf.save_config()
        self.confs[conf_id] = conf
        self.mapping[conf_id] = {"path": path.name, "name": name or conf_id[:8]}
        return conf_id

    async def update_conf_info(self, conf_id: str, name: str | None = None) -> bool:
        if name is not None:
            self.mapping[conf_id]["name"] = name
        return True


class FakeLifecycle:
    def __init__(self, acm: FakeACM) -> None:
        self.astrbot_config_mgr = acm
        self.reloaded: list[str] = []
        self.subagent_orchestrator = AsyncMock()

    async def reload_pipeline_scheduler(self, conf_id: str) -> None:
        self.reloaded.append(conf_id)


@pytest.fixture
def transfer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(
        transfer_module,
        "get_astrbot_config_path",
        lambda: str(config_dir),
    )
    acm = FakeACM(config_dir)
    lifecycle = FakeLifecycle(acm)
    return ConfigTransferService(lifecycle, runtime={}), acm, lifecycle


async def _add_profile(
    acm: FakeACM,
    name: str,
    conf_id: str | None = None,
    **overrides,
) -> str:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(overrides)
    return await acm.create_conf(config, name=name, conf_id=conf_id)


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_export_single_profile_returns_wrapped_json(transfer) -> None:
    service, acm, _ = transfer
    conf_id = await _add_profile(
        acm,
        "软件开发",
        provider_settings={"default_provider_id": "local-vllm"},
    )

    content, media_type, filename = service.export_profiles([conf_id], False)
    payload = json.loads(content)

    assert media_type == "application/json"
    assert filename == f"astrbot-config-{conf_id[:8]}.json"
    assert payload["name"] == "软件开发"
    assert payload["id"] == conf_id
    assert "dashboard" not in payload["config"]
    assert payload["config"]["provider_settings"]["default_provider_id"] == (
        "local-vllm"
    )


@pytest.mark.asyncio
async def test_export_all_builds_manifest_and_strips_dashboard(transfer) -> None:
    service, acm, _ = transfer
    await _add_profile(acm, "软件开发")
    await _add_profile(acm, "硬件开发")

    content, media_type, _ = service.export_profiles([], True)

    assert media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        default_config = json.loads(archive.read("cmd_config.json"))

    assert manifest["includes_default"] is True
    assert len(manifest["profiles"]) == 2
    assert "dashboard" not in default_config
    assert all(item["file"] in names for item in manifest["profiles"])
    assert "cmd_config.json" in names


@pytest.mark.asyncio
async def test_export_default_only_produces_importable_package(transfer) -> None:
    service, acm, _ = transfer
    await _add_profile(acm, "软件开发")

    content, media_type, _ = service.export_profiles(["default"], False)

    assert media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["profiles"] == []
    assert manifest["includes_default"] is True
    assert "cmd_config.json" in names

    plan = service.preview_import(
        content,
        "default.zip",
        conflict_mode="overwrite",
        apply_default=True,
    )
    assert plan["items"] == []
    assert plan["default"]["action"] == "overwrite"


@pytest.mark.asyncio
@pytest.mark.parametrize("package_type", ["json", "zip", "zip_with_default"])
async def test_subagent_policy_round_trip_without_applying_default(
    transfer, tmp_path: Path, package_type: str
) -> None:
    service, acm, _ = transfer
    reviewer = {"id": "reviewer", "name": "reviewer", "system_prompt": "Review changes"}
    writer = {"id": "writer", "name": "writer", "system_prompt": "Draft text"}
    acm.confs["default"]["subagent_orchestrator"]["agents"] = [reviewer, writer]
    policy = {
        "main_enable": True,
        "agent_ids": [reviewer["id"]],
        "execution_timeout": 90,
        "dynamic_agents": {"enabled": True, "max_subagent_count": 2},
    }
    profile_id = await _add_profile(acm, "Development", subagent_policy=policy)
    ids = [profile_id]
    if package_type == "zip":
        ids.append(
            await _add_profile(acm, "Support", subagent_policy={"main_enable": False})
        )
    raw, _, filename = service.export_profiles(ids, package_type == "zip_with_default")
    if package_type == "json":
        assert json.loads(raw)["subagent_definitions"] == [reviewer]
    elif package_type == "zip":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            assert json.loads(archive.read("manifest.json"))[
                "subagent_definitions"
            ] == [reviewer]

    destination_dir = tmp_path / "destination"
    destination_dir.mkdir()
    destination = FakeACM(destination_dir)
    local = destination.confs["default"]
    local_reviewer = {**reviewer, "system_prompt": "Local instructions"}
    local["subagent_orchestrator"]["agents"] = [local_reviewer]
    local["subagent_policy"] = {"agent_ids": [reviewer["id"]]}
    local.save_config()
    lifecycle = FakeLifecycle(destination)
    orchestrator = SubAgentOrchestrator(
        None, SimpleNamespace(get_persona_v3_by_id=lambda _: None)
    )
    lifecycle.subagent_orchestrator = orchestrator
    orchestrator.reload_from_config = AsyncMock(wraps=orchestrator.reload_from_config)
    importer = ConfigTransferService(lifecycle)

    result = await importer.import_package(
        raw,
        filename,
        conflict_mode="overwrite",
        apply_default=False,
        allow_admin_id_change=True,
    )

    agents = local["subagent_orchestrator"]["agents"]
    assert len(agents) == 2
    imported = agents[1]
    assert agents[0] == local_reviewer
    assert imported["name"] == "reviewer_import"
    assert imported["id"] != reviewer["id"]
    assert imported["system_prompt"] == reviewer["system_prompt"]
    assert local["subagent_policy"] == {"agent_ids": [reviewer["id"]]}
    assert destination.confs[profile_id]["subagent_policy"] == {
        **policy,
        "agent_ids": [imported["id"]],
    }
    assert result["summary"]["applied"] == len(ids)
    assert (Path(result["backup"]) / "cmd_config.json").is_file()
    lifecycle.subagent_orchestrator.reload_from_config.assert_awaited_once_with(
        local["subagent_orchestrator"]
    )
    effective = SubAgentPolicy.from_profile(destination.confs[profile_id])
    assert [tool.name for tool in orchestrator.available_handoffs(effective, {})] == [
        "transfer_to_reviewer_import"
    ]

    await importer.import_package(
        raw,
        filename,
        conflict_mode="overwrite",
        apply_default=False,
        allow_admin_id_change=True,
    )
    assert local["subagent_orchestrator"]["agents"] == agents
    assert destination.confs[profile_id]["subagent_policy"]["agent_ids"] == [
        imported["id"]
    ]
    lifecycle.subagent_orchestrator.reload_from_config.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("apply_default", [True, False])
async def test_import_legacy_package_migrates_global_subagent_policy(
    transfer, apply_default: bool
) -> None:
    service, acm, _ = transfer
    local = acm.confs["default"]
    local["subagent_policy_migrated"] = True
    local_agent = {"id": "local", "name": "local"}
    local["subagent_orchestrator"]["agents"] = [local_agent]
    profile_id = str(uuid.uuid4())
    profile_file = f"profiles/abconf_{profile_id}.json"
    package = {
        "manifest.json": json.dumps(
            {
                "profiles": [
                    {"id": profile_id, "name": "Development", "file": profile_file}
                ]
            }
        ),
        profile_file: "{}",
        "cmd_config.json": json.dumps(
            {
                "subagent_orchestrator": {
                    "main_enable": True,
                    "dynamic_agents": {"enabled": True, "max_subagent_count": 3},
                    "agents": [
                        {"name": "reviewer"},
                        {"name": "disabled", "enabled": False},
                    ],
                },
            }
        ),
    }
    await service.import_package(
        _zip_bytes(package),
        "legacy.zip",
        conflict_mode="overwrite",
        apply_default=apply_default,
        allow_admin_id_change=True,
    )

    catalogue = local["subagent_orchestrator"]["agents"]
    assert catalogue[0] == local_agent
    assert [agent["name"] for agent in catalogue[1:]] == (
        ["reviewer", "disabled"] if apply_default else ["reviewer"]
    )
    expected_policy = {
        "main_enable": True,
        "dynamic_agents": {"enabled": True, "max_subagent_count": 3},
        "agent_ids": [catalogue[1]["id"]],
    }
    assert acm.confs[profile_id]["subagent_policy"] == expected_policy
    assert local["subagent_policy"] == (expected_policy if apply_default else None)
    assert local["subagent_policy_migrated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_null", [False, True])
async def test_import_profile_distinguishes_missing_policy_from_null(
    transfer, explicit_null: bool
) -> None:
    service, acm, _ = transfer
    policy = {"main_enable": True, "agent_ids": ["reviewer"]}
    conf_id = await _add_profile(acm, "Development", subagent_policy=policy)
    config = {"subagent_policy": None} if explicit_null else {}
    raw = json.dumps({"id": conf_id, "name": "Development", "config": config}).encode()
    result = await service.import_package(
        raw,
        "legacy.json",
        conflict_mode="overwrite",
        apply_default=False,
        allow_admin_id_change=True,
    )
    assert result["summary"]["applied"] == 1
    assert acm.confs[conf_id]["subagent_policy"] == (None if explicit_null else policy)


@pytest.mark.asyncio
async def test_catalogue_save_failure_does_not_publish_profiles(
    transfer, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, acm, lifecycle = transfer
    default = acm.confs["default"]
    previous = copy.deepcopy(default["subagent_orchestrator"])

    def fail_after_update(config: dict) -> None:
        default.update(config)
        raise OSError("Cannot write configuration")

    monkeypatch.setattr(default, "save_config", fail_after_update)
    raw = json.dumps(
        {
            "name": "Development",
            "config": {"subagent_policy": {"agent_ids": ["reviewer"]}},
            "subagent_definitions": [{"id": "reviewer", "name": "reviewer"}],
        }
    ).encode()

    with pytest.raises(ConfigTransferError, match="保存静态子代理定义失败"):
        await service.import_package(
            raw,
            "development.json",
            conflict_mode="overwrite",
            apply_default=False,
            allow_admin_id_change=True,
        )

    assert default["subagent_orchestrator"] == previous
    assert list(acm.confs) == ["default"]
    assert lifecycle.reloaded == []


@pytest.mark.asyncio
async def test_preview_matches_uuid_before_name(transfer) -> None:
    service, acm, _ = transfer
    conf_id = await _add_profile(acm, "旧名字")
    package = {
        "manifest.json": json.dumps(
            {
                "format_version": 1,
                "kind": "astrbot-config-profiles",
                "profiles": [
                    {
                        "id": conf_id,
                        "name": "新名字",
                        "file": f"profiles/abconf_{conf_id}.json",
                    }
                ],
            }
        ),
        f"profiles/abconf_{conf_id}.json": json.dumps(DEFAULT_CONFIG),
    }

    plan = service.preview_import(
        _zip_bytes(package),
        "package.zip",
        conflict_mode="overwrite",
        apply_default=False,
    )

    item = plan["items"][0]
    assert item["action"] == "overwrite"
    assert item["conflict"] == "uuid"
    assert item["target_id"] == conf_id
    assert item["target_name"] == "旧名字"
    assert "config" not in item
    assert "config" not in plan["default"]


@pytest.mark.asyncio
async def test_preview_matches_name_when_uuid_differs(transfer) -> None:
    service, acm, _ = transfer
    local_id = await _add_profile(acm, "软件开发")
    package_id = str(uuid.uuid4())
    package = {
        "manifest.json": json.dumps(
            {
                "format_version": 1,
                "kind": "astrbot-config-profiles",
                "profiles": [
                    {
                        "id": package_id,
                        "name": "软件开发",
                        "file": f"profiles/abconf_{package_id}.json",
                    }
                ],
            }
        ),
        f"profiles/abconf_{package_id}.json": json.dumps(DEFAULT_CONFIG),
    }

    plan = service.preview_import(
        _zip_bytes(package),
        "package.zip",
        conflict_mode="overwrite",
        apply_default=False,
    )

    item = plan["items"][0]
    assert item["action"] == "overwrite"
    assert item["conflict"] == "name"
    assert item["target_id"] == local_id


@pytest.mark.asyncio
async def test_rename_mode_creates_profile_with_suffix(transfer) -> None:
    service, acm, _ = transfer
    await _add_profile(acm, "软件开发")
    package_id = str(uuid.uuid4())
    package = {
        "manifest.json": json.dumps(
            {
                "kind": "astrbot-config-profiles",
                "profiles": [
                    {
                        "id": package_id,
                        "name": "软件开发",
                        "file": f"profiles/abconf_{package_id}.json",
                    }
                ],
            }
        ),
        f"profiles/abconf_{package_id}.json": json.dumps(DEFAULT_CONFIG),
    }

    plan = service.preview_import(
        _zip_bytes(package),
        "package.zip",
        conflict_mode="rename",
        apply_default=False,
    )

    item = plan["items"][0]
    assert item["action"] == "create"
    assert item["target_name"] == "软件开发 (导入)"


@pytest.mark.asyncio
async def test_duplicate_local_names_are_rejected_and_renameable(transfer) -> None:
    service, acm, _ = transfer
    await _add_profile(acm, "重复名")
    await _add_profile(acm, "重复名")
    package_id = str(uuid.uuid4())
    package = {
        "manifest.json": json.dumps(
            {
                "kind": "astrbot-config-profiles",
                "profiles": [
                    {
                        "id": package_id,
                        "name": "重复名",
                        "file": f"profiles/abconf_{package_id}.json",
                    }
                ],
            }
        ),
        f"profiles/abconf_{package_id}.json": json.dumps(DEFAULT_CONFIG),
    }

    rejected = service.preview_import(
        _zip_bytes(package),
        "package.zip",
        conflict_mode="overwrite",
        apply_default=False,
    )["items"][0]
    renamed = service.preview_import(
        _zip_bytes(package),
        "package.zip",
        conflict_mode="rename",
        apply_default=False,
    )["items"][0]

    assert rejected["action"] == "error"
    assert rejected["conflict"] == "duplicate_name"
    assert renamed["action"] == "create"
    assert renamed["target_name"] == "重复名 (导入)"


@pytest.mark.asyncio
async def test_import_overwrites_creates_and_backs_up(transfer) -> None:
    service, acm, lifecycle = transfer
    local_id = await _add_profile(
        acm,
        "软件开发",
        provider_settings={"default_provider_id": "before"},
        dashboard={"password": "local-secret"},
    )
    new_id = str(uuid.uuid4())
    package_config = copy.deepcopy(DEFAULT_CONFIG)
    package_config["provider_settings"]["default_provider_id"] = "after"
    package_config["dashboard"] = {"password": "package-secret"}
    package = {
        "manifest.json": json.dumps(
            {
                "kind": "astrbot-config-profiles",
                "profiles": [
                    {
                        "id": local_id,
                        "name": "软件开发",
                        "file": f"profiles/abconf_{local_id}.json",
                    },
                    {
                        "id": new_id,
                        "name": "硬件开发",
                        "file": f"profiles/abconf_{new_id}.json",
                    },
                ],
            }
        ),
        f"profiles/abconf_{local_id}.json": json.dumps(package_config),
        f"profiles/abconf_{new_id}.json": json.dumps(DEFAULT_CONFIG),
    }

    result = await service.import_package(
        _zip_bytes(package),
        "package.zip",
        conflict_mode="overwrite",
        apply_default=False,
        allow_admin_id_change=True,
    )

    assert result["summary"]["applied"] == 2
    assert acm.confs[local_id]["provider_settings"]["default_provider_id"] == ("after")
    assert acm.confs[local_id]["dashboard"] == {"password": "local-secret"}
    assert new_id in acm.confs
    assert set(lifecycle.reloaded) == {local_id, new_id}
    backup = Path(result["backup"])
    assert (backup / "abconf_mapping.json").is_file()
    assert (backup / f"abconf_{local_id}.json").is_file()


@pytest.mark.asyncio
async def test_import_default_preserves_protected_keys(transfer) -> None:
    service, acm, _ = transfer
    local_default = acm.confs["default"]
    local_default["dashboard"] = {"password": "local-secret"}
    local_default["admins_id"] = ["local-admin"]
    local_default["provider"] = [{"id": "local-provider"}]
    local_default["provider_sources"] = [{"id": "local-source"}]
    local_default["platform"] = [{"id": "local-platform"}]

    package_config = copy.deepcopy(DEFAULT_CONFIG)
    package_config["dashboard"] = {"password": "package-secret"}
    package_config["admins_id"] = ["package-admin"]
    package_config["provider"] = [{"id": "package-provider"}]
    package_config["provider_sources"] = [{"id": "package-source"}]
    package_config["platform"] = [{"id": "package-platform"}]
    package_config["log_level"] = "DEBUG"
    package = {
        "manifest.json": json.dumps(
            {
                "kind": "astrbot-config-profiles",
                "profiles": [],
            }
        ),
        "cmd_config.json": json.dumps(package_config),
    }

    await service.import_package(
        _zip_bytes(package),
        "package.zip",
        conflict_mode="overwrite",
        apply_default=True,
        allow_admin_id_change=True,
    )

    assert local_default["dashboard"] == {"password": "local-secret"}
    assert local_default["admins_id"] == ["local-admin"]
    assert local_default["provider"] == [{"id": "local-provider"}]
    assert local_default["provider_sources"] == [{"id": "local-source"}]
    assert local_default["platform"] == [{"id": "local-platform"}]
    assert local_default["log_level"] == "DEBUG"


def test_zip_rejects_path_traversal(transfer) -> None:
    service, _, _ = transfer
    package = {
        "manifest.json": json.dumps(
            {
                "kind": "astrbot-config-profiles",
                "profiles": [
                    {
                        "id": str(uuid.uuid4()),
                        "name": "evil",
                        "file": "profiles/../evil.json",
                    }
                ],
            }
        ),
        "profiles/../evil.json": "{}",
    }

    with pytest.raises(ConfigTransferError, match="非法路径"):
        service.preview_import(
            _zip_bytes(package),
            "package.zip",
            conflict_mode="overwrite",
            apply_default=False,
        )


def test_zip_requires_manifest(transfer) -> None:
    service, _, _ = transfer
    package = {
        "profiles/abconf_00000000-0000-0000-0000-000000000000.json": json.dumps(
            DEFAULT_CONFIG
        )
    }

    with pytest.raises(ConfigTransferError, match="manifest.json"):
        service.preview_import(
            _zip_bytes(package),
            "package.zip",
            conflict_mode="overwrite",
            apply_default=False,
        )


@pytest.mark.asyncio
async def test_acm_create_conf_accepts_explicit_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import astrbot.core.astrbot_config_mgr as acm_module
    from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
    from astrbot.core.config.astrbot_config import AstrBotConfig

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(
        acm_module,
        "get_astrbot_config_path",
        lambda: str(config_dir),
    )

    class _SP:
        def __init__(self) -> None:
            self.data: dict = {}

        async def global_get(self, key, default=None):
            return self.data.get(key, default)

        async def global_put(self, key, value) -> None:
            self.data[key] = value

    default_conf = AstrBotConfig(config_path=str(tmp_path / "cmd_config.json"))
    acm = AstrBotConfigManager(default_conf, None, _SP())
    await acm.initialize()

    conf_id = str(uuid.uuid4())
    created = await acm.create_conf(
        copy.deepcopy(DEFAULT_CONFIG),
        name="导入方案",
        conf_id=conf_id,
    )

    assert created == conf_id
    assert (config_dir / f"abconf_{conf_id}.json").is_file()
    assert acm.get_conf_list()[0] == {
        "id": conf_id,
        "name": "导入方案",
        "path": f"abconf_{conf_id}.json",
    }
    with pytest.raises(ValueError):
        await acm.create_conf(
            copy.deepcopy(DEFAULT_CONFIG),
            name="重复",
            conf_id=conf_id,
        )
