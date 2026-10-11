"""事件总线, 用于处理事件的分发和处理
事件总线是一个异步队列, 用于接收各种消息事件, 并将其发送到Scheduler调度器进行处理
其中包含了一个无限循环的调度函数, 用于从事件队列中获取新的事件, 并创建一个新的异步任务来执行管道调度器的处理逻辑

class:
    EventBus: 事件总线, 用于处理事件的分发和处理

工作流程:
1. 维护一个异步队列, 来接受各种消息事件
2. 无限循环的调度函数, 从事件队列中获取新的事件, 打印日志并创建一个新的异步任务来执行管道调度器的处理逻辑
"""

import asyncio
import copy
from asyncio import Queue
from contextvars import copy_context

from astrbot.core import logger
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.pipeline.scheduler import PipelineScheduler

from .platform import AstrMessageEvent


class EventBus:
    """用于处理事件的分发和处理"""

    def __init__(
        self,
        event_queue: Queue,
        pipeline_scheduler_mapping: dict[str, PipelineScheduler],
        astrbot_config_mgr: AstrBotConfigManager,
    ) -> None:
        self.event_queue = event_queue  # 事件队列
        # abconf uuid -> scheduler
        self.pipeline_scheduler_mapping = pipeline_scheduler_mapping
        self.astrbot_config_mgr = astrbot_config_mgr
        # 持有正在执行的 pipeline 任务的强引用, 防止 task 在 pending 状态被 GC 回收
        self._pending_tasks: set[asyncio.Task] = set()

    async def dispatch(self) -> None:
        while True:
            event: AstrMessageEvent = await self.event_queue.get()
            # Agent team node turns (spec §2.3) carry a per-turn execution
            # token. Resolve it BEFORE the usual umo-based routing so the turn
            # runs on the binding's own config profile scheduler. Expired or
            # foreign tokens must not start a replacement default invocation.
            scheduler = None
            binding = None
            token = event.get_extra("execution_token")
            if isinstance(token, str) and token:
                from astrbot.core.agent_team_execution import (
                    AgentTeamExecutionRegistry,
                )

                binding = AgentTeamExecutionRegistry.resolve(
                    token, umo=event.unified_msg_origin
                )
                if binding is not None:
                    binding.message_id = str(event.message_obj.message_id)
                    binding.source_umo = event.unified_msg_origin
                    event.unified_msg_origin = binding.umo
                    event.set_extra("agent_team_execution", binding)
                    if binding.config_id:
                        scheduler = self.pipeline_scheduler_mapping.get(
                            binding.config_id
                        )
                if binding is None or (binding.config_id and scheduler is None):
                    logger.warning(
                        "Agent team invocation expired, foreign, or missing its profile (umo=%s).",
                        event.unified_msg_origin,
                    )
                    from astrbot.core.platform.sources.webchat.webchat_event import (
                        WebChatMessageEvent,
                    )

                    if isinstance(event, WebChatMessageEvent):
                        await event.send(
                            event.plain_result(
                                "Team invocation is no longer available."
                            )
                        )
                        await event.send(None)
                    continue
            conf_info = self.astrbot_config_mgr.get_conf_info(event.unified_msg_origin)
            conf_id = conf_info["id"]
            conf_name = conf_info.get("name") or conf_id
            if binding is not None:
                binding.config_id = binding.config_id or conf_id
                conf_name = binding.config_id
            self._print_event(event, conf_name)
            if scheduler is None:
                scheduler = self.pipeline_scheduler_mapping.get(conf_id)
            if not scheduler:
                logger.error(
                    f"PipelineScheduler not found for id: {conf_id}, event ignored."
                )
                continue
            if binding is not None and binding.profile_snapshot is not None:
                from astrbot.core.pipeline.context import PipelineContext

                # A v2 run owns its settings, while providers and plugin services
                # remain live references subject to current authorization.
                frozen_config = dict.__new__(type(scheduler.ctx.astrbot_config))
                frozen_config.__dict__.update(vars(scheduler.ctx.astrbot_config))
                frozen_config.update(copy.deepcopy(dict(scheduler.ctx.astrbot_config)))
                frozen_config.update(copy.deepcopy(binding.profile_snapshot))
                scheduler = PipelineScheduler(
                    PipelineContext(
                        astrbot_config=frozen_config,
                        plugin_manager=scheduler.ctx.plugin_manager,
                        astrbot_config_id=binding.config_id or conf_id,
                        db_helper=scheduler.ctx.db_helper,
                    )
                )
                await scheduler.initialize()
            from astrbot.core.agent_team_execution import current_team_execution

            execution_context = copy_context()
            execution_context.run(current_team_execution.set, binding)
            task = execution_context.run(asyncio.create_task, scheduler.execute(event))
            self._pending_tasks.add(task)
            task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """pipeline 任务结束回调: 移除强引用并暴露未捕获的异常"""
        self._pending_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Pipeline task failed.", exc_info=exc)

    def _print_event(self, event: AstrMessageEvent, conf_name: str) -> None:
        """用于记录事件信息

        Args:
            event (AstrMessageEvent): 事件对象

        """
        # 如果有发送者名称: [平台名] 发送者名称/发送者ID: 消息概要
        if event.get_sender_name():
            logger.info(
                f"[{conf_name}] [{event.get_platform_id()}({event.get_platform_name()})] {event.get_sender_name()}/{event.get_sender_id()}: {event.get_message_outline()}",
                extra={"category": "user_chat"},
            )
        # 没有发送者名称: [平台名] 发送者ID: 消息概要
        else:
            logger.info(
                f"[{conf_name}] [{event.get_platform_id()}({event.get_platform_name()})] {event.get_sender_id()}: {event.get_message_outline()}",
                extra={"category": "user_chat"},
            )
