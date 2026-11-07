"""Expose session messaging through ordinary plugin tool controls."""

from astrbot.api import star
from astrbot.builtin_stars.session_messages.tools import (
    CreateChatSessionTool,
    ListChatSessionsTool,
    ReadChatSessionTool,
    SendChatMessageTool,
)


class Main(star.Star):
    async def initialize(self) -> None:
        """Register tools with existing activation and permission controls."""
        self.context.add_llm_tools(
            ListChatSessionsTool(),
            ReadChatSessionTool(),
            SendChatMessageTool(),
            CreateChatSessionTool(),
        )
