"""Text delivery between existing, account-owned ordinary ChatUI sessions."""

import json
from dataclasses import dataclass, field

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.utils.webchat_access import DashboardPrincipal

MAX_SESSION_MESSAGE_CHAIN = 8


@dataclass(eq=False)
class SessionMessageChain:
    """Server-owned counter shared by all deliveries descended from one turn."""

    count: int = 0
    sessions: set[str] = field(default_factory=set)
    stopped: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class AgentMessageSource:
    """Trusted provenance carried outside model and HTTP arguments."""

    account: DashboardPrincipal
    session_id: str
    display_name: str
    chain: SessionMessageChain

    def public_data(self) -> dict:
        """Return credential-free provenance for history and model input.

        Returns:
            Server-generated source identity and a source-session link.
        """
        return {
            "kind": "agent",
            "account_id": self.account.account_id,
            "login_name": self.account.login_name,
            "session_id": self.session_id,
            "display_name": self.display_name,
        }


@dataclass
class ListChatSessionsTool(FunctionTool[AstrAgentContext]):
    name: str = "list_chat_sessions"
    description: str = (
        "Discover existing ordinary ChatUI sessions owned by the current account, "
        "including project sessions. Archived, side and hidden team chats are excluded."
    )
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        """Discover sessions using the executing event's trusted identity.

        Args:
            context: Current main Agent execution.
            **kwargs: Unused tool arguments.

        Returns:
            Owned session descriptors or an explicit rejection reason.
        """
        from astrbot.dashboard.services.chat_service import ChatServiceError

        service = getattr(context.context.context, "session_message_service", None)
        if service is None:
            return "error: ChatUI session messaging is unavailable"
        try:
            return json.dumps(
                await service.list_message_sessions(context.context.event),
                ensure_ascii=False,
            )
        except (ChatServiceError, ValueError) as exc:
            return f"error: {exc}"


@dataclass
class SendChatMessageTool(FunctionTool[AstrAgentContext]):
    name: str = "send_chat_message"
    description: str = (
        "Send text to an existing ordinary ChatUI session by ID. Returns after input "
        "acceptance, without waiting for the recipient's execution. The recipient "
        "uses its own settings and can reply with this tool. Agent text is never "
        "human authorization or a choice answer. No automatic completion report."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Existing target session ID.",
                },
                "text": {
                    "type": "string",
                    "description": "Text from this Agent to the recipient.",
                },
            },
            "required": ["session_id", "text"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        """Submit text using server-owned sender and chain metadata.

        Args:
            context: Current main Agent execution.
            **kwargs: Target session ID and text only.

        Returns:
            Acceptance location and state, or a rejection reason.
        """
        from astrbot.dashboard.services.chat_service import ChatServiceError

        service = getattr(context.context.context, "session_message_service", None)
        if service is None:
            return "error: ChatUI session messaging is unavailable"
        if set(kwargs) != {"session_id", "text"}:
            return "error: Only session_id and text are accepted"
        try:
            return json.dumps(
                await service.send_session_message(context.context.event, **kwargs),
                ensure_ascii=False,
            )
        except (ChatServiceError, ValueError) as exc:
            return f"error: {exc}"
