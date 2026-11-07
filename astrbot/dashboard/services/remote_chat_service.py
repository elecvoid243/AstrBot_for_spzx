"""Direct account-bound remote reads over the ordinary local ChatUI service."""

import hashlib
import secrets

from sqlmodel import select

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    DashboardAccount,
    RemoteChatCredential,
    RemoteChatExposure,
)
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.services.chat_service import ChatService


class RemoteChatService:
    """Keep remote credentials separate from global API keys and login secrets."""

    def __init__(
        self, db: BaseDatabase, config: AstrBotConfig, chat: ChatService
    ) -> None:
        self.db = db
        self.config = config
        self.chat = chat

    def require_enabled(self) -> None:
        """Gate new inbound and outbound remote operations using live config.

        Raises:
            ApiError: The instance has remote connections disabled.
        """
        if self.config.get("chatui", {}).get("remote_sessions_enabled") is not True:
            raise ApiError("Remote session connections are disabled", status_code=403)

    async def authenticate(self, raw_key: str) -> DashboardAccount:
        """Resolve a dedicated key against its current account on every request.

        Args:
            raw_key: High-entropy connection secret from the remote caller.

        Returns:
            Enabled account bound to this credential, with its stable owner.

        Raises:
            ApiError: Remote access is disabled or the credential/account is invalid.
        """
        self.require_enabled()
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        async with self.db.get_db() as connection:
            credential = (
                await connection.execute(
                    select(RemoteChatCredential).where(
                        RemoteChatCredential.key_hash == key_hash,
                        RemoteChatCredential.revoked == False,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()
            account = (
                await connection.get(DashboardAccount, credential.account_id)
                if credential
                else None
            )
        if (
            account is None
            or not account.enabled
            or account.role not in {"user", "admin"}
            or account.token_version != credential.account_token_version
        ):
            raise ApiError("Invalid remote credential", status_code=401)
        return account

    async def sessions(
        self, account: DashboardAccount, *, exposed_only: bool = True
    ) -> list[dict]:
        """Discover only this account's ordinary sessions on this instance.

        Args:
            account: Trusted account resolved by login or dedicated credential.
            exposed_only: Apply the current exposure policy for a remote caller.

        Returns:
            Local WebChat sessions with safe metadata and owned project membership.
        """
        async with self.db.get_db() as connection:
            policy = await connection.get(RemoteChatExposure, account.account_id)
        selected = set(policy.session_ids) if policy else set()
        result = []
        page = 1
        while True:
            rows, total = await self.db.get_platform_sessions_by_creator_paginated(
                account.resource_owner, "webchat", page=page, page_size=100
            )
            for row in rows:
                session = row["session"]
                if session.is_group or (
                    exposed_only
                    and not (policy and policy.mode == "all")
                    and session.session_id not in selected
                ):
                    continue
                result.append(
                    {
                        "session_id": session.session_id,
                        "display_name": session.display_name,
                        "archived": bool(session.archived),
                        # The existing query joins projects with the same owner,
                        # without the project-list endpoint's default page cap.
                        "project_id": row["project_id"],
                        "project_name": row["project_title"],
                        "project_emoji": row["project_emoji"],
                    }
                )
            if page * 100 >= total:
                return result
            page += 1

    async def require_session(self, account: DashboardAccount, session_id: str) -> None:
        """Check live exposure before any session or associated-resource access.

        Args:
            account: Account freshly authenticated by a dedicated credential.
            session_id: Requested ordinary local session.

        Raises:
            ApiError: The session is foreign, internal, missing, or unexposed.
        """
        self.require_enabled()
        async with self.db.get_db() as connection:
            current = await connection.get(DashboardAccount, account.account_id)
            policy = await connection.get(RemoteChatExposure, account.account_id)
        session = await self.db.get_platform_session_by_id(session_id)
        if (
            current is None
            or not current.enabled
            or current.token_version != account.token_version
            or session is None
            or session.creator != current.resource_owner
            or session.platform_id != "webchat"
            or session.is_group
            or policy is None
            or (policy.mode != "all" and session_id not in policy.session_ids)
        ):
            raise ApiError("Session is unavailable", status_code=404)

    async def access(self, account: DashboardAccount) -> dict:
        """Read the signed-in account's policy and non-secret credential metadata.

        Args:
            account: Current Dashboard account.

        Returns:
            Exposure settings, selectable sessions and credentials without verifiers.
        """
        async with self.db.get_db() as connection:
            policy = await connection.get(RemoteChatExposure, account.account_id)
            credentials = (
                (
                    await connection.execute(
                        select(RemoteChatCredential).where(
                            RemoteChatCredential.account_id == account.account_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {
            "enabled": self.config.get("chatui", {}).get("remote_sessions_enabled")
            is True,
            "mode": policy.mode if policy else "selected",
            "session_ids": policy.session_ids if policy else [],
            "sessions": await self.sessions(account, exposed_only=False),
            "credentials": [
                {
                    "credential_id": credential.credential_id,
                    "name": credential.name,
                    "revoked": credential.revoked
                    or credential.account_token_version != account.token_version,
                }
                for credential in credentials
            ],
        }

    async def set_exposure(
        self, account: DashboardAccount, mode: str, session_ids: list[str]
    ) -> None:
        """Persist a policy after checking that every selected ID is local and owned.

        Args:
            account: Current Dashboard account managing its own exposure.
            mode: All ordinary sessions or only selected IDs.
            session_ids: Explicit selection; empty in all mode.

        Raises:
            ApiError: A selected session is unavailable to this account.
        """
        available = {
            item["session_id"]
            for item in await self.sessions(account, exposed_only=False)
        }
        if set(session_ids) - available:
            raise ApiError("Session is unavailable", status_code=404)
        async with self.db.get_db() as connection:
            policy = await connection.get(RemoteChatExposure, account.account_id)
            if policy is None:
                policy = RemoteChatExposure(account_id=account.account_id)
            policy.mode = mode
            policy.session_ids = (
                list(dict.fromkeys(session_ids)) if mode == "selected" else []
            )
            connection.add(policy)
            await connection.commit()

    async def create_credential(self, account: DashboardAccount, name: str) -> dict:
        """Issue one independently revocable secret for a direct binding.

        Args:
            account: Current Dashboard account to bind immutably.
            name: Human-readable binding label.

        Returns:
            Credential ID and the sole plaintext copy of the new secret.
        """
        raw_key = "arc_" + secrets.token_urlsafe(32)
        credential = RemoteChatCredential(
            account_id=account.account_id,
            account_token_version=account.token_version,
            name=name,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        )
        async with self.db.get_db() as connection:
            connection.add(credential)
            await connection.commit()
        return {"credential_id": credential.credential_id, "key": raw_key}

    async def revoke_credential(
        self, account: DashboardAccount, credential_id: str
    ) -> None:
        """Revoke a binding without revealing another account's credential IDs.

        Args:
            account: Current Dashboard account managing its own credentials.
            credential_id: Credential to revoke permanently.

        Raises:
            ApiError: The credential is missing or belongs to another account.
        """
        async with self.db.get_db() as connection:
            credential = await connection.get(RemoteChatCredential, credential_id)
            if credential is None or credential.account_id != account.account_id:
                raise ApiError("Credential is unavailable", status_code=404)
            credential.revoked = True
            connection.add(credential)
            await connection.commit()
