"""Creation and text delivery for account-owned ordinary ChatUI sessions."""

import asyncio
import json
from dataclasses import dataclass, field

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import (
    FunctionTool,
    ToolExecResult,
    current_tool_invocation,
)
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
    # Reject a user stop that occurs while the recipient input is being prepared.
    target_stop_revision: int = 0

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
        "including project sessions. Each entry returns project_id and project_name "
        "(the ChatUI sidebar folder title, null for no project). Archived, side and "
        "hidden team chats are excluded."
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


@dataclass
class CreateChatSessionTool(FunctionTool[AstrAgentContext]):
    name: str = "create_chat_session"
    description: str = (
        "Create an independent ordinary ChatUI session with empty history. Omitted "
        "project and settings inherit this execution's effective values. Select an existing "
        "owned project directly with project_name (exact ChatUI sidebar folder title, "
        "not a filesystem path), or project_id. These parameters are mutually exclusive. "
        "project_id=null explicitly chooses no project. Ambiguous names require an ID. "
        "Optional text sends the first Agent message through send_chat_message, sharing "
        "its automatic chain budget. Without text, only create. Returns the created ID "
        "even if first-message admission fails; reuse it with send_chat_message."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "New conversation title."},
                "project_id": {
                    "type": ["string", "null"],
                    "description": "Existing owned project ID, or null for no project. Cannot be combined with project_name; omit both to inherit.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Exact existing ChatUI sidebar folder title, including empty folders; surrounding input whitespace is trimmed. Not a filesystem path. Mutually exclusive with project_id. Duplicate names require an ID.",
                },
                "selected_provider": {
                    "type": "string",
                    "description": "Available chat provider ID.",
                },
                "selected_model": {
                    "type": "string",
                    "description": "Model allowed by the provider.",
                },
                "thinking_effort": {
                    "type": "string",
                    "description": "Thinking effort, budget, or auto.",
                },
                "text": {
                    "type": "string",
                    "description": "Optional nonempty first Agent message.",
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        """Create once per real invocation, retaining a result through caller retries.

        Args:
            context: Executing main Agent with server-owned runner and event.
            **kwargs: Title, optional project, settings and first text.

        Returns:
            Created session location and optional send outcome, or an explicit error.
        """
        from astrbot.dashboard.services.chat_service import ChatServiceError

        event = context.context.event
        service = getattr(context.context.context, "session_message_service", None)
        invocation = current_tool_invocation.get()
        runner = context.context.extra.get("main_agent_runner")
        if service is None or runner is None or not invocation:
            return "error: A trusted main Agent tool invocation is required"
        if set(kwargs) - self.parameters["properties"].keys():
            return "error: Unsupported creation arguments"
        # This cache lives only as long as the source execution. Never use a
        # model-provided key or a mutable shared field for parallel tool calls.
        pending = event.get_extra("_created_chat_sessions")
        if pending is None:
            pending = {}
            event.set_extra("_created_chat_sessions", pending)
        if invocation not in pending:
            effective = {
                "selected_provider": runner.provider.provider_config["id"],
                "selected_model": runner.req.model or runner.provider.get_model(),
                "thinking_effort": runner.req.llm_params.get("thinking_effort")
                or event.get_extra("thinking_effort")
                or "auto",
            }
            pending[invocation] = asyncio.create_task(
                service.create_session_from_agent(event, kwargs, effective)
            )
        try:
            # A tool timeout must not abandon a partly configured conversation.
            return json.dumps(
                await asyncio.shield(pending[invocation]), ensure_ascii=False
            )
        except (ChatServiceError, ValueError) as exc:
            return f"error: {exc}"
