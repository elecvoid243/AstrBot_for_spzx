# Author: elecvoid243 · Created: 2026-08-30
"""Builtin /goal command surface — the loop logic lives in the kernel
(``astrbot/core/goal/goal_service.py``). Migrated from the
astrbot_plugin_goal plugin so injected turns render through the exact
primary-run pipeline on webchat sessions.
"""

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.goal.goal_service import goal_service
from astrbot.core.star.filter.command import GreedyStr


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        goal_service.bind(context)

    async def initialize(self) -> None:
        """Import goal states stored by the legacy astrbot_plugin_goal."""
        try:
            await goal_service.migrate_legacy_states()
        except Exception as e:
            from astrbot.core import logger

            logger.warning(f"goal loop: legacy state migration failed: {e}")

    # ------------------------------------------------------------------
    # /goal 指令组
    # ------------------------------------------------------------------

    @filter.command_group("goal", alias={"mubiao"})
    def goal(self):
        """常驻目标循环指令组。"""
        pass

    @goal.command("set")
    async def goal_set(self, event: AstrMessageEvent, text: GreedyStr):
        """设定常驻目标：/goal set <目标文本>"""
        if event.get_extra("goal_continuation"):
            # Synthetic continuation turns must never re-enter command
            # handlers (pathological goal text shaped like a command).
            return
        if refusal := goal_service.check_permission(event):
            yield event.plain_result(refusal)
            return
        umo = event.unified_msg_origin
        try:
            state = await goal_service.set_goal(umo, (text or "").strip())
        except ValueError as e:
            yield event.plain_result(f"目标无效：{e}")
            return
        yield event.plain_result(
            f"⊙ 目标已设定（{state.max_turns} 轮预算）：{state.goal}\n"
            "每轮结束后 judge 模型会评估目标是否达成；未达成将自动继续。"
            "可用 /goal status、/goal pause、/goal resume、/goal clear 管理，"
            "/subgoal add 追加评判标准。"
        )
        # Kick the loop off immediately: goal text as the first turn.
        await goal_service.inject_continuation(event, state.goal)

    @goal.command("status")
    async def goal_status(self, event: AstrMessageEvent):
        """查看当前目标状态"""
        if event.get_extra("goal_continuation"):
            return
        state = await goal_service.goals.get(event.unified_msg_origin)
        yield event.plain_result(
            state.status_line()
            if state
            else "当前没有目标。用 /goal set <目标文本> 设定一个。"
        )

    @goal.command("pause")
    async def goal_pause(self, event: AstrMessageEvent):
        """暂停目标循环"""
        if event.get_extra("goal_continuation"):
            return
        if refusal := goal_service.check_permission(event):
            yield event.plain_result(refusal)
            return
        state = await goal_service.goals.pause(
            event.unified_msg_origin, reason="user-paused"
        )
        yield event.plain_result(
            f"⏸ 目标已暂停：{state.goal}" if state else "当前没有目标。"
        )

    @goal.command("resume")
    async def goal_resume(self, event: AstrMessageEvent):
        """恢复目标循环（重置轮次预算）"""
        if event.get_extra("goal_continuation"):
            return
        state = await goal_service.goals.resume(event.unified_msg_origin)
        if not state:
            yield event.plain_result("没有可恢复的目标。")
            return
        yield event.plain_result(f"▶ 目标已恢复：{state.goal}（预算已重置）")
        # Kick off a fresh turn so the loop resumes immediately.
        await goal_service.inject_continuation(event, f"请继续完成目标：{state.goal}")

    @goal.command("clear")
    async def goal_clear(self, event: AstrMessageEvent):
        """清除目标，终止循环"""
        if event.get_extra("goal_continuation"):
            return
        had = await goal_service.goals.clear(event.unified_msg_origin)
        yield event.plain_result("✓ 目标已清除。" if had else "当前没有目标。")

    # ------------------------------------------------------------------
    # /subgoal 指令组
    # ------------------------------------------------------------------

    @filter.command_group("subgoal")
    def subgoal(self):
        """目标评判标准指令组（循环中途追加，judge 逐条要求证据）。"""
        pass

    @subgoal.command("add")
    async def subgoal_add(self, event: AstrMessageEvent, text: GreedyStr):
        """追加评判标准：/subgoal add <标准>"""
        if event.get_extra("goal_continuation"):
            return
        try:
            added = await goal_service.goals.add_subgoal(event.unified_msg_origin, text)
            yield event.plain_result(f"✓ 已追加标准：{added}")
        except (RuntimeError, ValueError) as e:
            yield event.plain_result(f"追加失败：{e}")

    @subgoal.command("list")
    async def subgoal_list(self, event: AstrMessageEvent):
        """列出当前评判标准"""
        if event.get_extra("goal_continuation"):
            return
        state = await goal_service.goals.get(event.unified_msg_origin)
        if not state:
            yield event.plain_result("暂无额外标准，用 /subgoal add <标准> 添加。")
        else:
            yield event.plain_result(state.render_subgoals_block())

    @subgoal.command("remove")
    async def subgoal_remove(self, event: AstrMessageEvent, index: int):
        """移除评判标准：/subgoal remove <序号>"""
        if event.get_extra("goal_continuation"):
            return
        try:
            removed = await goal_service.goals.remove_subgoal(
                event.unified_msg_origin, index
            )
            yield event.plain_result(f"✓ 已移除：{removed}")
        except (RuntimeError, ValueError, IndexError) as e:
            yield event.plain_result(f"移除失败：{e}")

    @subgoal.command("clear")
    async def subgoal_clear(self, event: AstrMessageEvent):
        """清空全部评判标准"""
        if event.get_extra("goal_continuation"):
            return
        try:
            n = await goal_service.goals.clear_subgoals(event.unified_msg_origin)
            yield event.plain_result(f"✓ 已清空 {n} 条标准。")
        except RuntimeError:
            yield event.plain_result("当前没有目标。")

    # ------------------------------------------------------------------
    # loop driver hooks
    # ------------------------------------------------------------------

    @filter.on_llm_request()
    async def _guard_continuation(self, event: AstrMessageEvent, req):
        """Drop stale continuation turns and strip blocking tools (kernel)."""
        await goal_service.guard_continuation(event, req)

    @filter.on_agent_done()
    async def _on_agent_done(self, event: AstrMessageEvent, run_context, response):
        """Judge the finished turn and maybe inject a continuation (kernel)."""
        await goal_service.on_turn_done(event, response)
