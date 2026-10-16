"""Explicit platform access and delivery over the shared asset/run service."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import select

from astrbot.core import logger
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.db.po import (
    AgentWorkflowChatAccess,
    AgentWorkflowChatDelivery,
    AgentWorkflowRun,
)

if TYPE_CHECKING:
    from astrbot.core.agent_teams.workflows import WorkflowService


class ChatCaller(BaseModel):
    """Identity taken from a real platform event and its configured routing."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    platform_id: str = Field(min_length=1, max_length=128)
    sender_id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=512)
    profile_id: str = Field(min_length=1, max_length=128)

    @property
    def principal(self) -> str:
        """Return a platform-qualified identity outside the dashboard namespace.

        Returns:
            An unambiguous platform instance and sender identity.
        """
        return f"platform:{quote(self.platform_id, safe='')}:{quote(self.sender_id, safe='')}"


class ChatAccessRequest(BaseModel):
    """A deliberate local grant; importing an asset never grants access."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    platform_id: str = Field(min_length=1, max_length=128)
    sender_id: str = Field(min_length=1, max_length=256)
    version: int = Field(ge=1)
    profile_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=512)


class WorkflowChat:
    """Platform admission to existing assets without another execution engine."""

    def __init__(self, service: "WorkflowService"):
        """Use the same application service and persistence as WebUI.

        Args:
            service: Core-owned asset/run service.
        """
        self.service = service
        self._delivery_lock = asyncio.Lock()
        self._live_senders: dict[str, Callable[[ChatCaller, str], Awaitable[bool]]] = {}

    async def save_access(self, username: str, asset_id: str, payload: dict) -> dict:
        """Open a fixed asset version to an exact platform instance and sender.

        Args:
            username: Authenticated asset owner, never supplied by chat content.
            asset_id: Asset to open.
            payload: Exact caller, version and source configuration profile.

        Returns:
            The saved local access grant.

        Raises:
            RoleAssetError: The owner cannot access that asset version.
        """
        try:
            request = ChatAccessRequest.model_validate(payload)
        except ValidationError as exc:
            raise RoleAssetError("Invalid chat access") from exc
        await self.service.get_asset(username, asset_id, request.version)
        grant = AgentWorkflowChatAccess(
            asset_id=asset_id, owner_username=username, **request.model_dump()
        )
        async with self.service.db.get_db() as session:
            await session.merge(grant)
            await session.commit()
        return request.model_dump()

    async def list_assets(self, caller: ChatCaller) -> dict:
        """Offer only definitions deliberately opened to this platform caller.

        Args:
            caller: Verified event identity and current source profile.

        Returns:
            Names, fixed versions and required input names, without role content.
        """
        async with self.service.db.get_db() as session:
            grants = (
                (
                    await session.execute(
                        select(AgentWorkflowChatAccess).where(
                            AgentWorkflowChatAccess.platform_id == caller.platform_id,
                            AgentWorkflowChatAccess.sender_id == caller.sender_id,
                            AgentWorkflowChatAccess.profile_id == caller.profile_id,
                            AgentWorkflowChatAccess.source == caller.source,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assets = []
        for grant in grants:
            try:
                await self.require_access(
                    {
                        "caller": caller.model_dump(),
                        "asset_id": grant.asset_id,
                        "version": grant.version,
                    },
                    grant.owner_username,
                )
            except RoleAssetError:
                continue
            asset = await self.service.get_asset(
                grant.owner_username, grant.asset_id, grant.version
            )
            assets.append(
                {
                    "asset_id": grant.asset_id,
                    "version": grant.version,
                    "name": asset["definition"]["name"],
                    "required_inputs": asset["definition"]["required_inputs"],
                }
            )
        return {"assets": assets}

    async def list_access(self, username: str, asset_id: str) -> dict:
        """Read an owner's deliberate local chat grants.

        Args:
            username: Authenticated asset owner.
            asset_id: Owned asset identity.

        Returns:
            Local grants, excluding all execution state.

        Raises:
            RoleAssetError: The asset is inaccessible.
        """
        await self.service.get_asset(username, asset_id)
        async with self.service.db.get_db() as session:
            grants = (
                (
                    await session.execute(
                        select(AgentWorkflowChatAccess).where(
                            AgentWorkflowChatAccess.asset_id == asset_id,
                            AgentWorkflowChatAccess.owner_username == username,
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {
            "grants": [
                g.model_dump(exclude={"owner_username", "asset_id"}) for g in grants
            ]
        }

    async def revoke_access(
        self, username: str, asset_id: str, platform_id: str, sender_id: str
    ) -> None:
        """Revoke future execution boundaries and result delivery for a caller.

        Args:
            username: Authenticated asset owner.
            asset_id: Owned asset identity.
            platform_id: Exact platform instance.
            sender_id: Exact sender on that instance.

        Raises:
            RoleAssetError: The asset is inaccessible.
        """
        await self.service.get_asset(username, asset_id)
        async with self.service.db.get_db() as session:
            grant = await session.get(
                AgentWorkflowChatAccess, (asset_id, platform_id, sender_id)
            )
            if grant is not None:
                await session.delete(grant)
                await session.commit()

    async def invoke(self, caller: ChatCaller, selector: str, inputs: dict) -> dict:
        """Resolve an explicit selection and clarify before admitting execution.

        Args:
            caller: Actual platform event identity.
            selector: Exact accessible asset ID or name.
            inputs: Explicit named text inputs.

        Returns:
            A clarification or the accepted run identity.

        Raises:
            RoleAssetError: The asset is unavailable or inputs are invalid.
        """
        assets = (await self.list_assets(caller))["assets"]
        choices = [a for a in assets if a["asset_id"] == selector]
        if not choices:
            choices = [a for a in assets if a["name"] == selector]
        if not choices:
            raise RoleAssetError("Asset is not available to this caller", status=404)
        if len(choices) > 1:
            return {"status": "clarification", "choices": choices}
        asset = choices[0]
        fields = [
            {
                "path": f"inputs.{name}",
                "code": "REQUIRED",
                "message": "Provide non-empty text",
            }
            for name in asset["required_inputs"]
            if not isinstance(inputs.get(name), str) or not inputs[name].strip()
        ]
        if fields:
            return {
                "status": "clarification",
                "asset_id": asset["asset_id"],
                "fields": fields,
            }
        async with self.service.db.get_db() as session:
            grant = await session.get(
                AgentWorkflowChatAccess,
                (asset["asset_id"], caller.platform_id, caller.sender_id),
            )
        if (
            grant is None
            or grant.source != caller.source
            or grant.profile_id != caller.profile_id
        ):
            raise RoleAssetError("Asset is not available to this caller", status=404)
        run = await self.service.start_run(
            grant.owner_username,
            grant.asset_id,
            {"version": grant.version, "inputs": inputs, "source": caller.source},
            chat={
                "caller": caller.model_dump(),
                "asset_id": grant.asset_id,
                "version": grant.version,
            },
        )
        return {
            "status": "accepted",
            "run_id": run["run_id"],
            "detail_path": "/#/agent-teams?workflow_run=" + run["run_id"],
        }

    def prepare(self, frozen: dict, chat: dict) -> None:
        """Constrain every frozen member and nested scope to the caller profile.

        Args:
            frozen: Resolved launch snapshot, before any execution is persisted.
            chat: Verified caller and fixed grant identity.

        Raises:
            RoleAssetError: Any reachable member uses another source profile.
        """
        caller = ChatCaller.model_validate(chat["caller"])
        pending = [frozen]
        while pending:
            scope = pending.pop()
            scope["chat"] = chat
            scope["initiator"] = caller.principal
            members = (
                [scope]
                if "role" in scope
                else [
                    *scope.get("members", {}).values(),
                    *scope.get("allowed_roles", {}).values(),
                ]
            )
            for member in members:
                if member["profile_id"] != caller.profile_id:
                    raise RoleAssetError(
                        "Asset resources are outside the caller's source profile",
                        status=403,
                    )
                member["profile"]["admins_id"] = []
            pending.extend(scope.get("child_assets", {}).values())
            pending.extend(scope.get("allowed_workflows", {}).values())

    async def require_access(self, chat: dict, username: str) -> None:
        """Recheck the exact local grant at launch, execution and result release.

        Args:
            chat: Frozen verified caller and root asset grant identity.
            username: Asset owner whose configured definition is being used.

        Raises:
            RoleAssetError: Access was revoked or source routing has changed.
        """
        caller = ChatCaller.model_validate(chat["caller"])
        async with self.service.db.get_db() as session:
            grant = await session.get(
                AgentWorkflowChatAccess,
                (
                    chat["asset_id"],
                    caller.platform_id,
                    caller.sender_id,
                ),
            )
        if (
            grant is None
            or grant.owner_username != username
            or grant.version != chat["version"]
            or grant.source != caller.source
            or grant.profile_id != caller.profile_id
        ):
            raise RoleAssetError("Chat access is no longer available", status=403)
        core = self.service.roles.core_lifecycle
        if core is not None:
            platform = next(
                (
                    item
                    for item in core.platform_manager.platform_insts
                    if item.meta().id == caller.platform_id
                ),
                None,
            )
            if platform is not None and (
                platform.meta().name == "satori"
                or (
                    platform.meta().name == "misskey"
                    and not caller.source.split(":", 2)[-1].startswith(
                        ("chat%", "room%")
                    )
                )
            ):
                raise RoleAssetError(
                    "This platform source does not identify a unique audience or account",
                    status=403,
                )
        if (
            core is not None
            and core.astrbot_config_mgr.get_conf_info(caller.source)["id"]
            != caller.profile_id
        ):
            raise RoleAssetError("The caller's source profile has changed", status=403)

    async def get_run(self, caller: ChatCaller, run_id: str) -> dict:
        """Read the caller's final result without exposing execution internals.

        Args:
            caller: Actual caller in the authorized result delivery conversation.
            run_id: Previously accepted root run.

        Returns:
            Status, final result and the existing authenticated WebUI detail path.

        Raises:
            RoleAssetError: The caller or source cannot access this run.
        """
        async with self.service.db.get_db() as session:
            row = await session.get(AgentWorkflowRun, run_id)
            if (
                row is None
                or row.snapshot.get("parent_run_id") is not None
                or row.snapshot.get("chat", {}).get("caller") != caller.model_dump()
            ):
                raise RoleAssetError("Run not found", status=404)
        await self.require_access(row.snapshot["chat"], row.owner_username)
        run = await self.service.get_run(row.owner_username, run_id)
        return {
            "run_id": run_id,
            "status": run["status"],
            "result": run["result"] if run["status"] == "completed" else None,
            "detail_path": "/#/agent-teams?workflow_run=" + run_id,
        }

    async def deliver_pending(
        self,
        send: Callable[[ChatCaller, str], Awaitable[bool]],
        run_id: str | None = None,
    ) -> int:
        """Send accepted, waiting and terminal notices to the authorized source.

        Args:
            send: Platform delivery boundary; False leaves the notice pending.
            run_id: Optional just-accepted run to acknowledge on its live event.

        Returns:
            Number of successfully delivered messages.
        """
        count = 0
        async with self._delivery_lock:
            if run_id is not None:
                self._live_senders[run_id] = send
            async with self.service.db.get_db() as session:
                query = select(AgentWorkflowChatDelivery).where(
                    AgentWorkflowChatDelivery.finished == False  # noqa: E712
                )
                if run_id is not None:
                    query = query.where(AgentWorkflowChatDelivery.run_id == run_id)
                deliveries = (await session.execute(query)).scalars().all()
            for delivery in deliveries:
                try:
                    async with self.service.db.get_db() as session:
                        row = await session.get(AgentWorkflowRun, delivery.run_id)
                    if row is None:
                        continue
                    caller = ChatCaller.model_validate(row.snapshot["chat"]["caller"])
                    projected = await self.get_run(caller, row.run_id)
                    run = await self.service.get_run(row.owner_username, row.run_id)
                    link = projected["detail_path"]
                    notices = [
                        ("accepted", f"Accepted run {row.run_id}. Details: {link}")
                    ]
                    pending = [
                        t["todo_id"]
                        for t in run.get("todos", [])
                        if t["status"] == "pending"
                    ]
                    if pending:
                        notices.append(
                            (
                                "waiting:" + ":".join(sorted(pending)),
                                f"Run {row.run_id} needs human input. Answer the existing pending request in WebUI: {link}",
                            )
                        )
                    terminal = projected["status"] in {
                        "completed",
                        "cancelled",
                        "failed",
                        "interrupted",
                        "awaiting_result",
                    }
                    if projected["status"] == "blocked" and not pending:
                        terminal = True
                    if terminal:
                        text = (
                            f"Run {row.run_id}: {projected['status']}. Details: {link}"
                        )
                        if projected["result"] is not None:
                            text += "\n" + json.dumps(
                                projected["result"], ensure_ascii=False
                            )
                        # Keep each message within common platform text limits.
                        parts = [text[i : i + 1500] for i in range(0, len(text), 1500)]
                        notices.extend(
                            (f"state:{projected['status']}:{i}", part)
                            for i, part in enumerate(parts)
                        )
                    for key, text in notices:
                        if key in delivery.delivered:
                            continue
                        # Recheck the grant immediately before releasing content.
                        await self.get_run(caller, row.run_id)
                        sender = self._live_senders.get(row.run_id, send)
                        if not await sender(caller, text):
                            break
                        count += 1
                        delivery.delivered = [*delivery.delivered, key]
                        async with self.service.db.get_db() as session:
                            await session.merge(delivery)
                            await session.commit()
                    else:
                        if projected["status"] in {"completed", "cancelled"}:
                            delivery.finished = True
                            self._live_senders.pop(row.run_id, None)
                            async with self.service.db.get_db() as session:
                                await session.merge(delivery)
                                await session.commit()
                except RoleAssetError:
                    # Revocation suppresses delivery; a later deliberate grant
                    # can make the retained run accessible again.
                    continue
                except Exception:
                    self._live_senders.pop(delivery.run_id, None)
                    logger.exception("Failed to deliver an explicit asset run notice")
        return count

    async def run_delivery(
        self, send: Callable[[ChatCaller, str], Awaitable[bool]]
    ) -> None:
        """Resume pending source notifications for the core service lifetime.

        Args:
            send: Existing platform sender, available after platform initialization.
        """
        while True:
            await self.deliver_pending(send)
            await asyncio.sleep(1)
