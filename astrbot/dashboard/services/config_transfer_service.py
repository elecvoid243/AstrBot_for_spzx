from __future__ import annotations

import copy
import io
import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.core import logger
from astrbot.core.config.agent_runner import normalize_agent_runner
from astrbot.core.config.default import CONFIG_METADATA_2, DEFAULT_CONFIG, VERSION
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.astrbot_path import (
    get_astrbot_config_path,
    get_astrbot_data_path,
)
from astrbot.dashboard.services.config_service import save_config, validate_config

PACKAGE_FORMAT_VERSION = 1
PACKAGE_KIND = "astrbot-config-profiles"
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 100
MAX_PROFILE_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MANIFEST_NAME = "manifest.json"
DEFAULT_CONFIG_NAME = "cmd_config.json"

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
PROFILE_FILE_PATTERN = re.compile(r"^abconf_[0-9a-f-]{36}\.json$", re.IGNORECASE)

# The default configuration keeps instance identity and credentials local.
DEFAULT_PROTECTED_KEYS = (
    "dashboard",
    "admins_id",
    "provider",
    "provider_sources",
    "platform",
)


class ConfigTransferError(ValueError):
    """Raised when a configuration package cannot be parsed or applied."""


def _fill_missing(target: dict, defaults: dict) -> None:
    """Fill keys absent from ``target`` with deep copies of ``defaults``."""
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _fill_missing(target[key], value)


def _unique_import_name(base: str, used_names: set[str]) -> str:
    """Return a display name that does not collide with ``used_names``."""
    base = (base or "").strip() or "未命名"
    candidate = f"{base} (导入)"
    if candidate not in used_names:
        return candidate
    index = 2
    while f"{base} (导入 {index})" in used_names:
        index += 1
    return f"{base} (导入 {index})"


def _normalize_config(config: dict) -> dict:
    """Return a deep copy ready for validation or persistence."""
    normalized = copy.deepcopy(config)
    normalized.pop("dashboard", None)
    normalized["agent_runner"] = normalize_agent_runner(normalized.get("agent_runner"))
    _fill_missing(normalized, DEFAULT_CONFIG)
    return normalized


class ConfigTransferService:
    """Export and import configuration profiles as portable packages."""

    def __init__(
        self,
        core_lifecycle: AstrBotCoreLifecycle,
        *,
        runtime: dict | None = None,
    ) -> None:
        self.core_lifecycle = core_lifecycle
        self.acm = core_lifecycle.astrbot_config_mgr
        self.runtime = runtime or {}

    def export_profiles(
        self,
        ids: list[str],
        include_default: bool,
    ) -> tuple[bytes, str, str]:
        """Serialize selected profiles into a JSON file or a ZIP package.

        Args:
            ids: Profile IDs to export. Empty means every non-default profile.
            include_default: Whether ``cmd_config.json`` joins the package.

        Returns:
            ``(content, media_type, filename)`` for the HTTP response.

        Raises:
            ConfigTransferError: If no profile can be exported or an ID is unknown.
        """
        infos = {info["id"]: info for info in self.acm.get_conf_list()}
        if ids:
            unknown = [conf_id for conf_id in ids if conf_id not in infos]
            if unknown:
                raise ConfigTransferError(f"配置文件不存在: {', '.join(unknown)}")
            include_default = include_default or "default" in ids
            selected = [conf_id for conf_id in ids if conf_id != "default"]
        else:
            selected = [conf_id for conf_id in infos if conf_id != "default"]

        if not selected and not include_default:
            raise ConfigTransferError("没有可导出的配置文件。")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if len(selected) == 1 and not include_default:
            conf_id = selected[0]
            payload = {
                "format_version": PACKAGE_FORMAT_VERSION,
                "kind": PACKAGE_KIND,
                "name": infos[conf_id].get("name") or conf_id[:8],
                "id": conf_id,
                "config": self._export_config(conf_id),
            }
            return (
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
                f"astrbot-config-{conf_id[:8]}.json",
            )

        manifest: dict[str, Any] = {
            "format_version": PACKAGE_FORMAT_VERSION,
            "kind": PACKAGE_KIND,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_version": VERSION,
            "config_version": DEFAULT_CONFIG.get("config_version"),
            "includes_default": bool(include_default),
            "profiles": [],
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for conf_id in selected:
                info = infos[conf_id]
                file_name = str(info.get("path") or f"abconf_{conf_id}.json")
                if not PROFILE_FILE_PATTERN.match(file_name):
                    file_name = f"abconf_{conf_id}.json"
                archive.writestr(
                    f"profiles/{file_name}",
                    json.dumps(
                        self._export_config(conf_id),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                manifest["profiles"].append(
                    {
                        "id": conf_id,
                        "name": info.get("name") or conf_id[:8],
                        "file": f"profiles/{file_name}",
                    }
                )
            if include_default:
                archive.writestr(
                    DEFAULT_CONFIG_NAME,
                    json.dumps(
                        self._export_config("default"),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        return (
            buffer.getvalue(),
            "application/zip",
            f"astrbot-configs-{timestamp}.zip",
        )

    def _export_config(self, conf_id: str) -> dict:
        config = copy.deepcopy(dict(self.acm.confs[conf_id]))
        config.pop("dashboard", None)
        return config

    def preview_import(
        self,
        raw: bytes,
        filename: str,
        *,
        conflict_mode: str,
        apply_default: bool,
    ) -> dict:
        """Parse a package and describe what an import would change.

        Args:
            raw: Uploaded package bytes.
            filename: Original upload filename, used for JSON single profiles.
            conflict_mode: ``overwrite`` or ``rename``.
            apply_default: Whether the caller wants to apply ``cmd_config.json``.

        Returns:
            A plan with per-profile actions and a summary.

        Raises:
            ConfigTransferError: If the package is invalid.
        """
        entries, default_config, meta = self._parse_package(raw, filename)
        plan = self._build_plan(entries, conflict_mode)
        default_plan = self._prepare_default(default_config, apply_default)
        return {
            "package": meta,
            "conflict_mode": conflict_mode,
            "items": [self._public_item(item) for item in plan],
            "default": self._public_default(default_plan),
            "summary": self._summarize(plan, default_plan),
        }

    async def import_package(
        self,
        raw: bytes,
        filename: str,
        *,
        conflict_mode: str,
        apply_default: bool,
        allow_admin_id_change: bool,
    ) -> dict:
        """Apply a configuration package and return the per-profile result.

        Args:
            raw: Uploaded package bytes.
            filename: Original upload filename, used for JSON single profiles.
            conflict_mode: ``overwrite`` or ``rename``.
            apply_default: Whether to replace ``cmd_config.json``.
            allow_admin_id_change: Whether the caller may change ``admins_id``.

        Returns:
            Applied plan, backup path, and summary.

        Raises:
            ConfigTransferError: If the package is invalid.
        """
        entries, default_config, meta = self._parse_package(raw, filename)
        plan = self._build_plan(entries, conflict_mode)
        default_plan = self._prepare_default(default_config, apply_default)
        backup_path = self._backup(plan, default_plan)
        changed = await self._apply_plan(
            plan,
            default_plan,
            allow_admin_id_change=allow_admin_id_change,
        )
        return {
            "package": meta,
            "conflict_mode": conflict_mode,
            "items": [self._public_item(item) for item in plan],
            "default": self._public_default(default_plan),
            "summary": self._summarize(plan, default_plan),
            "backup": backup_path,
            "changed": changed,
        }

    def _parse_package(
        self,
        raw: bytes,
        filename: str,
    ) -> tuple[list[dict], dict | None, dict]:
        if not raw:
            raise ConfigTransferError("上传的文件为空。")
        if len(raw) > MAX_PACKAGE_BYTES:
            raise ConfigTransferError("配置文件包不能超过 20 MB。")

        if zipfile.is_zipfile(io.BytesIO(raw)):
            return self._parse_zip(raw)
        return self._parse_json(raw, filename)

    def _parse_json(
        self,
        raw: bytes,
        filename: str,
    ) -> tuple[list[dict], dict | None, dict]:
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigTransferError("文件不是合法的 JSON 或 ZIP。") from exc
        if not isinstance(payload, dict):
            raise ConfigTransferError("配置文件必须是 JSON 对象。")

        stem = Path(filename or "config.json").stem
        if isinstance(payload.get("config"), dict):
            config = payload["config"]
            name = str(payload.get("name") or stem).strip() or stem
            conf_id = str(payload.get("id") or "").strip()
            source_id = conf_id if UUID_PATTERN.match(conf_id) else None
        else:
            config = payload
            name = stem
            source_id = None

        entry = {"id": source_id, "name": name, "config": config}
        meta = {
            "format_version": PACKAGE_FORMAT_VERSION,
            "kind": PACKAGE_KIND,
            "source_version": None,
            "includes_default": False,
            "profile_count": 1,
        }
        return [entry], None, meta

    def _parse_zip(
        self,
        raw: bytes,
    ) -> tuple[list[dict], dict | None, dict]:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_PACKAGE_ENTRIES:
                    raise ConfigTransferError(
                        f"配置文件包最多包含 {MAX_PACKAGE_ENTRIES} 个条目。"
                    )
                total = 0
                names: set[str] = set()
                for info in infos:
                    if info.is_dir():
                        continue
                    self._assert_safe_member(info.filename)
                    total += info.file_size
                    if info.file_size > MAX_PROFILE_BYTES:
                        raise ConfigTransferError(f"包内文件过大: {info.filename}")
                    if total > MAX_UNCOMPRESSED_BYTES:
                        raise ConfigTransferError("配置文件包解压后过大。")
                    names.add(info.filename)

                if MANIFEST_NAME not in names:
                    raise ConfigTransferError("配置文件包缺少 manifest.json。")
                try:
                    manifest = json.loads(archive.read(MANIFEST_NAME))
                except json.JSONDecodeError as exc:
                    raise ConfigTransferError("manifest.json 不是合法 JSON。") from exc
                if not isinstance(manifest, dict):
                    raise ConfigTransferError("manifest.json 结构不正确。")
                if manifest.get("kind") not in (None, PACKAGE_KIND):
                    raise ConfigTransferError("这不是 AstrBot 配置文件包。")

                profiles = manifest.get("profiles")
                if profiles is None:
                    profiles = []
                if not isinstance(profiles, list):
                    raise ConfigTransferError("manifest.json 的方案列表无效。")
                if not profiles and DEFAULT_CONFIG_NAME not in names:
                    raise ConfigTransferError("manifest.json 中没有配置方案。")
                if len(profiles) > MAX_PACKAGE_ENTRIES:
                    raise ConfigTransferError(
                        f"配置文件包最多包含 {MAX_PACKAGE_ENTRIES} 个方案。"
                    )

                entries: list[dict] = []
                for item in profiles:
                    if not isinstance(item, dict):
                        raise ConfigTransferError("manifest.json 的方案条目无效。")
                    file_name = str(item.get("file") or "")
                    if file_name not in names:
                        raise ConfigTransferError(
                            f"manifest.json 引用了不存在的文件: {file_name}"
                        )
                    try:
                        config = json.loads(archive.read(file_name))
                    except json.JSONDecodeError as exc:
                        raise ConfigTransferError(
                            f"{file_name} 不是合法 JSON。"
                        ) from exc
                    if not isinstance(config, dict):
                        raise ConfigTransferError(f"{file_name} 必须是 JSON 对象。")
                    conf_id = str(item.get("id") or "").strip()
                    entries.append(
                        {
                            "id": conf_id if UUID_PATTERN.match(conf_id) else None,
                            "name": str(item.get("name") or "").strip()
                            or Path(file_name).stem,
                            "config": config,
                        }
                    )

                default_config = None
                if DEFAULT_CONFIG_NAME in names:
                    try:
                        default_config = json.loads(archive.read(DEFAULT_CONFIG_NAME))
                    except json.JSONDecodeError as exc:
                        raise ConfigTransferError(
                            "cmd_config.json 不是合法 JSON。"
                        ) from exc
                    if not isinstance(default_config, dict):
                        raise ConfigTransferError("cmd_config.json 必须是 JSON 对象。")
        except zipfile.BadZipFile as exc:
            raise ConfigTransferError("配置文件包不是合法的 ZIP。") from exc

        meta = {
            "format_version": manifest.get("format_version", PACKAGE_FORMAT_VERSION),
            "kind": PACKAGE_KIND,
            "source_version": manifest.get("source_version"),
            "config_version": manifest.get("config_version"),
            "includes_default": default_config is not None,
            "profile_count": len(entries),
        }
        return entries, default_config, meta

    @staticmethod
    def _assert_safe_member(name: str) -> None:
        normalized = name.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if (
            not parts
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {".", ".."} for part in parts)
        ):
            raise ConfigTransferError(f"配置文件包包含非法路径: {name}")
        if len(parts) == 1 and parts[0] in {MANIFEST_NAME, DEFAULT_CONFIG_NAME}:
            return
        if (
            len(parts) == 2
            and parts[0] == "profiles"
            and PROFILE_FILE_PATTERN.match(parts[1])
        ):
            return
        raise ConfigTransferError(f"配置文件包包含未知条目: {name}")

    def _build_plan(self, entries: list[dict], conflict_mode: str) -> list[dict]:
        if conflict_mode not in {"overwrite", "rename"}:
            raise ConfigTransferError("不支持的冲突处理方式。")

        infos = self.acm.get_conf_list()
        by_id = {info["id"]: info for info in infos if info["id"] != "default"}
        by_name: dict[str, list[dict]] = {}
        used_names = {str(info.get("name") or "") for info in infos}
        for info in infos:
            if info["id"] == "default":
                continue
            by_name.setdefault(str(info.get("name") or ""), []).append(info)

        plan: list[dict] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for entry in entries:
            source_id = entry.get("id")
            source_name = entry.get("name") or "未命名"
            item = {
                "source_id": source_id,
                "source_name": source_name,
                "target_id": None,
                "target_name": source_name,
                "action": "create",
                "conflict": None,
                "error": None,
                "status": "pending",
                "config": None,
            }

            if source_id and source_id in seen_ids:
                item.update(
                    action="error",
                    error="配置文件包内存在重复的方案 ID。",
                )
            elif source_name in seen_names:
                item.update(
                    action="error",
                    error="配置文件包内存在重复的方案名。",
                )
            else:
                seen_ids.add(source_id or "")
                seen_names.add(source_name)
                if source_id and source_id in by_id:
                    item.update(
                        action="overwrite",
                        target_id=source_id,
                        target_name=str(by_id[source_id].get("name") or source_name),
                        conflict="uuid",
                    )
                elif source_name in by_name:
                    matches = by_name[source_name]
                    if len(matches) == 1:
                        item.update(
                            action="overwrite",
                            target_id=matches[0]["id"],
                            target_name=str(matches[0].get("name") or source_name),
                            conflict="name",
                        )
                    else:
                        item.update(
                            action="error",
                            conflict="duplicate_name",
                            error="本地存在多个同名方案，请先改名后再导入。",
                        )
                else:
                    item.update(
                        action="create",
                        target_id=source_id if source_id else None,
                    )

            if conflict_mode == "rename" and item["conflict"] in {
                "name",
                "duplicate_name",
            }:
                new_name = _unique_import_name(source_name, used_names)
                used_names.add(new_name)
                item.update(
                    action="create",
                    target_id=source_id if source_id else None,
                    target_name=new_name,
                    error=None,
                )
            elif item["action"] == "create":
                used_names.add(item["target_name"])

            if item["action"] != "error":
                config = _normalize_config(entry["config"])
                try:
                    errors, normalized = validate_config(
                        config,
                        CONFIG_METADATA_2,
                        True,
                        runtime=self.runtime,
                    )
                except Exception as exc:  # noqa: BLE001 - surface any validator fault
                    item.update(action="error", error=f"校验失败: {exc}")
                else:
                    if errors:
                        item.update(
                            action="error",
                            error="；".join(str(error) for error in errors[:5]),
                        )
                    else:
                        item["config"] = normalized

            plan.append(item)
        return plan

    def _prepare_default(
        self,
        default_config: dict | None,
        apply_default: bool,
    ) -> dict:
        item = {
            "included": default_config is not None,
            "requested": bool(apply_default),
            "action": "none",
            "protected_keys": list(DEFAULT_PROTECTED_KEYS),
            "error": None,
            "config": None,
        }
        if default_config is None:
            return item
        if not apply_default:
            item["action"] = "skipped"
            return item

        config = copy.deepcopy(default_config)
        config["agent_runner"] = normalize_agent_runner(config.get("agent_runner"))
        _fill_missing(config, DEFAULT_CONFIG)
        try:
            errors, normalized = validate_config(
                config,
                CONFIG_METADATA_2,
                True,
                runtime=self.runtime,
            )
        except Exception as exc:  # noqa: BLE001 - surface any validator fault
            item.update(action="error", error=f"校验失败: {exc}")
            return item
        if errors:
            item.update(
                action="error",
                error="；".join(str(error) for error in errors[:5]),
            )
            return item
        item.update(action="overwrite", config=normalized)
        return item

    async def _apply_plan(
        self,
        plan: list[dict],
        default_plan: dict,
        *,
        allow_admin_id_change: bool,
    ) -> list[str]:
        changed: list[str] = []
        for item in plan:
            if item["action"] not in {"overwrite", "create"} or item["error"]:
                continue
            config = copy.deepcopy(item["config"] or {})
            try:
                if item["action"] == "overwrite":
                    target = self.acm.confs.get(item["target_id"])
                    if target is None:
                        raise ConfigTransferError("目标配置文件不存在。")
                    if "dashboard" in target:
                        config["dashboard"] = copy.deepcopy(target.get("dashboard"))
                    if not allow_admin_id_change:
                        config["admins_id"] = copy.deepcopy(target.get("admins_id"))
                    save_config(
                        config,
                        target,
                        is_core=True,
                        runtime=self.runtime,
                    )
                    if (
                        item["source_name"]
                        and item["source_name"] != item["target_name"]
                    ):
                        await self.acm.update_conf_info(
                            item["target_id"],
                            name=item["source_name"],
                        )
                        item["target_name"] = item["source_name"]
                    changed.append(item["target_id"])
                else:
                    conf_id = await self.acm.create_conf(
                        config,
                        name=item["target_name"],
                        conf_id=item["target_id"],
                    )
                    item["target_id"] = conf_id
                    changed.append(conf_id)
                item["status"] = "applied"
            except Exception as exc:  # noqa: BLE001 - report per-profile failures
                item["status"] = "failed"
                item["error"] = str(exc)
                logger.error(
                    "Failed to import config profile %s: %s",
                    item["source_name"],
                    exc,
                )

        if default_plan["action"] == "overwrite" and default_plan["config"]:
            try:
                local = self.acm.confs["default"]
                merged = copy.deepcopy(default_plan["config"])
                for key in DEFAULT_PROTECTED_KEYS:
                    if key in local:
                        merged[key] = copy.deepcopy(local.get(key))
                save_config(
                    merged,
                    local,
                    is_core=True,
                    runtime=self.runtime,
                )
                default_plan["status"] = "applied"
                changed.append("default")
            except Exception as exc:  # noqa: BLE001 - keep profile results
                default_plan["status"] = "failed"
                default_plan["error"] = str(exc)
                logger.error("Failed to import default config: %s", exc)

        for conf_id in dict.fromkeys(changed):
            try:
                await self.core_lifecycle.reload_pipeline_scheduler(conf_id)
            except Exception as exc:  # noqa: BLE001 - reload is best effort
                logger.warning(
                    "Failed to reload scheduler for %s after import: %s",
                    conf_id,
                    exc,
                )
        return list(dict.fromkeys(changed))

    def _backup(self, plan: list[dict], default_plan: dict) -> str | None:
        targets = [
            item
            for item in plan
            if item["action"] == "overwrite" and item.get("target_id")
        ]
        include_default = default_plan["action"] == "overwrite"
        if not targets and not include_default:
            return None

        base = Path(get_astrbot_config_path()) / (
            f"abconf_backup_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        backup_root = base
        index = 2
        while backup_root.exists():
            backup_root = base.with_name(f"{base.name}-{index}")
            index += 1
        backup_root.mkdir(parents=True)

        mapping = {
            info["id"]: {"path": info.get("path"), "name": info.get("name")}
            for info in self.acm.get_conf_list()
            if info["id"] != "default"
        }
        (backup_root / "abconf_mapping.json").write_text(
            json.dumps({"val": mapping}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for item in targets:
            conf = self.acm.confs.get(item["target_id"])
            if conf is None:
                continue
            source = Path(conf.config_path)
            if source.is_file():
                shutil.copy2(source, backup_root / source.name)
        if include_default:
            cmd_config = Path(get_astrbot_data_path()) / "cmd_config.json"
            if cmd_config.is_file():
                shutil.copy2(cmd_config, backup_root / "cmd_config.json")
        return str(backup_root)

    @staticmethod
    def _public_item(item: dict) -> dict:
        return {key: value for key, value in item.items() if key != "config"}

    @staticmethod
    def _public_default(item: dict) -> dict:
        return {key: value for key, value in item.items() if key != "config"}

    def _summarize(self, plan: list[dict], default_plan: dict) -> dict:
        summary = {
            "overwrite": 0,
            "create": 0,
            "error": 0,
            "applied": 0,
            "failed": 0,
            "default": default_plan["action"],
        }
        for item in plan:
            if item["action"] in {"overwrite", "create"}:
                summary[item["action"]] += 1
            else:
                summary["error"] += 1
            if item["status"] == "applied":
                summary["applied"] += 1
            elif item["status"] == "failed":
                summary["failed"] += 1
        return summary
