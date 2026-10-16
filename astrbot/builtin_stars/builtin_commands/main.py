import json
from typing import cast

from pydantic import ValidationError

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.agent_teams.chat import ChatCaller
from astrbot.core.star.filter.command import GreedyStr

from .commands import (
    AdminCommands,
    ConversationCommands,
    HelpCommand,
    NameCommand,
    ProviderCommands,
    SetUnsetCommands,
    SIDCommand,
)


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context

        self.admin_c = AdminCommands(self.context)
        self.conversation_c = ConversationCommands(self.context)
        self.help_c = HelpCommand(self.context)
        self.name_c = NameCommand(self.context)
        self.provider_c = ProviderCommands(self.context)
        self.setunset_c = SetUnsetCommands(self.context)
        self.sid_c = SIDCommand(self.context)

    @filter.command("help")
    async def help(self, event: AstrMessageEvent) -> None:
        """查看帮助信息"""
        await self.help_c.help(event)

    @filter.command("sid")
    async def sid(self, event: AstrMessageEvent) -> None:
        """获取会话 ID 及其他相关信息"""
        await self.sid_c.sid(event)

    @filter.command("asset")
    async def asset(self, event: AstrMessageEvent, request: GreedyStr) -> None:
        """Explicitly invoke an opened asset or inspect its returned result.

        Args:
            event: Actual platform sender and source conversation.
            request: Asset name/ID followed by optional pipe and JSON text inputs.
        """
        if event.get_extra("agent_team_execution") is not None:
            return
        context = cast(star.Context, self.context)
        service = context.workflow_service
        if service is None:
            await event.send(event.plain_result("Asset invocation is unavailable."))
            event.stop_event()
            return
        try:
            caller = ChatCaller(
                platform_id=str(event.get_platform_id()),
                sender_id=str(event.get_sender_id()),
                source=event.unified_msg_origin,
                profile_id=context.astrbot_config_mgr.get_conf_info(
                    event.unified_msg_origin
                )["id"],
            )
            # CommandFilter normalizes whitespace for argument matching. Read
            # the original event tail so quoted JSON input keeps its exact text.
            command_parts = event.get_message_str().split(maxsplit=1)
            request = GreedyStr(
                command_parts[1].strip() if len(command_parts) > 1 else ""
            )

            async def send_source(actual: ChatCaller, text: str) -> bool:
                """Use this verified event for retained source delivery.

                Args:
                    actual: Caller verified against the saved grant.
                    text: Safe status or final result projection.

                Returns:
                    True after the platform accepted this message.
                """
                await event.send(event.plain_result(text))
                return True

            if not request or request == "list":
                offered = await service.chat.list_assets(caller)
                text = 'Usage: /asset <name or ID> | {"input":"value"}\n'
                text += json.dumps(offered, ensure_ascii=False)
                await event.send(event.plain_result(text))
            elif request == "whoami":
                await event.send(
                    event.plain_result(
                        json.dumps(caller.model_dump(), ensure_ascii=False)
                    )
                )
            elif request.startswith("status "):
                run_id = request.removeprefix("status ").strip()
                await service.chat.get_run(caller, run_id)

                delivered = await service.chat.deliver_pending(send_source, run_id)
                if not delivered:
                    projection = await service.chat.get_run(caller, run_id)
                    text = json.dumps(projection, ensure_ascii=False)
                    for offset in range(0, len(text), 1500):
                        await event.send(
                            event.plain_result(text[offset : offset + 1500])
                        )
            else:
                selector, separator, raw_inputs = request.partition("|")
                inputs = json.loads(raw_inputs) if separator else {}
                if not isinstance(inputs, dict) or any(
                    not isinstance(v, str) for v in inputs.values()
                ):
                    raise ValueError("Inputs must be a JSON object of text values")
                response = await service.chat.invoke(caller, selector.strip(), inputs)
                if response["status"] == "accepted":
                    await service.chat.deliver_pending(send_source, response["run_id"])
                else:
                    await event.send(
                        event.plain_result(json.dumps(response, ensure_ascii=False))
                    )
        except (RoleAssetError, ValueError, ValidationError) as exc:
            await event.send(event.plain_result(str(exc)))
        event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("name")
    async def name(self, event: AstrMessageEvent, alias: GreedyStr) -> None:
        """Set display name for current UMO"""
        await self.name_c.name(event, alias)

    @filter.command("reset")
    @filter.permission_type(filter.PermissionType.SHARED_GROUP_ADMIN)
    async def reset(self, message: AstrMessageEvent) -> None:
        """开始新对话，保留之前的历史。

        Args:
            message: 命令事件，标识会话与发送者。
        """
        await self.conversation_c.new_conv(message)

    @filter.command("stop")
    async def stop(self, message: AstrMessageEvent) -> None:
        """停止 Agent 执行"""
        await self.conversation_c.stop(message)

    @filter.command("new")
    @filter.permission_type(filter.PermissionType.SHARED_GROUP_ADMIN)
    async def new_conv(self, message: AstrMessageEvent) -> None:
        """开始新对话，保留之前的历史。

        Args:
            message: 命令事件，标识会话与发送者。
        """
        await self.conversation_c.new_conv(message)

    @filter.command("stats")
    async def stats(self, message: AstrMessageEvent) -> None:
        """显示当前会话的 Token 用量统计"""
        await self.conversation_c.stats(message)

    @filter.command("context")
    async def context(self, message: AstrMessageEvent) -> None:
        """显示当前上下文窗口的 Token 占用"""
        await self.conversation_c.cmd_context(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("provider")
    async def provider(
        self,
        event: AstrMessageEvent,
        idx: str | int | None = None,
        idx2: int | None = None,
    ) -> None:
        """查看或切换 LLM Provider"""
        await self.provider_c.provider(event, idx, idx2)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("dashboard_update")
    async def update_dashboard(self, event: AstrMessageEvent) -> None:
        """更新 AstrBot WebUI"""
        await self.admin_c.update_dashboard(event)

    @filter.command("set")
    async def set_variable(self, event: AstrMessageEvent, key: str, value: str) -> None:
        """设置会话变量"""
        await self.setunset_c.set_variable(event, key, value)

    @filter.command("unset")
    async def unset_variable(self, event: AstrMessageEvent, key: str) -> None:
        """移除会话变量"""
        await self.setunset_c.unset_variable(event, key)
