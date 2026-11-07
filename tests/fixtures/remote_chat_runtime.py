"""Deterministic WebChat runtime for isolated HTTP and browser acceptance tests."""

import asyncio
from types import SimpleNamespace

from astrbot.core.agent.runners.tool_loop_agent_runner import FollowUpTicket
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.process_stage import follow_up
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.webchat.webchat_adapter import WebChatAdapter
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr


class DeterministicChatRuntime:
    """Exercise ordinary queues, pipeline identity, follow-up capture and stop."""

    def __init__(self, core, db, delay=0.15, chunks=60):
        self.core, self.db, self.delay, self.chunks = core, db, delay, chunks
        self.tasks = set()
        self.adapter = WebChatAdapter({}, {}, asyncio.Queue())
        self.accepted = []
        self.followups = []

    async def submit(self, item):
        """Run one ordinary queued event in the production pipeline.

        Args:
            item: The unchanged ChatService-to-platform queue item.
        """
        event = self.adapter.create_event(await self.adapter.convert_message(item))
        self.accepted.append(item)
        scheduler = PipelineScheduler(PipelineContext(self.core.astrbot_config, self.core.plugin_manager, "default", self.db))
        scheduler.stages = [SimpleNamespace(process=self.process)]
        await scheduler.execute(event)

    async def process(self, event):
        """Emit deterministic incremental output or capture a real follow-up.

        Args:
            event: Production WebChat event with its trusted principal.
        """
        capture = follow_up.try_capture_follow_up(event)
        if capture:
            event.set_extra("_follow_up_captured", {"target_run_id": capture.target_run_id})
            consumed, activated = await follow_up.prepare_follow_up_capture(capture)
            await follow_up.finalize_follow_up_capture(capture, activated=activated, consumed_marked=consumed)
            return
        stopped = asyncio.Event()
        tickets = []
        runner = SimpleNamespace(run_context=SimpleNamespace(context=SimpleNamespace(event=event)),
                                 request_stop=stopped.set, done=lambda: stopped.is_set())

        def accept(message_text, **kwargs):
            """Return the standard runner follow-up ticket for deterministic input."""
            ticket = FollowUpTicket(seq=len(tickets), text=message_text)
            tickets.append(ticket)
            return ticket

        runner.follow_up = accept
        follow_up.register_active_runner(event.unified_msg_origin, runner)
        try:
            await event.send_typing()
            queue = webchat_queue_mgr.get_or_create_back_queue(event.message_obj.message_id)
            for index in range(self.chunks):
                if stopped.is_set():
                    break
                for ticket in tickets:
                    if not ticket.consumed:
                        ticket.consumed = True
                        ticket.resolved.set()
                        self.followups.append(ticket.text)
                        await queue.put({"type": "plain", "data": "\nFollow-up received: " + ticket.text + "\n", "streaming": True, "message_id": event.message_obj.message_id})
                await queue.put({"type": "plain", "data": f"Remote progress {index + 1}. ", "streaming": True, "message_id": event.message_obj.message_id})
                await asyncio.sleep(self.delay)
        finally:
            stopped.set()
            for ticket in tickets:
                ticket.resolved.set()
            follow_up.unregister_active_runner(event.unified_msg_origin, runner)

    async def listen(self, session_id):
        """Drain a session queue using concurrent ordinary pipeline executions.

        Args:
            session_id: Isolated local WebChat session.
        """
        queue = webchat_queue_mgr.get_or_create_queue(session_id)
        try:
            while True:
                item = await queue.get()
                task = asyncio.create_task(self.submit(item))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
