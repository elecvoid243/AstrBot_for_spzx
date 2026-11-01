from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import traceback
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import jwt

from astrbot.core import file_token_service, logger
from astrbot.core.backup.exporter import AstrBotExporter
from astrbot.core.backup.importer import AstrBotImporter
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.db import BaseDatabase
from astrbot.core.utils.astrbot_path import (
    get_astrbot_backups_path,
    get_astrbot_data_path,
)
from astrbot.core.utils.upload import UploadTooLargeError
from astrbot.dashboard.services.chunked_upload_service import (
    ChunkedUploadError,
    ChunkedUploadService,
)

CHUNK_SIZE = 1024 * 1024
# Hard caps against disk exhaustion: a backup is never legitimately larger
# than this, and the whole-file endpoint is only for small backups (large
# ones must use the chunked flow).
MAX_BACKUP_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
RESTORE_STOP_TIMEOUT = 15
MAX_DIRECT_UPLOAD_BYTES = 128 * 1024 * 1024


class BackupServiceError(Exception):
    pass


@dataclass
class BackupDownload:
    path: str
    filename: str


def secure_filename(filename: str) -> str:
    filename = filename.replace("\\", "/")
    filename = os.path.basename(filename)
    filename = filename.replace("..", "_")
    filename = re.sub(r"[^\w\-.]", "_", filename)
    filename = filename.strip(".")
    if not filename or filename.replace("_", "") == "":
        filename = "backup"
    return filename


def generate_unique_filename(original_filename: str) -> str:
    name, ext = os.path.splitext(original_filename)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{name}_{timestamp}{ext}"


class BackupService:
    def __init__(
        self,
        db: BaseDatabase,
        core_lifecycle: AstrBotCoreLifecycle,
    ) -> None:
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.config = core_lifecycle.astrbot_config
        self.backup_dir = get_astrbot_backups_path()
        self.data_dir = get_astrbot_data_path()
        self.chunks_dir = os.path.join(self.backup_dir, ".chunks")
        self.backup_tasks: dict[str, dict] = {}
        self.backup_progress: dict[str, dict] = {}
        self.chunked_uploads = ChunkedUploadService(self.chunks_dir)
        self.last_restore_task_id: str | None = None
        self.restore_phase = "ready"
        self.restore_requests: set[asyncio.Task] = set()
        self.restore_request_done = asyncio.Event()
        self.restore_request_done.set()
        self.restore_task: asyncio.Task | None = None
        self.restore_quiesced = False
        self.restore_mutated = False
        self.services = None
        receipt = Path(self.backup_dir) / ".restore-state.json"
        if receipt.is_file():
            try:
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                if not isinstance(saved["task_id"], str) or not isinstance(
                    saved["task"], dict
                ):
                    raise ValueError("Invalid restore receipt")
                if saved["task"].get("type") != "import" or saved["task"].get(
                    "status"
                ) not in {"pending", "processing", "completed", "failed"}:
                    raise ValueError("Invalid restore task")
                if not {"result", "error"}.issubset(saved["task"]):
                    raise ValueError("Incomplete restore task")
            except (OSError, ValueError, KeyError, TypeError):
                saved = {
                    "task_id": str(uuid.uuid4()),
                    "mutated": True,
                    "task": {
                        "type": "import",
                        "status": "failed",
                        "result": None,
                        "error": "Unreadable restore receipt. Sign in as administrator and retry with a valid backup. Restarting only restarts maintenance; it does not complete restoration.",
                    },
                }
                logger.error(
                    "Invalid full restore receipt; recovery operations remain available"
                )
            task_id = saved["task_id"]
            self.last_restore_task_id = task_id
            self.backup_tasks[task_id] = saved["task"]
            if saved["task"]["status"] != "completed" and (
                saved.get("mutated") or saved["task"]["status"] != "failed"
            ):
                self.backup_tasks[task_id].update(
                    status="failed",
                    error="Restore interrupted. Sign in as administrator and retry with a valid backup from Maintenance. Restarting only restarts maintenance; it does not complete restoration.",
                )
                self.restore_phase = "failed"
                self.restore_mutated = bool(saved.get("mutated"))
                self.db.restore_recovery_pending = True
                core_lifecycle.restore_recovery_pending = True
                core_lifecycle.restore_recovery_handler = self.quiesce_for_restore

    @staticmethod
    def _payload(data: object) -> dict[str, Any]:
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _validate_backup_filename(filename: str | None, *, missing: str) -> str:
        if not filename:
            raise BackupServiceError(missing)
        if ".." in filename or "/" in filename or "\\" in filename:
            raise BackupServiceError("无效的文件名")
        return filename

    def _init_task(self, task_id: str, task_type: str, status: str = "pending") -> None:
        self.backup_tasks[task_id] = {
            "type": task_type,
            "status": status,
            "result": None,
            "error": None,
        }
        self.backup_progress[task_id] = {
            "status": status,
            "stage": "waiting",
            "current": 0,
            "total": 100,
            "message": "",
        }

    def _set_task_result(
        self,
        task_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        if task_id in self.backup_tasks:
            self.backup_tasks[task_id]["status"] = status
            self.backup_tasks[task_id]["result"] = result
            self.backup_tasks[task_id]["error"] = error
        if task_id in self.backup_progress:
            self.backup_progress[task_id]["status"] = status

    def _update_progress(
        self,
        task_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        if task_id not in self.backup_progress:
            return
        progress = self.backup_progress[task_id]
        if status is not None:
            progress["status"] = status
        if stage is not None:
            progress["stage"] = stage
        if current is not None:
            progress["current"] = current
        if total is not None:
            progress["total"] = total
        if message is not None:
            progress["message"] = message

    def _make_progress_callback(self, task_id: str):
        async def _callback(
            stage: str,
            current: int,
            total: int,
            message: str = "",
        ) -> None:
            self._update_progress(
                task_id,
                status="processing",
                stage=stage,
                current=current,
                total=total,
                message=message,
            )

        return _callback

    def ensure_cleanup_task_started(self) -> None:
        self.chunked_uploads.ensure_cleanup_task_started()

    async def cleanup_upload_session(self, upload_id: str) -> None:
        await self.chunked_uploads.cleanup_session(upload_id)

    def get_backup_manifest(self, zip_path: str) -> dict | None:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if "manifest.json" in zf.namelist():
                    manifest_data = zf.read("manifest.json")
                    return json.loads(manifest_data.decode("utf-8"))
                return None
        except Exception as exc:
            logger.debug(f"读取备份 manifest 失败: {exc}")
        return None

    def list_backups(self, *, page: int, page_size: int) -> dict:
        self.ensure_cleanup_task_started()
        Path(self.backup_dir).mkdir(parents=True, exist_ok=True)

        backup_files = []
        for filename in os.listdir(self.backup_dir):
            if not filename.endswith(".zip") or filename.startswith("."):
                continue

            file_path = os.path.join(self.backup_dir, filename)
            if not os.path.isfile(file_path):
                continue

            manifest = self.get_backup_manifest(file_path)
            if manifest is None:
                logger.debug(f"跳过无效备份文件: {filename}")
                continue

            stat = os.stat(file_path)
            backup_files.append(
                {
                    "filename": filename,
                    "size": stat.st_size,
                    "created_at": stat.st_mtime,
                    "type": manifest.get("origin", "exported"),
                    "astrbot_version": manifest.get("astrbot_version", "未知"),
                    "exported_at": manifest.get("exported_at"),
                }
            )

        backup_files.sort(key=lambda x: x["created_at"], reverse=True)
        start = (page - 1) * page_size
        end = start + page_size

        return {
            "items": backup_files[start:end],
            "total": len(backup_files),
            "page": page,
            "page_size": page_size,
        }

    def export_backup(self) -> dict:
        task_id = str(uuid.uuid4())
        self._init_task(task_id, "export", "pending")
        asyncio.create_task(self.background_export_task(task_id))
        return {
            "task_id": task_id,
            "message": "export task created, processing in background",
        }

    async def background_export_task(self, task_id: str) -> None:
        try:
            self._update_progress(task_id, status="processing", message="正在初始化...")
            preferences = getattr(self.core_lifecycle.umop_config_router, "sp", None)
            if preferences is not None:
                await preferences.flush()
            kb_manager = getattr(self.core_lifecycle, "kb_manager", None)
            exporter = AstrBotExporter(
                main_db=self.db,
                kb_manager=kb_manager,
                config_path=os.path.join(self.data_dir, "cmd_config.json"),
            )
            zip_path = await exporter.export_all(
                output_dir=self.backup_dir,
                progress_callback=self._make_progress_callback(task_id),
            )
            self._set_task_result(
                task_id,
                "completed",
                result={
                    "filename": os.path.basename(zip_path),
                    "path": zip_path,
                    "size": os.path.getsize(zip_path),
                },
            )
        except Exception as exc:
            logger.error(f"后台导出任务 {task_id} 失败: {exc}")
            logger.error(traceback.format_exc())
            self._set_task_result(task_id, "failed", error=str(exc))

    async def upload_backup(self, file: Any | None) -> dict:
        if not file:
            raise BackupServiceError("缺少备份文件")
        if not file.filename or not file.filename.endswith(".zip"):
            raise BackupServiceError("请上传 ZIP 格式的备份文件")

        safe_filename = secure_filename(file.filename)
        unique_filename = generate_unique_filename(safe_filename)

        Path(self.backup_dir).mkdir(parents=True, exist_ok=True)
        zip_path = os.path.join(self.backup_dir, unique_filename)
        try:
            await file.save(zip_path, max_bytes=MAX_DIRECT_UPLOAD_BYTES)
        except UploadTooLargeError as exc:
            raise BackupServiceError(
                f"Backup file exceeds the size limit ({MAX_DIRECT_UPLOAD_BYTES // (1024**2)} MB); "
                "use chunked upload instead."
            ) from exc

        logger.info(
            f"上传的备份文件已保存: {unique_filename} (原始名称: {file.filename})"
        )
        return {
            "filename": unique_filename,
            "original_filename": file.filename,
            "size": os.path.getsize(zip_path),
        }

    def upload_init(self, data: object, *, owner: str = "") -> dict:
        payload = self._payload(data)
        filename = payload.get("filename")
        total_size = payload.get("total_size", 0)

        if not filename:
            raise BackupServiceError("缺少 filename 参数")
        if not filename.endswith(".zip"):
            raise BackupServiceError("请上传 ZIP 格式的备份文件")
        if total_size <= 0:
            raise BackupServiceError("无效的文件大小")
        if total_size > MAX_BACKUP_TOTAL_BYTES:
            raise BackupServiceError(
                f"Backup file exceeds the size limit ({MAX_BACKUP_TOTAL_BYTES // (1024**3)} GB). "
                "You can copy it into the backups folder of the data directory "
                "via FTP/SFTP and restore it from the backup list."
            )

        unique_filename = generate_unique_filename(secure_filename(filename))
        self.chunked_uploads.ensure_cleanup_task_started()
        try:
            session = self.chunked_uploads.init_session(
                owner=owner,
                purpose="backup",
                filename=unique_filename,
                original_filename=filename,
                total_size=total_size,
            )
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

        return {
            "upload_id": session.id,
            "chunk_size": session.chunk_size,
            "total_chunks": session.total_chunks,
            "filename": unique_filename,
        }

    async def upload_chunk(
        self,
        *,
        upload_id: str | None,
        chunk_index_str: str | None,
        chunk_file: Any | None,
        owner: str = "",
    ) -> dict:
        if not upload_id or chunk_index_str is None:
            raise BackupServiceError("缺少必要参数")

        try:
            chunk_index = int(chunk_index_str)
        except ValueError as exc:
            raise BackupServiceError("无效的分片索引") from exc

        if not chunk_file:
            raise BackupServiceError("缺少分片数据")

        try:
            return await self.chunked_uploads.save_chunk(
                upload_id, chunk_index, chunk_file, owner=owner
            )
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

    def mark_backup_as_uploaded(self, zip_path: str) -> None:
        try:
            manifest = {"origin": "uploaded", "uploaded_at": datetime.now().isoformat()}
            with zipfile.ZipFile(zip_path, "r") as zf:
                if "manifest.json" in zf.namelist():
                    manifest_data = zf.read("manifest.json")
                    manifest = json.loads(manifest_data.decode("utf-8"))
                    manifest["origin"] = "uploaded"
                    manifest["uploaded_at"] = datetime.now().isoformat()

            with zipfile.ZipFile(zip_path, "a") as zf:
                new_manifest = json.dumps(manifest, ensure_ascii=False, indent=2)
                zf.writestr("manifest.json", new_manifest)

            logger.debug(f"已标记备份为上传来源: {zip_path}")
        except Exception as exc:
            logger.warning(f"标记备份来源失败: {exc}")

    async def upload_complete(self, data: object, *, owner: str = "") -> dict:
        payload = self._payload(data)
        upload_id = payload.get("upload_id")

        if not upload_id:
            raise BackupServiceError("缺少 upload_id 参数")

        try:
            session = self.chunked_uploads.get_session(upload_id, owner=owner)
            Path(self.backup_dir).mkdir(parents=True, exist_ok=True)
            output_path = os.path.join(self.backup_dir, session.filename)
            file_size = await self.chunked_uploads.assemble(
                upload_id, output_path, owner=owner
            )
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

        self.mark_backup_as_uploaded(output_path)
        logger.info(
            f"分片上传完成: {session.filename}, size={file_size}, "
            f"chunks={session.total_chunks}"
        )

        return {
            "filename": session.filename,
            "original_filename": session.original_filename,
            "size": file_size,
        }

    async def upload_abort(
        self, data: object, *, owner: str = ""
    ) -> tuple[dict | None, str | None]:
        payload = self._payload(data)
        upload_id = payload.get("upload_id")
        if not upload_id:
            raise BackupServiceError("缺少 upload_id 参数")

        try:
            if await self.chunked_uploads.abort(upload_id, owner=owner):
                logger.info(f"Aborted chunked backup upload: {upload_id}")
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

        return None, "上传已取消"

    def upload_status(self, data: object, *, owner: str = "") -> dict:
        payload = self._payload(data)
        upload_id = payload.get("upload_id")
        if not upload_id:
            raise BackupServiceError("缺少 upload_id 参数")

        try:
            return self.chunked_uploads.session_status(upload_id, owner=owner)
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

    def check_backup(self, data: object) -> dict:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少 filename 参数",
        )
        zip_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(zip_path):
            raise BackupServiceError(f"备份文件不存在: {filename}")

        kb_manager = getattr(self.core_lifecycle, "kb_manager", None)
        importer = AstrBotImporter(
            main_db=self.db,
            kb_manager=kb_manager,
            config_path=os.path.join(self.data_dir, "cmd_config.json"),
        )
        return importer.pre_check(zip_path).to_dict()

    def import_backup(self, data: object) -> dict:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少 filename 参数",
        )
        confirmed = payload.get("confirmed", False)
        if not confirmed:
            raise BackupServiceError(
                "请先确认导入。导入将会清空并覆盖现有数据，此操作不可撤销。"
            )

        zip_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(zip_path):
            raise BackupServiceError(f"备份文件不存在: {filename}")

        if self.restore_task is not None and not self.restore_task.done():
            raise BackupServiceError("A full restore is already running")
        task_id = str(uuid.uuid4())
        self.last_restore_task_id = task_id
        self._init_task(task_id, "import", "pending")
        self.restore_previous_phase = self.restore_phase
        self.restore_phase = "pending"
        from astrbot.dashboard.restore import restore_response_done

        self.restore_request_done = restore_response_done.get() or asyncio.Event()
        if restore_response_done.get() is None:
            self.restore_request_done.set()
        self.restore_task = asyncio.create_task(
            self.background_import_task(task_id, zip_path)
        )

        return {
            "task_id": task_id,
            "message": "import task created, processing in background",
        }

    def save_restore_receipt(self, task_id: str) -> None:
        """Atomically retain the last restore outcome across login and restart.

        Args:
            task_id: Full restore operation whose non-credential state is saved.
        """
        receipt = Path(self.backup_dir) / ".restore-state.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                {
                    "task_id": task_id,
                    "task": self.backup_tasks[task_id],
                    "mutated": self.restore_mutated,
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(receipt)

    async def quiesce_for_restore(self) -> None:
        """Stop supported runtime writers and await their final cleanup before replacement.

        This operation belongs only to full restore. Normal credential revocation
        intentionally leaves already admitted turns running.

        Raises:
            BackupServiceError: Supported tasks did not finish within the stop deadline.
        """
        from astrbot.core.computer.computer_client import shutdown_local_booter
        from astrbot.core.platform.sources.webchat.webchat_queue_mgr import (
            orphan_run_registry,
            webchat_queue_mgr,
        )
        from astrbot.core.utils.active_event_registry import active_event_registry

        core = self.core_lifecycle
        # Close input producers first. These are the official lifecycle hooks,
        # not an attempt to control arbitrary third-party threads or processes.
        for manager_name, hook in (
            ("platform_manager", "terminate"),
            ("cron_manager", "shutdown"),
            ("workflow_service", "shutdown"),
        ):
            manager = getattr(core, manager_name, None)
            if manager is not None and callable(
                operation := getattr(manager, hook, None)
            ):
                if manager_name == "cron_manager":
                    await operation(wait_for_jobs=True)
                else:
                    await operation()
        tasks = set(getattr(core, "curr_tasks", []))
        bus = getattr(core, "event_bus", None)
        if bus is not None:
            tasks.update(bus._pending_tasks)
        for origin in set(active_event_registry._events) | set(
            active_event_registry._background_tasks
        ):
            tasks.update(active_event_registry._background_tasks.get(origin, {}))
            active_event_registry.stop_all(origin)
        tasks.update(self.restore_requests)
        if self.services is not None:
            tasks.update(
                run.task for run in self.services.chat.chat_runs.values() if run.task
            )
            for session in self.services.live_chat.sessions.values():
                tasks.update(session.chat_request_tasks.values())
                tasks.update(session.chat_subscription_tasks.values())
                if session.live_request_task:
                    tasks.add(session.live_request_task)
        # Remove queue listeners before they can dispatch another admitted turn.
        await webchat_queue_mgr.clear_listener()
        tasks.discard(asyncio.current_task())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=RESTORE_STOP_TIMEOUT)
            if pending:
                raise BackupServiceError(
                    "Runtime did not stop; no backup data was replaced. Retry or restart."
                )
            await asyncio.gather(*tasks, return_exceptions=True)
        plugin_manager = getattr(core, "plugin_manager", None)
        if plugin_manager is not None and callable(
            terminate := getattr(plugin_manager, "_terminate_plugin", None)
        ):
            for plugin in plugin_manager.context.get_all_stars():
                await terminate(plugin)
        await shutdown_local_booter()
        provider_manager = getattr(core, "provider_manager", None)
        if provider_manager is not None and callable(
            terminate := getattr(provider_manager, "terminate", None)
        ):
            await terminate()
        preferences = getattr(core.umop_config_router, "sp", None)
        if preferences is not None:
            await preferences.close()
        for conversation in list(webchat_queue_mgr.queues):
            webchat_queue_mgr.remove_queues(conversation)
        for run_id in list(orphan_run_registry._runs):
            orphan_run_registry.finish(run_id)
        webchat_queue_mgr.system_subscribers.clear()
        if self.services is not None:
            self.services.chat.chat_runs.clear()
            self.services.chat.chat_runs_by_session.clear()
            self.services.chat.running_convs.clear()
            self.services.live_chat.sessions.clear()
        self.restore_quiesced = True

    async def background_import_task(self, task_id: str, zip_path: str) -> None:
        """Replace data only after supported writers and response lifetimes finish.

        Args:
            task_id: Administrator-visible full restore receipt identifier.
            zip_path: Validated archive path in the instance's backup directory.
        """
        mutated = False
        try:
            # Deliver task_id before retiring the initiating login/request.
            await self.restore_request_done.wait()
            self._update_progress(
                task_id, status="processing", message="Preparing full restore"
            )
            self.backup_tasks[task_id]["status"] = "processing"
            importer = AstrBotImporter(
                main_db=self.db,
                kb_manager=getattr(self.core_lifecycle, "kb_manager", None),
                config_path=str(Path(self.data_dir) / "cmd_config.json"),
                runtime_config=self.config,
            )
            checked = importer.pre_check(zip_path)
            if not checked.can_import:
                raise BackupServiceError(checked.error or checked.confirm_message)
            # Validate required JSON and identity shape before stopping the runtime.
            with zipfile.ZipFile(zip_path) as archive:
                tables = json.loads(archive.read("databases/main_db.json"))
                if not isinstance(tables, dict):
                    raise BackupServiceError("Invalid main database payload")
                if "dashboard_accounts" in tables:
                    bootstrap = [
                        row
                        for row in tables["dashboard_accounts"]
                        if row.get("bootstrap_key") == "dashboard"
                    ]
                    if (
                        len(bootstrap) != 1
                        or bootstrap[0].get("role") != "admin"
                        or not bootstrap[0].get("enabled", True)
                    ):
                        raise BackupServiceError(
                            "Backup must contain one enabled bootstrap administrator"
                        )
            self.restore_phase = "stopping"
            self.save_restore_receipt(task_id)
            await asyncio.wait_for(
                self.quiesce_for_restore(), timeout=RESTORE_STOP_TIMEOUT + 5
            )
            self.restore_phase = "restoring"
            # Rotation precedes every replacement and is never sourced from an archive.
            new_key = secrets.token_hex(32)
            old_key = self.config["dashboard"]["jwt_secret"]
            self.config["dashboard"]["jwt_secret"] = new_key
            try:
                self.config.save_config()
            except BaseException:
                self.config["dashboard"]["jwt_secret"] = old_key
                raise
            if self.services is not None:
                self.services.restore_app.state.jwt_secret = new_key
            async with file_token_service.lock:
                file_token_service.staged_files.clear()
                file_token_service.account_owners.clear()
            mutated = True
            self.restore_mutated = True
            self.save_restore_receipt(task_id)
            result = await importer.import_all(
                zip_path=zip_path,
                mode="replace",
                jwt_secret=new_key,
                progress_callback=self._make_progress_callback(task_id),
            )
            # Both ordinary and data-only archives restore the bootstrap credentials
            # with their accounts. No admission is allowed while config is stale.
            restored_config = json.loads(
                Path(importer.config_path).read_text(encoding="utf-8-sig")
            )
            self.config.clear()
            self.config.update(restored_config)
            if result.success:
                self._set_task_result(task_id, "completed", result=result.to_dict())
                self.restore_phase = "restart_required"
            else:
                raise BackupServiceError("; ".join(result.errors))
        except (Exception, asyncio.CancelledError) as exc:
            if mutated:
                self.db.restore_recovery_pending = True
            logger.error("Full restore %s failed: %s", task_id, exc, exc_info=True)
            self._set_task_result(
                task_id, "failed", error=str(exc) or "Restore interrupted"
            )
            # Preflight never entered the stop boundary; a failed stop never clears DB.
            self.restore_phase = (
                "failed"
                if mutated or self.restore_phase != "pending"
                else getattr(self, "restore_previous_phase", "ready")
            )
        finally:
            self.save_restore_receipt(task_id)

    def get_progress(self, task_id: str | None) -> dict:
        if not task_id:
            raise BackupServiceError("缺少参数 task_id")
        if task_id not in self.backup_tasks:
            raise BackupServiceError("找不到该任务")

        task_info = self.backup_tasks[task_id]
        status = task_info["status"]
        response_data = {
            "task_id": task_id,
            "type": task_info["type"],
            "status": status,
            "restart_required": self.restore_phase in {"restart_required", "failed"},
        }

        if status == "processing" and task_id in self.backup_progress:
            response_data["progress"] = self.backup_progress[task_id]
        if status == "completed":
            response_data["result"] = task_info["result"]
        if status == "failed":
            response_data["error"] = task_info["error"]

        return response_data

    async def prepare_download(
        self,
        *,
        filename: str | None,
        token: str | None,
        jwt_secret: str | None,
    ) -> BackupDownload:
        """Authorize an administrator before returning a backup download.

        Args:
            filename: Backup archive name.
            token: Current dashboard session JWT.
            jwt_secret: Configured dashboard signing secret.

        Returns:
            The authorized backup archive path and name.

        Raises:
            BackupServiceError: Credentials or the requested archive are invalid.
        """
        if not filename:
            raise BackupServiceError("缺少参数 filename")
        if not token:
            raise BackupServiceError("缺少参数 token")
        if not jwt_secret:
            raise BackupServiceError("服务器配置错误")

        try:
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                options={
                    "require": ["exp"],
                    "verify_signature": True,
                    "verify_exp": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise BackupServiceError("Token 已过期，请刷新页面后重试") from exc
        except jwt.InvalidTokenError as exc:
            raise BackupServiceError("Token 无效") from exc

        from astrbot.dashboard.services.account_service import AccountService

        account = await AccountService(self.db, self.config).resolve(payload)
        if account is None or account.role != "admin" or payload.get("token_type"):
            raise BackupServiceError("Token 无效")
        filename = self._validate_backup_filename(filename, missing="缺少参数 filename")
        file_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(file_path):
            raise BackupServiceError("备份文件不存在")
        return BackupDownload(path=file_path, filename=filename)

    def delete_backup(self, data: object) -> tuple[dict | None, str | None]:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少参数 filename",
        )
        file_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(file_path):
            raise BackupServiceError("备份文件不存在")

        os.remove(file_path)
        return None, "删除备份成功"

    def rename_backup(self, data: object) -> dict:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少参数 filename",
        )
        new_name = payload.get("new_name")
        if not new_name:
            raise BackupServiceError("缺少参数 new_name")

        new_name = secure_filename(new_name)
        if new_name.endswith(".zip"):
            new_name = new_name[:-4]
        if not new_name or new_name.replace("_", "") == "":
            raise BackupServiceError("新文件名无效")

        new_filename = f"{new_name}.zip"
        old_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(old_path):
            raise BackupServiceError("备份文件不存在")

        new_path = os.path.join(self.backup_dir, new_filename)
        if os.path.exists(new_path):
            raise BackupServiceError(f"文件名 '{new_filename}' 已存在")

        os.rename(old_path, new_path)
        logger.info(f"备份文件重命名: {filename} -> {new_filename}")
        return {
            "old_filename": filename,
            "new_filename": new_filename,
        }
