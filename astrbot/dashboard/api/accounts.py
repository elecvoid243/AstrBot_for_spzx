"""Administrator-managed ordinary accounts; no registration or role assignment."""

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
