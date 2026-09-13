"""Tests for the workspace-path injection placement in build_main_agent.

The workspace root is session identity: it must NOT live in the system
prompt (which is part of the provider prefix-cache payload), but in a temp
system-reminder attached to the current user message.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from astrbot.core.agent.message import Message, dump_messages_with_checkpoints
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent

CONFIG = {
    "provider_settings": {
        "datetime_system_prompt": True,
        "identifier": False,
        "group_name_display": False,
        "web_search": False,
        "wake_prefix": "",
        "computer_use_runtime": "local",
        "default_personality": "default",
    },
    "agent_runner": {
        "config": {
            "persona": {"safety_mode": True, "safety_mode_strategy": "system_prompt"},
            "misc": {"tool_schema_mode": "full", "max_steps": 30},
            "compression": {},
            "model": {},
        }
    },
    "subagent_orchestrator": {"main_enable": False},
    "kb_agentic_mode": False,
    "timezone": None,
    "provider_ltm_settings": {},
    "proactive_capability": {"add_cron_tools": False},
}


def _make_fake_context():
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.core.star.context import Context

    class FakeContext(Context):
        def __init__(self):
            self._cfg = CONFIG

            tool_mgr = MagicMock()

            def _full_tool_set():
                ts = ToolSet()
                ts.add_tool(
                    FunctionTool(
                        name="astrweb_search",
                        description="Search the web.",
                        parameters={"type": "object", "properties": {}},
                    )
                )
                return ts

            tool_mgr.get_full_tool_set.side_effect = _full_tool_set
            tool_mgr.get_builtin_tool.side_effect = lambda cls: FunctionTool(
                name=getattr(cls, "name", cls.__name__.lower()),
                description="builtin",
                parameters={"type": "object", "properties": {}},
            )
            self._tool_mgr = tool_mgr

            persona_manager = MagicMock()
            persona_manager.resolve_selected_persona = AsyncMock(
                return_value=(
                    "default",
                    {"prompt": "You are a tester.", "_begin_dialogs_processed": []},
                    None,
                    False,
                )
            )
            self.persona_manager = persona_manager

            conv_mgr = MagicMock()
            conv_mgr.get_curr_conversation_id = AsyncMock(return_value="cid-1")
            conv = MagicMock()
            conv.history = "[]"
            conv.persona_id = ""
            conv_mgr.get_conversation = AsyncMock(return_value=conv)
            self.conversation_manager = conv_mgr

            self.subagent_orchestrator = None

        def get_config(self, umo=None):
            return self._cfg

        def get_llm_tool_manager(self):
            return self._tool_mgr

    return FakeContext()


def _make_fake_event():
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.message_type import MessageType

    class FakeEvent(AstrMessageEvent):
        def __init__(self, message_str: str):
            message_obj = MagicMock()
            message_obj.message = []
            message_obj.type = MessageType.FRIEND_MESSAGE
            platform_meta = MagicMock()
            platform_meta.name = "test_platform"
            platform_meta.support_proactive_message = False
            super().__init__(
                message_str=message_str,
                message_obj=message_obj,
                platform_meta=platform_meta,
                session_id="webchat!u1",
            )

        @property
        def unified_msg_origin(self) -> str:
            return "test_platform:FriendMessage:u1"

        def get_platform_name(self):
            return "test_platform"

        def get_sender_name(self) -> str:
            return "tester"

    return FakeEvent("hello")


def _build_request():
    """Run the real build_main_agent with the local computer-use runtime."""
    from unittest.mock import MagicMock as _M

    async def _run():
        with (
            patch(
                "astrbot.core.astr_main_agent._select_provider",
                new=AsyncMock(
                    return_value=_M(
                        provider_config={
                            "id": "p1",
                            "modalities": ["text", "image", "tool_use"],
                        }
                    )
                ),
            ),
            patch(
                "astrbot.core.astr_main_agent.retrieve_knowledge_base",
                new=AsyncMock(return_value=None),
            ),
            patch("astrbot.core.astr_main_agent.SkillManager") as _sm,
        ):
            _sm.return_value.list_skills.return_value = []
            _sm.return_value.list_workspace_skills.return_value = []
            result = await build_main_agent(
                event=_make_fake_event(),
                plugin_context=_make_fake_context(),
                config=MainAgentBuildConfig(
                    tool_call_timeout=120,
                    tool_schema_mode="full",
                    computer_use_runtime="local",
                    llm_safety_mode=True,
                    provider_settings={**CONFIG["provider_settings"]},
                ),
                apply_reset=False,
            )
        result.reset_coro.close()
        return result.provider_request

    return asyncio.run(_run())


def test_workspace_path_not_in_system_prompt():
    req = _build_request()
    assert "Current workspace" not in (req.system_prompt or "")


def test_workspace_path_injected_as_persistent_system_reminder():
    req = _build_request()
    reminder_parts = [
        part
        for part in req.extra_user_content_parts
        if getattr(part, "text", "").startswith("<system_reminder>")
        and "Current workspace" in getattr(part, "text", "")
    ]
    assert len(reminder_parts) == 1
    # Persisted with the message (like the datetime reminder): later requests
    # re-send the exact bytes the provider already cached, so the cross-turn
    # prefix cache stays intact.
    assert reminder_parts[0]._no_save is False


def test_workspace_reminder_persists_into_history():
    """The assembled request carries the reminder in the current user
    message, and the persisted history keeps it so later requests re-send
    the same bytes."""
    req = _build_request()
    assembled = asyncio.run(req.assemble_context())
    message = Message.model_validate(assembled)
    flat = str(message.model_dump())
    assert "Current workspace" in flat

    persisted = dump_messages_with_checkpoints([message])
    assert "Current workspace" in str(persisted)
