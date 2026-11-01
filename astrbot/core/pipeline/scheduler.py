from collections.abc import AsyncGenerator
from typing import cast

from astrbot.core import logger
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.platform.sources.webchat.webchat_event import WebChatMessageEvent
from astrbot.core.platform.sources.wecom_ai_bot.wecomai_event import (
    WecomAIBotMessageEvent,
)
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.webchat_access import (
    DashboardPrincipal,
    dashboard_account_context,
    webchat_account_role,
    webchat_resource_owner,
)

from .bootstrap import ensure_builtin_stages_registered
from .context import PipelineContext
from .stage import Stage, registered_stages
from .stage_order import STAGES_ORDER


class PipelineScheduler:
    """管道调度器，负责调度各个阶段的执行"""

    def __init__(self, context: PipelineContext) -> None:
        ensure_builtin_stages_registered()
        registered_stages.sort(
            key=lambda x: STAGES_ORDER.index(x.__name__),
        )  # 按照顺序排序
        self.ctx = context  # 上下文对象
        self.stages: list[Stage] = []  # 存储阶段实例

    async def initialize(self) -> None:
        """初始化管道调度器时, 初始化所有阶段"""
        for stage_cls in registered_stages:
            stage_instance = stage_cls()  # 创建实例
            await stage_instance.initialize(self.ctx)
            self.stages.append(stage_instance)

    async def _process_stages(self, event: AstrMessageEvent, from_stage=0) -> None:
        """依次执行各个阶段

        Args:
            event (AstrMessageEvent): 事件对象
            from_stage (int): 从第几个阶段开始执行, 默认从0开始

        """
        for i in range(from_stage, len(self.stages)):
            stage = self.stages[i]  # 获取当前要执行的阶段
            # logger.debug(f"执行阶段 {stage.__class__.__name__}")
            coroutine = stage.process(
                event,
            )  # 调用阶段的process方法, 返回协程或者异步生成器

            if isinstance(coroutine, AsyncGenerator):
                # 如果返回的是异步生成器, 实现洋葱模型的核心
                agen = cast(AsyncGenerator[None], coroutine)
                async for _ in agen:
                    # 此处是前置处理完成后的暂停点(yield), 下面开始执行后续阶段
                    if event.is_stopped():
                        logger.debug(
                            f"Stage {stage.__class__.__name__} stopped event propagation.",
                        )
                        break

                    # 递归调用, 处理所有后续阶段
                    await self._process_stages(event, i + 1)

                    # 此处是后续所有阶段处理完毕后返回的点, 执行后置处理
                    if event.is_stopped():
                        logger.debug(
                            f"Stage {stage.__class__.__name__} stopped event propagation.",
                        )
                        break
            else:
                # 如果返回的是普通协程(不含yield的async函数), 则不进入下一层(基线条件)
                # 简单地等待它执行完成, 然后继续执行下一个阶段
                await coroutine

                if event.is_stopped():
                    logger.debug(
                        f"Stage {stage.__class__.__name__} stopped event propagation."
                    )
                    break

    async def execute(self, event: AstrMessageEvent) -> None:
        """执行 pipeline

        Args:
            event (AstrMessageEvent): 事件对象

        """
        # The HTTP-to-WebChat queue carries an immutable principal; external
        # JSON fields cannot construct it. Propagate identity through the real
        # pipeline so tool/API consumers and generated file tokens retain it.
        account = (
            event.get_extra("_dashboard_account")
            if isinstance(event, WebChatMessageEvent)
            else None
        )
        if (
            not isinstance(account, DashboardPrincipal)
            or account.resource_owner != event.get_sender_id()
        ):
            account = None
        account_token = dashboard_account_context.set(account)
        owner_token = webchat_resource_owner.set(
            account.resource_owner if account else None
        )
        role_token = webchat_account_role.set(account.role if account else None)
        active_event_registry.register(event)
        try:
            await self._process_stages(event)

            # 发送一个空消息, 以便于后续的处理
            if isinstance(event, WebChatMessageEvent | WecomAIBotMessageEvent):
                await event.send(None)

            logger.debug("pipeline execution completed.")
        finally:
            try:
                event.cleanup_temporary_local_files()
                active_event_registry.unregister(event)
            finally:
                webchat_account_role.reset(role_token)
                webchat_resource_owner.reset(owner_token)
                dashboard_account_context.reset(account_token)
