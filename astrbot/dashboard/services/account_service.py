from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import DashboardAccount
from astrbot.dashboard.password_state import (
    get_dashboard_password_hash,
    is_password_storage_upgraded,
)


class AccountService:
    """Persist identities while preserving the original admin recovery config."""

    def __init__(self, db: BaseDatabase, config: AstrBotConfig) -> None:
        self.db = db
        self.config = config

    async def ensure_admin(self) -> DashboardAccount:
        """Idempotently import the configured administrator and security updates.

        The original administrator's credential configuration remains the
        recovery source of truth. Its frozen resource owner survives renames;
        no session, UMO, project, history or attachment references are rewritten.

        Returns:
            The unique migrated administrator, including its stable owner.

        Raises:
            IntegrityError: The configured login collides with another account.
        """
        async with self.db.dashboard_account_lock:
            username = self.config["dashboard"]["username"]
            password_hash = get_dashboard_password_hash(
                self.config,
                upgraded=await is_password_storage_upgraded(self.db, self.config),
            )
            async with self.db.get_db() as session:
                account = (
                    await session.execute(
                        select(DashboardAccount).where(
                            DashboardAccount.bootstrap_key == "dashboard"
                        )
                    )
                ).scalar_one_or_none()
                if account is None:
                    account = DashboardAccount(
                        username=username,
                        password_hash=password_hash,
                        role="admin",
                        legacy_owner=username,
                        bootstrap_key="dashboard",
                    )
                    session.add(account)
                    try:
                        await session.commit()
                    except IntegrityError:
                        # Concurrent first requests may attempt the same import.
                        await session.rollback()
                        account = (
                            await session.execute(
                                select(DashboardAccount).where(
                                    DashboardAccount.bootstrap_key == "dashboard"
                                )
                            )
                        ).scalar_one_or_none()
                        if account is None:
                            raise
                if getattr(self.db, "restore_recovery_pending", False):
                    # An interrupted restore may have committed config before its
                    # DB transaction. In recovery only, the persisted bootstrap
                    # identity supplies credentials instead of being overwritten.
                    dashboard = self.config["dashboard"]
                    dashboard["username"] = account.username
                    upgraded = account.password_hash.startswith("pbkdf2_sha256$")
                    dashboard["pbkdf2_password"] = (
                        account.password_hash if upgraded else ""
                    )
                    if not upgraded:
                        dashboard["password"] = account.password_hash
                    dashboard["password_storage_upgraded"] = upgraded
                    self.config.save_config()
                    return account
                if (
                    account.username != username
                    or account.password_hash != password_hash
                ):
                    account.username = username
                    account.password_hash = password_hash
                    account.token_version += 1
                    session.add(account)
                    await session.commit()
                return account

    async def resolve(self, payload: dict) -> DashboardAccount | None:
        """Resolve signed claims against current persisted account state.

        Args:
            payload: JWT claims after signature and expiry verification.

        Returns:
            An enabled account with matching credential version, or None.
        """
        admin = await self.ensure_admin()
        account_id = payload.get("sub")
        if account_id is None:
            # Username-only tokens are an upgrade bridge for this one account,
            # never a way to grant arbitrary signed usernames admin access.
            if (
                payload.get("username") == admin.username
                and admin.token_version == 0
                and admin.enabled
                and admin.role == "admin"
            ):
                return admin
            return None
        if not isinstance(account_id, str):
            return None
        async with self.db.get_db() as session:
            account = await session.get(DashboardAccount, account_id)
        if (
            account is None
            or not account.enabled
            or type(payload.get("ver")) is not int
            or payload["ver"] != account.token_version
            or account.role not in {"admin", "user"}
        ):
            return None
        return account
