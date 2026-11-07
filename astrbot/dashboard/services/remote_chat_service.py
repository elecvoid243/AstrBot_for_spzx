"""Direct account-bound remote reads over the ordinary local ChatUI service."""

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import anyio
import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import select

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import (
    DashboardAccount,
    RemoteChatConnection,
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
        # Bounded, process-local admission receipts. These never schedule delivery.
        self.receipts: dict[tuple[str, str, str], dict] = {}
        # Invalidate admissions whose asynchronous reads overlap a grant change.
        self.grant_revisions: dict[str, int] = {}

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
            self.grant_revisions[account.account_id] = (
                self.grant_revisions.get(account.account_id, 0) + 1
            )

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
            self.grant_revisions[account.account_id] = (
                self.grant_revisions.get(account.account_id, 0) + 1
            )

    async def connections(self, account: DashboardAccount) -> dict:
        """List only the current local account's non-secret binding metadata.

        Args:
            account: Authenticated local Dashboard account.

        Returns:
            The live local switch and saved direct connections without keys.
        """
        async with self.db.get_db() as connection:
            rows = (
                (
                    await connection.execute(
                        select(RemoteChatConnection).where(
                            RemoteChatConnection.account_id == account.account_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {
            "enabled": self.config.get("chatui", {}).get("remote_sessions_enabled")
            is True,
            "connections": [
                {
                    "connection_id": row.connection_id,
                    "name": row.name,
                    "base_url": row.base_url,
                    "revision": row.revision,
                }
                for row in rows
            ],
        }

    async def save_connection(
        self,
        account: DashboardAccount,
        name: str,
        base_url: str,
        key: str | None,
        connection_id: str | None = None,
    ) -> str:
        """Save a direct HTTP origin and encrypt its dedicated credential at rest.

        Args:
            account: Authenticated local account that owns this binding.
            name: User-visible connection name.
            base_url: HTTP(S) origin, including private/LAN addresses.
            key: Replacement dedicated credential, or None to retain it.
            connection_id: Existing binding to update, or None for creation.

        Returns:
            Stable connection identity, independent of its display name.

        Raises:
            ApiError: The switch, binding, address or credential is invalid.
        """
        self.require_enabled()
        try:
            url = urlsplit(base_url.strip())
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.path not in {"", "/"}
                or url.query
                or url.fragment
                or any(char.isspace() or ord(char) < 32 for char in base_url)
                or "\\" in base_url
            ):
                raise ValueError
            url.port
            origin = str(httpx.URL(base_url.strip())).rstrip("/")
        except (ValueError, httpx.InvalidURL):
            raise ApiError(
                "Use an HTTP(S) server origin without credentials, path, query or fragment",
                status_code=400,
            ) from None
        if not name.strip() or (
            key is not None
            and (
                not key.startswith("arc_")
                or not key.isascii()
                or any(char.isspace() or ord(char) < 33 for char in key)
            )
        ):
            raise ApiError(
                "A name and dedicated RemoteSession key are required", status_code=400
            )
        async with self.db.get_db() as connection:
            row = (
                await connection.get(RemoteChatConnection, connection_id)
                if connection_id
                else None
            )
            if connection_id and (row is None or row.account_id != account.account_id):
                raise ApiError("Connection is unavailable", status_code=404)
            if row is None:
                if key is None:
                    raise ApiError(
                        "A dedicated RemoteSession key is required", status_code=400
                    )
                row = RemoteChatConnection(
                    account_id=account.account_id,
                    name=name.strip(),
                    base_url=origin,
                    encrypted_key="",
                )
            if connection_id:
                row.revision = secrets.token_hex(16)
            row.name, row.base_url = name.strip(), origin
            if key is not None:
                cipher = Fernet(
                    base64.urlsafe_b64encode(
                        hashlib.sha256(
                            self.config["dashboard"]["jwt_secret"].encode()
                        ).digest()
                    )
                )
                row.encrypted_key = cipher.encrypt(key.encode()).decode()
            connection.add(row)
            await connection.commit()
            return row.connection_id

    async def delete_connection(
        self, account: DashboardAccount, connection_id: str
    ) -> None:
        """Delete an owned outbound binding without modifying remote resources.

        Args:
            account: Authenticated local account.
            connection_id: Binding owned by this account.

        Raises:
            ApiError: Connections are disabled or the binding is unavailable.
        """
        self.require_enabled()
        async with self.db.get_db() as connection:
            row = await connection.get(RemoteChatConnection, connection_id)
            if row is None or row.account_id != account.account_id:
                raise ApiError("Connection is unavailable", status_code=404)
            await connection.delete(row)
            await connection.commit()

    async def read_connection(
        self,
        account: DashboardAccount,
        connection_id: str,
        path: str,
        params: dict | None = None,
        *,
        method: str = "GET",
        payload: dict | None = None,
        revision: str | None = None,
        stream: bool = False,
    ):
        """Exchange a fixed protocol resource with bounded, nonredirecting I/O.

        Args:
            account: Authenticated local account, never a remote caller.
            connection_id: Direct binding owned by this local account.
            path: Protocol path constructed by a local route, never a caller URL.
            params: Validated history paging parameters.
            method: Fixed HTTP operation selected by the local route.
            payload: Validated text message, never arbitrary execution options.
            revision: Binding revision shown to the caller when submitting input.
            stream: Return a continuously authorized result subscription.

        Returns:
            Remote success data, without HTTP headers or credential metadata.

        Raises:
            ApiError: Binding, local switch, network, protocol or remote grant failed.
                Remote failures never use local HTTP 401 (which means login expired).
        """
        self.require_enabled()
        async with self.db.get_db() as connection:
            row = await connection.get(RemoteChatConnection, connection_id)
        if row is None or row.account_id != account.account_id:
            raise ApiError("Connection is unavailable", status_code=404)
        if revision is not None and row.revision != revision:
            raise ApiError(
                "Connection changed",
                status_code=409,
                data={"code": "remote_unavailable"},
            )
        try:
            cipher = Fernet(
                base64.urlsafe_b64encode(
                    hashlib.sha256(
                        self.config["dashboard"]["jwt_secret"].encode()
                    ).digest()
                )
            )
            key = cipher.decrypt(row.encrypted_key.encode()).decode()
        except InvalidToken:
            raise ApiError(
                "Replace this connection's key",
                status_code=409,
                data={"code": "remote_unauthorized"},
            ) from None
        if stream:
            return self.connection_events(account, row, key, path)
        try:
            with anyio.fail_after(15):
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    trust_env=False,
                    timeout=httpx.Timeout(10, connect=2),
                ) as client:
                    async with client.stream(
                        method,
                        row.base_url + "/api/v1/remote-chat/" + path,
                        params=params,
                        json=payload,
                        headers={
                            "Authorization": "RemoteSession " + key,
                            "Accept": "application/json",
                        },
                    ) as response:
                        if response.status_code != 200:
                            code = {
                                401: "remote_unauthorized",
                                403: "remote_disabled",
                                404: "remote_unavailable",
                                400: "remote_rejected",
                                422: "remote_rejected",
                            }.get(
                                response.status_code,
                                "remote_acceptance_unknown"
                                if method == "POST"
                                else "remote_protocol_error",
                            )
                            raise ApiError(
                                "Remote connection request failed",
                                status_code=502,
                                data={"code": code},
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 32 * 1024 * 1024:
                                raise ValueError
                        payload = json.loads(body)
                        if (
                            not isinstance(payload, dict)
                            or payload.get("status") != "ok"
                            or "data" not in payload
                        ):
                            raise ValueError
            self.require_enabled()
            async with self.db.get_db() as connection:
                current = await connection.get(RemoteChatConnection, connection_id)
                current_account = await connection.get(
                    DashboardAccount, account.account_id
                )
            if (
                current is None
                or current.revision != row.revision
                or current.base_url != row.base_url
                or current.encrypted_key != row.encrypted_key
                or current_account is None
                or not current_account.enabled
                or current_account.token_version != account.token_version
            ):
                raise ApiError(
                    "Connection changed during request",
                    status_code=409,
                    data={
                        "code": "remote_acceptance_unknown"
                        if method == "POST"
                        else "remote_unavailable"
                    },
                )
            return payload["data"]
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ApiError(
                "Remote server is unreachable",
                status_code=502,
                data={"code": "remote_offline"},
            ) from None
        except (httpx.HTTPError, TimeoutError):
            raise ApiError(
                "Remote server is unreachable",
                status_code=502,
                data={
                    "code": "remote_acceptance_unknown"
                    if method == "POST"
                    else "remote_offline"
                },
            ) from None

        except (ValueError, TypeError):
            raise ApiError(
                "Remote server does not support the connection protocol",
                status_code=502,
                data={
                    "code": "remote_acceptance_unknown"
                    if method == "POST"
                    else "remote_protocol_error"
                },
            ) from None

    async def connection_events(
        self, account: DashboardAccount, row: RemoteChatConnection, key: str, path: str
    ) -> AsyncIterator[str]:
        """Forward a direct subscription while checking local admission when idle.

        Args:
            account: Local authenticated account at admission.
            row: Immutable snapshot of the direct binding.
            key: Decrypted dedicated credential, never included in events.
            path: Fixed remote session event path.

        Yields:
            Bounded SSE frames, or a sanitized terminal access error.
        """
        pending = None
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(10, read=5),
            ) as client:
                async with client.stream(
                    "GET",
                    row.base_url + "/api/v1/remote-chat/" + path,
                    headers={"Authorization": "RemoteSession " + key},
                ) as response:
                    if response.status_code != 200:
                        code = {
                            401: "remote_unauthorized",
                            403: "remote_disabled",
                            404: "remote_unavailable",
                        }.get(response.status_code, "remote_protocol_error")
                        raise ApiError("Remote stream unavailable", data={"code": code})
                    chunks = response.aiter_bytes()
                    pending = asyncio.create_task(anext(chunks))
                    buffer = bytearray()
                    while True:
                        await asyncio.wait({pending}, timeout=0.25)
                        self.require_enabled()
                        with anyio.CancelScope(shield=True):
                            async with self.db.get_db() as connection:
                                current = await connection.get(
                                    RemoteChatConnection, row.connection_id
                                )
                                current_account = await connection.get(
                                    DashboardAccount, account.account_id
                                )
                        if (
                            current is None
                            or current.revision != row.revision
                            or current.account_id != account.account_id
                            or current_account is None
                            or not current_account.enabled
                            or current_account.token_version != account.token_version
                        ):
                            raise ApiError(
                                "Connection changed",
                                data={"code": "remote_unavailable"},
                            )
                        if not pending.done():
                            continue
                        try:
                            chunk = pending.result()
                        except StopAsyncIteration:
                            return
                        buffer.extend(chunk)
                        if len(buffer) > 32 * 1024 * 1024:
                            raise ValueError
                        while (boundary := buffer.find(b"\n\n")) >= 0:
                            frame = bytes(buffer[: boundary + 2])
                            del buffer[: boundary + 2]
                            yield frame
                        pending = asyncio.create_task(anext(chunks))
        except (ApiError, httpx.HTTPError, ValueError) as exc:
            code = (
                (exc.data or {}).get("code", "remote_disabled")
                if isinstance(exc, ApiError)
                else "remote_offline"
            )
            yield "data: " + json.dumps({"type": "access_error", "code": code}) + "\n\n"
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
