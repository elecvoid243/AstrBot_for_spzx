"""Administrator-managed ordinary accounts; no registration or role assignment."""

import asyncio

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from astrbot.core.db.po import DashboardAccount
from astrbot.core.utils.auth_password import (
    hash_dashboard_password,
    validate_dashboard_password,
)
from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.account_cleanup import delete_account_data

from .auth import require_dashboard_user

router = APIRouter(tags=["Accounts"])


class AccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class AccountInfo(BaseModel):
    account_id: str
    username: str
    role: str
    enabled: bool
    resource_owner: str


class AccountPasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=1024)


class AccountListResponse(BaseModel):
    status: str = "ok"
    data: list[AccountInfo]
    message: str | None = None


class AccountResponse(BaseModel):
    status: str = "ok"
    data: AccountInfo
    message: str | None = None


@router.get("/auth/session", response_model=AccountResponse)
async def current_account(
    request: Request, _owner: str = Depends(require_dashboard_user)
):
    account = request.state.dashboard_account
    return ok(AccountInfo.model_validate(account, from_attributes=True).model_dump())


@router.post("/accounts/{account_id}/reset-password", response_model=AccountResponse)
async def reset_account_password(
    account_id: str,
    request: Request,
    payload: AccountPasswordResetRequest,
    _owner: str = Depends(require_dashboard_user),
):
    """Replace an ordinary account's password and revoke its existing sessions.

    Args:
        account_id: Stable identity of the ordinary account.
        request: Authenticated Dashboard request.
        payload: Replacement password, subject to the existing password policy.
        _owner: Current trusted caller's resource identity.

    Returns:
        Updated public account information without credentials.

    Raises:
        ApiError: The caller is not an administrator, the target is not an
            ordinary account, or the password fails validation.
    """
    if request.state.dashboard_account.role != "admin":
        raise ApiError("Administrator permission required", status_code=403)
    try:
        validate_dashboard_password(payload.password)
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    password_hash = hash_dashboard_password(payload.password)
    db = request.app.state.db
    async with db.dashboard_account_lock:
        async with db.get_db() as session:
            account = await session.get(DashboardAccount, account_id)
            if account is None or account.role != "user":
                raise ApiError("Ordinary account not found", status_code=404)
            account.password_hash = password_hash
            account.token_version += 1
            session.add(account)
            await session.commit()
    return ok(AccountInfo.model_validate(account, from_attributes=True).model_dump())


@router.post("/accounts/{account_id}/disable", response_model=AccountResponse)
async def disable_account(
    account_id: str,
    request: Request,
    _owner: str = Depends(require_dashboard_user),
):
    """Disable an ordinary account and revoke its existing sessions atomically.

    Args:
        account_id: Stable identity of the ordinary account.
        request: Authenticated Dashboard request.
        _owner: Current trusted caller's resource identity.

    Returns:
        Updated public account information without credentials.

    Raises:
        ApiError: The caller is not an administrator or the target is not an
            ordinary account.
    """
    if request.state.dashboard_account.role != "admin":
        raise ApiError("Administrator permission required", status_code=403)
    db = request.app.state.db
    async with db.dashboard_account_lock:
        async with db.get_db() as session:
            account = await session.get(DashboardAccount, account_id)
            if account is None or account.role != "user":
                raise ApiError("Ordinary account not found", status_code=404)
            if account.enabled:
                account.enabled = False
                account.token_version += 1
                session.add(account)
                await session.commit()
    return ok(AccountInfo.model_validate(account, from_attributes=True).model_dump())


@router.post("/accounts/{account_id}/enable", response_model=AccountResponse)
async def enable_account(
    account_id: str, request: Request, _owner: str = Depends(require_dashboard_user)
):
    """Enable an ordinary account without reviving previously revoked credentials.

    Args:
        account_id: Stable identity of the ordinary account.
        request: Authenticated Dashboard request.
        _owner: Current trusted caller's resource identity.

    Returns:
        Updated account information retaining the original resource identity.

    Raises:
        ApiError: Caller is not an administrator, target is protected, or deletion
            is currently running.
    """
    if request.state.dashboard_account.role != "admin":
        raise ApiError("Administrator permission required", status_code=403)
    db = request.app.state.db
    async with db.dashboard_account_lock:
        if account_id in request.app.state.deleting_accounts:
            raise ApiError("Account deletion is in progress", status_code=409)
        async with db.get_db() as session:
            account = await session.get(DashboardAccount, account_id)
            if (
                account is None
                or account.role != "user"
                or account_id == request.state.dashboard_account.account_id
            ):
                raise ApiError("Ordinary account not found", status_code=404)
            if not account.enabled:
                account.enabled = True
                account.token_version += 1
                session.add(account)
                await session.commit()
    return ok(AccountInfo.model_validate(account, from_attributes=True).model_dump())


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: str, request: Request, _owner: str = Depends(require_dashboard_user)
):
    """Permanently remove an ordinary account and its owned ChatUI data.

    Args:
        account_id: Stable identity of the ordinary account.
        request: Authenticated Dashboard request.
        _owner: Current trusted caller's resource identity.

    Returns:
        Success only after cleanup and login deletion have completed.

    Raises:
        ApiError: Caller or target is protected, deletion is already running,
            or cleanup failed and must be retried with the account disabled.
    """
    if request.state.dashboard_account.role != "admin":
        raise ApiError("Administrator permission required", status_code=403)
    state = request.app.state
    async with state.db.dashboard_account_lock:
        if account_id in state.deleting_accounts:
            raise ApiError("Account deletion is in progress", status_code=409)
        async with state.db.get_db() as session:
            account = await session.get(DashboardAccount, account_id)
            if (
                account is None
                or account.role != "user"
                or account_id == request.state.dashboard_account.account_id
            ):
                raise ApiError("Ordinary account not found", status_code=404)
            account.enabled = False
            account.token_version += 1
            session.add(account)
            await session.commit()
        state.deleting_accounts.add(account_id)
    cleanup = asyncio.create_task(delete_account_data(request.app, account))
    try:
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # A disconnected administrator must not release the deletion guard
            # while a filesystem worker or cancellation finalizer still runs.
            await cleanup
            raise
    except Exception as exc:
        from astrbot import logger

        logger.error("Failed to delete ordinary account %s: %s", account_id, exc)
        raise ApiError(
            "Account cleanup failed. The account remains disabled; retry deletion.",
            status_code=500,
        ) from exc
    finally:
        state.deleting_accounts.discard(account_id)
    return ok()


@router.get(
    "/accounts",
    response_model=AccountListResponse,
)
async def list_accounts(
    request: Request, _owner: str = Depends(require_dashboard_user)
):
    if request.state.dashboard_account.role != "admin":
        raise ApiError("Administrator permission required", status_code=403)
    async with request.app.state.db.get_db() as session:
        accounts = (
            (
                await session.execute(
                    select(DashboardAccount)
                    .where(DashboardAccount.role == "user")
                    .order_by(DashboardAccount.username)
                )
            )
            .scalars()
            .all()
        )
    return ok(
        [
            AccountInfo.model_validate(a, from_attributes=True).model_dump()
            for a in accounts
        ]
    )


@router.post("/accounts", response_model=AccountResponse)
async def create_account(
    request: Request,
    payload: AccountCreateRequest,
    _owner: str = Depends(require_dashboard_user),
):
    if request.state.dashboard_account.role != "admin":
        raise ApiError("Administrator permission required", status_code=403)
    username = payload.username.strip()
    if len(username) < 3:
        raise ApiError(
            "Username must contain at least three characters", status_code=400
        )
    try:
        validate_dashboard_password(payload.password)
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    db = request.app.state.db
    # ensure_admin acquires this non-reentrant lock itself.
    await request.app.state.services.auth.accounts.ensure_admin()
    async with db.dashboard_account_lock:
        account = DashboardAccount(
            username=username, password_hash=hash_dashboard_password(payload.password)
        )
        async with db.get_db() as session:
            session.add(account)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ApiError("Username is already in use", status_code=409) from exc
    return ok(AccountInfo.model_validate(account, from_attributes=True).model_dump())
