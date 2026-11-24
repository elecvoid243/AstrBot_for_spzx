"""Dashboard project boards and their durable reminder schedule."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlmodel import col, select

from astrbot import logger
from astrbot.core.db.po import (
    ChatUIProject,
    DashboardAccount,
    PlatformSession,
    ProjectTodo,
    SessionProjectRelation,
    is_run_conversation_id,
)
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.chat_service import ChatServiceError

from .auth import require_dashboard_user

router = APIRouter(prefix="/api/project-todos", include_in_schema=False)
Owner = Annotated[str, Depends(require_dashboard_user)]
Stage = Literal["todo", "doing", "done"]


class Reminder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(gt=0, le=253402214399)
    kind: Literal["once", "daily", "weekly", "hours"]
    timezone: str = "UTC"
    hours: int = Field(default=1, ge=1, le=8760)
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=list, max_length=7
    )

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Unknown reminder time zone") from exc
        return value

    def next_after(self, now: int) -> int | None:
        """Advance directly past missed occurrences, preserving local calendar time.

        Args:
            now: Exclusive lower bound in Unix seconds.

        Returns:
            Next scheduled instant, or None for an exhausted one-time reminder.
        """
        zone = ZoneInfo(self.timezone)
        start = datetime.fromtimestamp(self.start, zone)
        if self.kind == "weekly" or self.kind == "daily":
            days = self.weekdays or [start.weekday()]
            trigger = CronTrigger(
                day_of_week=",".join(map(str, days)) if self.kind == "weekly" else "*",
                hour=start.hour,
                minute=start.minute,
                second=start.second,
                timezone=zone,
                start_date=start,
            )
            result = trigger.get_next_fire_time(
                None, datetime.fromtimestamp(max(self.start, now + 1), zone)
            )
            return int(result.timestamp()) if result else None
        if self.start > now:
            return self.start
        if self.kind == "hours":
            period = self.hours * 3600
            return self.start + ((now - self.start) // period + 1) * period
        return None


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: UUID
    title: str = Field(min_length=1, max_length=200)
    done: bool = False


class CardLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: UUID
    name: str = Field(min_length=1, max_length=40)
    color: Literal["blue", "teal", "green", "amber", "orange", "red", "purple", "slate"]


class LaneOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[Stage]
    previous: list[Stage]

    @field_validator("order", "previous")
    @classmethod
    def complete_order(cls, value: list[Stage]) -> list[Stage]:
        if sorted(value) != ["doing", "done", "todo"]:
            raise ValueError("Each board column must appear exactly once")
        return value


class CardEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    status: Stage = "todo"
    reminder: Reminder | None = None
    revision: int | None = Field(default=None, ge=0)
    checklist: list[ChecklistItem] = Field(default_factory=list, max_length=200)
    labels: list[CardLabel] = Field(default_factory=list, max_length=32)

    @field_validator("checklist", "labels")
    @classmethod
    def unique_ids(cls, value):
        if len({item.id for item in value}) != len(value):
            raise ValueError("Duplicate item identity")
        return value


class CardMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Stage
    before: UUID | None = None
    revision: int


class Dispatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    request_id: UUID
    revision: int


async def owned_project(
    project_id: str, request: Request, owner: Owner
) -> ChatUIProject:
    """Resolve the project from the authenticated account before any board access.

    Args:
        project_id: Project selected in the URL.
        request: Dashboard request.
        owner: Authenticated resource owner.

    Returns:
        The caller's project.

    Raises:
        ApiError: The project is missing or belongs to a different account.
    """
    project = await request.app.state.db.get_chatui_project_by_id(project_id)
    if project is None or project.creator != owner:
        raise ApiError("Project not found", 404)
    return project


Project = Annotated[ChatUIProject, Depends(owned_project)]


async def owned_card(session, project, card_id):
    """Read a card within its authorized project.

    Args:
        session: Active database transaction.
        project: Authorized project.
        card_id: Requested card identity.

    Returns:
        Project card.

    Raises:
        ApiError: Card missing from this project.
    """
    card = await session.get(ProjectTodo, str(card_id))
    if card is None or card.project_id != project.project_id:
        raise ApiError("Card not found", 404)
    return card


@router.get("/reminders")
async def project_todo_reminders(request: Request, owner: Owner):
    async with request.app.state.db.get_db() as session:
        rows = (
            await session.execute(
                select(ProjectTodo, ChatUIProject.title)
                .join(ChatUIProject, ChatUIProject.project_id == ProjectTodo.project_id)
                .where(
                    ChatUIProject.creator == owner,
                    col(ProjectTodo.unread_at).is_not(None),
                )
                .order_by(col(ProjectTodo.unread_at).desc())
            )
        ).all()
        return ok(
            [
                {
                    "id": c.id,
                    "project_id": c.project_id,
                    "title": c.title,
                    "project": title,
                    "unread_at": c.unread_at,
                }
                for c, title in rows
            ]
        )


@router.get("/targets")
async def project_todo_targets(request: Request, owner: Owner):
    async with request.app.state.db.get_db() as session:
        rows = (
            await session.execute(
                select(
                    PlatformSession.session_id,
                    PlatformSession.display_name,
                    ChatUIProject.title,
                )
                .outerjoin(
                    SessionProjectRelation,
                    SessionProjectRelation.session_id == PlatformSession.session_id,
                )
                .outerjoin(
                    ChatUIProject,
                    ChatUIProject.project_id == SessionProjectRelation.project_id,
                )
                .where(
                    PlatformSession.creator == owner,
                    PlatformSession.platform_id == "webchat",
                    PlatformSession.archived == 0,
                )
                .order_by(col(PlatformSession.updated_at).desc())
            )
        ).all()
        return ok(
            [
                {"id": sid, "name": name, "project": project}
                for sid, name, project in rows
                if not is_run_conversation_id(sid)
            ]
        )


@router.get("/{project_id}")
async def list_project_todos(request: Request, project: Project):
    async with request.app.state.db.get_db() as session:
        rows = await session.execute(
            select(ProjectTodo)
            .where(ProjectTodo.project_id == project.project_id)
            .order_by(ProjectTodo.position, ProjectTodo.id)
        )
        cards = [
            card.model_dump(exclude={"dispatch_receipt", "pending_session_id"})
            for card in rows.scalars()
        ]
        return {**ok(cards), "lane_order": project.todo_lane_order}


@router.post("/{project_id}/layout")
async def reorder_project_todo_lanes(
    request: Request, project: Project, body: LaneOrder
):
    async with (
        request.app.state.todo_lock,
        request.app.state.db.get_db() as session,
        session.begin(),
    ):
        current = await session.get(ChatUIProject, project.inner_id)
        if current is None or current.creator != project.creator:
            raise ApiError("Project not found", 404)
        if current.todo_lane_order != body.order:
            if current.todo_lane_order != body.previous:
                raise ApiError("Board order changed. Refresh the board.", 409)
            current.todo_lane_order = body.order
    return ok()


@router.put("/{project_id}/{card_id}")
async def save_project_todo(
    request: Request, project: Project, card_id: UUID, body: CardEdit
):
    async with (
        request.app.state.todo_lock,
        request.app.state.db.get_db() as session,
        session.begin(),
    ):
        card = await session.get(ProjectTodo, str(card_id))
        if card is None:
            if body.revision is not None:
                raise ApiError("Card not found", 404)
            last = (
                await session.execute(
                    select(func.max(ProjectTodo.position)).where(
                        ProjectTodo.project_id == project.project_id
                    )
                )
            ).scalar()
            card = ProjectTodo(
                id=str(card_id),
                project_id=project.project_id,
                title=body.title,
                position=(last or 0) + 1,
            )
            session.add(card)
        elif card.project_id != project.project_id:
            raise ApiError("Card not found", 404)
        elif card.revision != body.revision:
            raise ApiError("Card changed. Reopen it to edit the latest version.", 409)
        schedule = body.reminder.model_dump() if body.reminder else None
        if body.status == "done" or not schedule:
            card.next_due = card.unread_at = None
        elif schedule != card.reminder or card.status == "done":
            card.next_due = body.reminder.next_after(
                body.reminder.start - 1 if card.status != "done" else int(time.time())
            )
            card.unread_at = None
        card.title, card.description, card.status = (
            body.title,
            body.description,
            body.status,
        )
        card.reminder = schedule
        # Older clients omit these fields when editing a card.
        if "checklist" in body.model_fields_set:
            card.checklist = [item.model_dump(mode="json") for item in body.checklist]
        if "labels" in body.model_fields_set:
            card.labels = [label.model_dump(mode="json") for label in body.labels]
        card.revision += 1
    return ok()


@router.post("/{project_id}/{card_id}/move")
async def move_project_todo(
    request: Request, project: Project, card_id: UUID, body: CardMove
):
    async with (
        request.app.state.todo_lock,
        request.app.state.db.get_db() as session,
        session.begin(),
    ):
        card = await owned_card(session, project, card_id)
        if card.revision != body.revision:
            raise ApiError("Card changed. Refresh the board.", 409)
        peers = list(
            (
                await session.execute(
                    select(ProjectTodo)
                    .where(
                        ProjectTodo.project_id == project.project_id,
                        ProjectTodo.status == body.status,
                        ProjectTodo.id != card.id,
                    )
                    .order_by(ProjectTodo.position, ProjectTodo.id)
                )
            ).scalars()
        )
        index = next(
            (i for i, peer in enumerate(peers) if peer.id == str(body.before)),
            len(peers),
        )
        peers.insert(index, card)
        for i, peer in enumerate(peers):
            peer.position = i
        if body.status == "done":
            card.next_due = card.unread_at = None
        elif card.status == "done" and card.reminder:
            card.next_due = Reminder(**card.reminder).next_after(int(time.time()))
        card.status = body.status
        card.revision += 1
    return ok()


@router.delete("/{project_id}/{card_id}")
async def delete_project_todo(
    request: Request, project: Project, card_id: UUID, revision: int
):
    async with (
        request.app.state.todo_lock,
        request.app.state.db.get_db() as session,
        session.begin(),
    ):
        card = await owned_card(session, project, card_id)
        if revision != card.revision:
            raise ApiError("Card changed. Refresh the board.", 409)
        await session.delete(card)
    return ok()


@router.post("/{project_id}/{card_id}/read")
async def read_project_todo(request: Request, project: Project, card_id: UUID, at: int):
    async with (
        request.app.state.todo_lock,
        request.app.state.db.get_db() as session,
        session.begin(),
    ):
        card = await owned_card(session, project, card_id)
        if card.unread_at == at:
            card.unread_at = None
    return ok()


@router.post("/{project_id}/{card_id}/send")
async def dispatch_project_todo(
    request: Request, project: Project, card_id: UUID, body: Dispatch
):
    """Deliver the card through ordinary user chat admission and retain its target on retry.

    Args:
        request: Authenticated Dashboard request.
        project: Caller-owned project.
        card_id: Card to dispatch.
        body: Target selection, expected card revision and retry identity.

    Returns:
        The accepted session identity, including on replay of the same request.

    Raises:
        ApiError: The target is unavailable, admission fails, or the card changed.
    """
    async with request.app.state.todo_lock, request.app.state.db.get_db() as session:
        card = await owned_card(session, project, card_id)
        receipt = str(body.request_id)
        if card.dispatch_receipt == receipt:
            return ok({"session_id": card.session_id})
        if body.revision != card.revision:
            raise ApiError("Card changed. Reopen it before sending.", 409)
        sid = body.session_id or card.pending_session_id
        if sid:
            dest = (
                await session.execute(
                    select(PlatformSession).where(
                        PlatformSession.session_id == sid,
                        PlatformSession.creator == project.creator,
                        PlatformSession.platform_id == "webchat",
                        PlatformSession.archived == 0,
                    )
                )
            ).scalar_one_or_none()
            if dest is None or is_run_conversation_id(sid):
                if not body.session_id:
                    card.pending_session_id = None
                    await session.commit()
                raise ApiError("Session unavailable", 404)
        else:
            dest = PlatformSession(
                creator=project.creator, platform_id="webchat", display_name=card.title
            )
            session.add(dest)
            sid = dest.session_id
            session.add(
                SessionProjectRelation(project_id=project.project_id, session_id=sid)
            )
            card.pending_session_id = sid
        await session.commit()
        chat = request.app.state.services.chat
        if chat.chat_runs_by_session.get(sid):
            raise ApiError(
                "The target session is busy. Retry once its current turn ends.", 409
            )
        message = "\n\n".join(filter(None, [card.title, card.description]))
        if card.checklist:
            message += "\n\n" + "\n".join(
                f"- [{'x' if item['done'] else ' '}] {item['title']}"
                for item in card.checklist
            )
        try:
            await chat.start_unified_chat_run(
                project.creator,
                {
                    "session_id": sid,
                    "message": message,
                },
            )
        except ChatServiceError as exc:
            raise ApiError(str(exc), 409) from exc
        card.session_id = sid
        card.pending_session_id = None
        card.dispatch_receipt = receipt
        if card.status == "todo":
            card.status = "doing"
        card.revision += 1
        await session.commit()
    return ok({"session_id": sid})


async def run_reminders(app):
    """Persist one unread occurrence per overdue card, then notify the bound desktop.

    Args:
        app: Dashboard application owning the database and optional tray callback.
    """
    while True:
        try:
            notifications = []
            now = int(time.time())
            async with (
                app.state.todo_lock,
                app.state.db.get_db() as session,
                session.begin(),
            ):
                rows = (
                    await session.execute(
                        select(ProjectTodo, ChatUIProject)
                        .join(
                            ChatUIProject,
                            ChatUIProject.project_id == ProjectTodo.project_id,
                        )
                        .where(
                            col(ProjectTodo.next_due) <= now,
                            ProjectTodo.status != "done",
                        )
                    )
                ).all()
                notify = getattr(app.state.core_lifecycle, "notify_project_todo", None)
                admin = (
                    (
                        await session.execute(
                            select(DashboardAccount).where(
                                DashboardAccount.bootstrap_key == "dashboard",
                                DashboardAccount.role == "admin",
                                col(DashboardAccount.enabled).is_(True),
                            )
                        )
                    ).scalar_one_or_none()
                    if rows and notify
                    else None
                )
                for card, project in rows:
                    card.unread_at = now
                    card.next_due = Reminder(**card.reminder).next_after(now)
                    if admin and project.creator == admin.resource_owner:
                        notifications.append(
                            (project.project_id, card.id, project.title, card.title)
                        )
            for item in notifications:
                try:
                    notify(*item)
                except Exception:
                    logger.exception("Failed to show project reminder on the desktop")
        except Exception:
            logger.exception("Failed to process project reminders")
        await asyncio.sleep(5)
