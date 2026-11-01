"""Request-local ownership for administrative WebChat resource views."""

from contextvars import ContextVar
from dataclasses import dataclass

webchat_resource_owner: ContextVar[str | None] = ContextVar(
    "webchat_resource_owner", default=None
)

webchat_account_role: ContextVar[str | None] = ContextVar(
    "webchat_account_role", default=None
)


async def visible_webchat_origin(umo: str, db) -> bool:
    """Check whether a session origin belongs in the current daily view.

    Args:
        umo: Stored or requested unified message origin.
        db: Database used to verify the origin's actual platform session.

    Returns:
        Whether the origin is outside WebChat or has the current owner.
    """
    owner = webchat_resource_owner.get()
    if owner is None or not umo.startswith("webchat:"):
        return True
    prefix = "webchat:FriendMessage:"
    if not umo.startswith(prefix):
        return False
    origin = umo.removeprefix(prefix)
    session_id = origin.rsplit("!", 1)[-1] if origin.startswith("webchat!") else origin
    session = await db.get_platform_session_by_id(session_id)
    return (
        session is not None
        and session.platform_id == "webchat"
        and session.creator == owner
        and origin in {session.session_id, f"webchat!{owner}!{session.session_id}"}
    )


async def require_visible_origins(value: object, db) -> None:
    """Reject foreign WebChat origins before a batch starts mutating data.

    Args:
        value: Request payload or batch of stored origins.
        db: Database used to verify WebChat ownership.

    Raises:
        PermissionError: A nested origin belongs to another account.
    """
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and not await visible_webchat_origin(item, db):
            raise PermissionError("Resource is unavailable")


@dataclass(frozen=True)
class DashboardPrincipal:
    """Trusted runtime identity containing no credentials or recovery fields."""

    account_id: str
    resource_owner: str
    role: str
    login_name: str


# Only server authentication constructs this object; never forward client JSON.
dashboard_account_context: ContextVar[DashboardPrincipal | None] = ContextVar(
    "dashboard_account_context", default=None
)
