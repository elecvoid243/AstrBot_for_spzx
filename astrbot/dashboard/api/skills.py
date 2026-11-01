from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from astrbot.core import logger
from astrbot.core.skills.skill_guide import (
    _filter_plugin_skills,
    build_active_payload,
    guide_state,
    resolve_active_skills,
    resolve_all_skills,
    validate_load_skill,
)
from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.responses import ApiError, error, ok
from astrbot.dashboard.schemas import (
    SkillByNameUpdateRequest,
    SkillFileUpdateRequest,
    SkillNeoRequest,
    SkillUpdateRequest,
)
from astrbot.dashboard.services.chat_service import (
    ChatServiceError,
    build_thread_unified_msg_origin,
    build_webchat_unified_msg_origin,
)
from astrbot.dashboard.services.skills_service import (
    SkillArchive,
    SkillsOperationResult,
    SkillsService,
    SkillsServiceError,
)

from .auth import AuthContext, ScopeDependency, require_dashboard_user
from .multipart import multipart_parts, single_upload

router = APIRouter(tags=["Skills"])
legacy_router = APIRouter(
    prefix="/api/skills",
    tags=["Dashboard Skills"],
    include_in_schema=False,
)


def get_service(request: Request) -> SkillsService:
    return request.app.state.services.skills


require_skill_scope = ScopeDependency("skill")


async def _json_or_empty(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Missing key: {name}")
    return text


def _model_dict(payload) -> dict[str, Any]:
    if payload is None:
        return {}
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_none=True)
    return payload if isinstance(payload, dict) else {}


def _serialize_result(result: SkillsOperationResult):
    if result.ok:
        return ok(result.data, result.message)
    return error(result.message or "", result.data)


async def _run(operation, *, trace: bool = True):
    try:
        result = await run_maybe_async(operation)
        if isinstance(result, SkillsOperationResult):
            return _serialize_result(result)
        return ok(result)
    except SkillsServiceError as exc:
        return error(str(exc))
    except Exception as exc:
        logger.error(str(exc), exc_info=trace)
        return error(str(exc))


def _archive_response(archive: SkillArchive):
    return FileResponse(
        archive.path,
        filename=archive.filename,
        media_type="application/zip",
    )


async def _download_skill(service: SkillsService, name: str):
    try:
        return _archive_response(service.prepare_skill_archive(name))
    except SkillsServiceError as exc:
        message = str(exc)
        raise HTTPException(status_code=exc.status_code, detail=message) from exc
    except Exception as exc:
        logger.error(str(exc), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to prepare skill archive",
        ) from exc


@router.get("/skills")
async def list_skills(
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(service.get_skills)


@router.get("/skills/store")
async def get_skill_store(
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    """Resolve and probe the skill store configured by the update source."""
    return await _run(service.get_store)


@router.post("/skills")
async def upload_skill(
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    async def _operation():
        return await service.upload_skill(await single_upload(request))

    return await _run(_operation)


@router.post("/skills/batch")
async def upload_skills_batch(
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    async def _operation():
        _, files = await multipart_parts(request)
        return await service.batch_upload_skills(files.getlist("files"))

    return await _run(_operation)


@router.patch("/skills/by-name")
async def update_skill_by_name(
    payload: SkillByNameUpdateRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    skill_name = _required_text(payload.skill_name, "skill_name")
    return await _run(
        lambda: service.update_skill(
            {
                "name": skill_name,
                "active": payload.active_value(),
            }
        )
    )


@router.delete("/skills/by-name")
async def delete_skill_by_name(
    skill_name: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.delete_skill({"name": skill_name}))


@router.get("/skills/archive")
async def download_skill_by_name(
    skill_name: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _download_skill(service, skill_name)


@router.get("/skills/files")
async def list_skill_files_by_name(
    request: Request,
    skill_name: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(
        lambda: service.list_skill_files(
            skill_name,
            request.query_params.get("path", ""),
        )
    )


@router.get("/skills/file")
async def get_skill_file_by_name(
    skill_name: str,
    path: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.get_skill_file(skill_name, path))


@router.put("/skills/file")
async def update_skill_file_by_name(
    payload: SkillFileUpdateRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    skill_name = _required_text(payload.skill_name, "skill_name")
    path = _required_text(payload.path, "path")
    return await _run(
        lambda: service.update_skill_file(
            {
                "name": skill_name,
                "path": path,
                "content": payload.content,
            }
        )
    )


@router.get("/skills/{skill_name:path}/archive")
async def download_skill(
    skill_name: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _download_skill(service, skill_name)


@router.get("/skills/{skill_name:path}/files")
async def list_skill_files(
    skill_name: str,
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(
        lambda: service.list_skill_files(
            skill_name,
            request.query_params.get("path", ""),
        )
    )


@router.get("/skills/{skill_name:path}/files/{file_path:path}")
async def get_skill_file(
    skill_name: str,
    file_path: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.get_skill_file(skill_name, file_path))


@router.put("/skills/{skill_name:path}/files/{file_path:path}")
async def update_skill_file(
    skill_name: str,
    file_path: str,
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    content = (await request.body()).decode("utf-8")
    return await _run(
        lambda: service.update_skill_file(
            {"name": skill_name, "path": file_path, "content": content}
        )
    )


@router.patch("/skills/{skill_name:path}")
async def update_skill(
    skill_name: str,
    payload: SkillUpdateRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(
        lambda: service.update_skill(
            {
                "name": skill_name,
                "active": payload.active_value(),
            }
        )
    )


@router.delete("/skills/{skill_name:path}")
async def delete_skill(
    skill_name: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.delete_skill({"name": skill_name}))


@router.get("/skills/neo/candidates")
async def list_neo_skill_candidates(
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(
        service.get_neo_candidates(
            dict(request.query_params),
        )
    )


@router.get("/skills/neo/releases")
async def list_neo_skill_releases(
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(
        service.get_neo_releases(
            dict(request.query_params),
        )
    )


@router.get("/skills/neo/payload")
async def get_neo_skill_payload(
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(service.get_neo_payload(dict(request.query_params)))


@router.post("/skills/neo/evaluate")
async def evaluate_neo_skill_candidate(
    payload: SkillNeoRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.evaluate_neo_candidate(_model_dict(payload)))


@router.post("/skills/neo/promote")
async def promote_neo_skill_candidate(
    payload: SkillNeoRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.promote_neo_candidate(_model_dict(payload)))


@router.post("/skills/neo/rollback")
async def rollback_neo_skill_release(
    payload: SkillNeoRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.rollback_neo_release(_model_dict(payload)))


@router.post("/skills/neo/sync")
async def sync_neo_skill_release(
    payload: SkillNeoRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.sync_neo_release(_model_dict(payload)))


@router.post("/skills/neo/candidates/delete")
async def delete_neo_skill_candidate(
    payload: SkillNeoRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.delete_neo_candidate(_model_dict(payload)))


@router.post("/skills/neo/releases/delete")
async def delete_neo_skill_release(
    payload: SkillNeoRequest,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.delete_neo_release(_model_dict(payload)))


@legacy_router.get("")
async def list_dashboard_skills(
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    return await _run(service.get_skills)


@legacy_router.post("/upload")
async def upload_dashboard_skill(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    async def _operation():
        return await service.upload_skill(await single_upload(request))

    return await _run(_operation)


@legacy_router.post("/batch-upload")
async def batch_upload_dashboard_skills(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    async def _operation():
        _, files = await multipart_parts(request)
        return await service.batch_upload_skills(files.getlist("files"))

    return await _run(_operation)


@legacy_router.get("/download")
async def download_dashboard_skill(
    name: str,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    return await _download_skill(service, name)


@legacy_router.get("/files")
async def list_dashboard_skill_files(
    request: Request,
    name: str,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    return await _run(
        lambda: service.list_skill_files(name, request.query_params.get("path", ""))
    )


@legacy_router.get("/file")
async def get_dashboard_skill_file(
    name: str,
    path: str = "SKILL.md",
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    return await _run(lambda: service.get_skill_file(name, path))


@legacy_router.post("/file")
async def update_dashboard_skill_file(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.update_skill_file(body))


@legacy_router.post("/update")
async def update_dashboard_skill(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.update_skill(body))


@legacy_router.post("/delete")
async def delete_dashboard_skill(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.delete_skill(body))


@legacy_router.get("/neo/candidates")
async def list_dashboard_neo_skill_candidates(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    return await _run(service.get_neo_candidates(dict(request.query_params)))


@legacy_router.get("/neo/releases")
async def list_dashboard_neo_skill_releases(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    return await _run(service.get_neo_releases(dict(request.query_params)))


@legacy_router.get("/neo/payload")
async def get_dashboard_neo_skill_payload(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    return await _run(service.get_neo_payload(dict(request.query_params)))


@legacy_router.post("/neo/evaluate")
async def evaluate_dashboard_neo_skill_candidate(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.evaluate_neo_candidate(body))


@legacy_router.post("/neo/promote")
async def promote_dashboard_neo_skill_candidate(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.promote_neo_candidate(body))


@legacy_router.post("/neo/rollback")
async def rollback_dashboard_neo_skill_release(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.rollback_neo_release(body))


@legacy_router.post("/neo/sync")
async def sync_dashboard_neo_skill_release(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.sync_neo_release(body))


@legacy_router.post("/neo/delete-candidate")
async def delete_dashboard_neo_skill_candidate(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.delete_neo_candidate(body))


@legacy_router.post("/neo/delete-release")
async def delete_dashboard_neo_skill_release(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: SkillsService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(lambda: service.delete_neo_release(body))


# --- Skill guide (one-shot skill nudges) ------------------------------------


async def _owned_skill_origin(request: Request, auth: AuthContext, umo: str) -> str:
    """Resolve the real conversation before consuming or queuing skill state.

    Args:
        request: Current Dashboard request.
        auth: Trusted account or independent scoped API key.
        umo: Requested unified message origin.

    Returns:
        The verified origin, constructed from its persisted owner.

    Raises:
        ApiError: The origin is malformed, foreign or unavailable.
    """
    if auth.via == "api_key":
        return umo
    session_id = umo.rsplit("!", 1)[-1]
    try:
        session = await request.app.state.services.chat.require_owned_chat_session(
            auth.username, session_id, allow_thread=True
        )
    except ChatServiceError as exc:
        raise ApiError("Resource is unavailable", status_code=403) from exc
    origin = (
        build_thread_unified_msg_origin(auth.username, session_id)
        if hasattr(session, "thread_id")
        else build_webchat_unified_msg_origin(session)
    )
    if umo != origin:
        raise ApiError("Resource is unavailable", status_code=403)
    return origin


@router.get("/skill-guide/inventory")
async def skill_guide_inventory(
    request: Request,
    umo: str,
    auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    """List skill consumption metadata without granting skill management.

    Args:
        request: Current Dashboard request.
        umo: Requested owned conversation origin.
        auth: Authenticated caller.
        service: Existing skill resolver.

    Returns:
        Skill names and descriptions used by the existing command palette.
    """
    umo = await _owned_skill_origin(request, auth, umo)
    context = service.core_lifecycle.star_context
    skills = await resolve_all_skills(context, umo)
    from astrbot.core.star.star import star_registry

    available = {
        skill.name
        for skill in _filter_plugin_skills(
            skills,
            context.get_config(umo=umo)
            .get("provider_settings", {})
            .get("plugin_set", ["*"]),
            star_registry,
        )
    }
    payload = build_active_payload(skills, None)
    for item, skill in zip(payload["skills"], skills):
        item["active"] = bool(getattr(skill, "active", True))
        if skill.source_type == "plugin":
            item["plugin_active"] = skill.name in available
    return ok(payload)


@router.get("/skill-guide/active")
async def skill_guide_active(
    request: Request,
    umo: str,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    """List the skills effective for a session, for the guide menu.

    Args:
        umo: Unified message origin of the session.
    """
    umo = await _owned_skill_origin(request, _auth, umo)
    context = service.core_lifecycle.star_context
    try:
        skills, persona = await run_maybe_async(
            lambda: resolve_active_skills(context, umo),
        )
    except Exception as exc:
        logger.error(f"skill guide: failed to resolve active skills: {exc}")
        return error("resolve failed")
    return ok(build_active_payload(skills, persona))


@router.post("/skill-guide/load")
async def skill_guide_load(
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    """Queue a one-shot skill nudge for a session.

    Manual loads may target any skill on disk (not only session-active
    ones); the guidance prompt only needs the SKILL.md path.
    """
    body = await _json_or_empty(request)
    umo = str(body.get("umo") or "").strip()
    skill_name = str(body.get("skill_name") or "").strip()
    if not umo:
        return error("missing umo")
    if not skill_name:
        return error("missing skill_name")

    umo = await _owned_skill_origin(request, _auth, umo)
    context = service.core_lifecycle.star_context
    try:
        all_skills = await run_maybe_async(lambda: resolve_all_skills(context, umo))
    except Exception as exc:
        logger.error(f"skill guide: failed to resolve skills: {exc}")
        return error("resolve failed")
    if err := validate_load_skill(all_skills, skill_name):
        return error(err)
    guide_state.queue(umo, skill_name)
    return ok({"skill_name": skill_name, "queued": True})


@router.post("/skill-guide/clear")
async def skill_guide_clear(
    request: Request,
    _auth: AuthContext = Depends(require_skill_scope),
    service: SkillsService = Depends(get_service),
):
    """Drop the unconsumed guide queue of a session (undo)."""
    body = await _json_or_empty(request)
    umo = str(body.get("umo") or "").strip()
    if not umo:
        return error("missing umo")
    umo = await _owned_skill_origin(request, _auth, umo)
    return ok({"cleared": guide_state.clear(umo)})
