"""Unit tests for the LLM request injection trace (spec §4)."""

from __future__ import annotations

import pytest

from astrbot.core.pipeline.llm_request_trace import (
    EVENT_EXTRA_KEY,
    FULL_CHARS,
    collect_changes,
    read_injections,
    record_injections,
    snapshot,
)
from astrbot.core.provider.entities import ProviderRequest


class _FakeEvent:
    plugins_name = None

    def __init__(self) -> None:
        self.extras: dict = {}

    def is_stopped(self) -> bool:
        return False

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self.extras[key] = value


def test_system_prompt_append_reports_delta_and_preview():
    req = ProviderRequest()
    req.system_prompt = "base"
    before = snapshot(req)

    req.system_prompt = "base" + "X" * 412
    changes = collect_changes(req, before, snapshot(req))

    assert changes == [
        {
            "field": "system_prompt",
            "delta": 412,
            "lossy": False,
            "preview": "X" * 80,
            "full": "X" * 412,
        }
    ]


def test_no_change_collects_nothing():
    req = ProviderRequest()
    before = snapshot(req)
    assert collect_changes(req, before, snapshot(req)) == []


def test_equal_length_rewrite_is_reported_as_rewritten():
    req = ProviderRequest()
    req.system_prompt = "base"
    before = snapshot(req)

    req.system_prompt = "BASE"
    changes = collect_changes(req, before, snapshot(req))

    assert changes == [
        {
            "field": "system_prompt",
            "delta": 0,
            "lossy": False,
            "preview": "",
            "full": "",
        }
    ]


def test_contexts_append_reports_count_and_last_text():
    req = ProviderRequest()
    before = snapshot(req)

    req.contexts = [{"role": "user", "content": "hello context"}]
    changes = collect_changes(req, before, snapshot(req))

    assert changes == [
        {
            "field": "contexts",
            "delta": 1,
            "lossy": False,
            "preview": "hello context",
            "full": "hello context",
        }
    ]


def test_full_text_carries_beyond_the_preview():
    req = ProviderRequest()
    req.system_prompt = ""
    before = snapshot(req)

    appended = "A" * 500
    req.system_prompt = appended
    changes = collect_changes(req, before, snapshot(req))

    assert changes[0]["preview"] == "A" * 80
    assert changes[0]["full"] == appended


def test_full_text_capped_with_ellipsis():
    req = ProviderRequest()
    before = snapshot(req)

    req.system_prompt = "B" * (FULL_CHARS + 100)
    changes = collect_changes(req, before, snapshot(req))

    assert changes[0]["full"] == "B" * FULL_CHARS + "…"


def test_multiple_appended_contexts_join_into_full():
    req = ProviderRequest()
    before = snapshot(req)

    req.contexts = [
        {"role": "user", "content": "first block"},
        {"role": "user", "content": "second block"},
    ]
    changes = collect_changes(req, before, snapshot(req))

    assert changes[0]["delta"] == 2
    assert changes[0]["preview"] == "second block"
    assert changes[0]["full"] == "first block\n\nsecond block"


def test_record_and_read_round_trip():
    event = _FakeEvent()
    record_injections(
        event,
        "tc_memory",
        "decorate_llm_req",
        [{"field": "contexts", "delta": 1, "lossy": False, "preview": ""}],
    )
    record_injections(
        event,
        "spcode_toolkit",
        "inject",
        [{"field": "func_tool", "delta": 1, "lossy": False, "preview": "codegraph"}],
    )

    items = read_injections(event)
    assert [i["plugin"] for i in items] == ["tc_memory", "spcode_toolkit"]
    assert read_injections(None) == []
    assert event.extras[EVENT_EXTRA_KEY] == items


def test_read_injections_tolerates_events_without_extras_store():
    """Events bypassing ``AstrMessageEvent.__init__`` raise on get_extra."""

    class _ExtraslessEvent:
        def get_extra(self, key, default=None):
            raise AttributeError("_extras")

    assert read_injections(_ExtraslessEvent()) == []


@pytest.mark.asyncio
async def test_call_event_hook_traces_each_handler():
    from astrbot.core.pipeline.context_utils import call_event_hook
    from astrbot.core.star.star import StarMetadata, star_map
    from astrbot.core.star.star_handler import (
        EventType,
        StarHandlerMetadata,
        star_handlers_registry,
    )

    event = _FakeEvent()
    req = ProviderRequest()

    async def _inject(_event, _req):
        _req.system_prompt += " injected"

    module_path = "test_llm_request_trace_mod"
    star_map[module_path] = StarMetadata(name="demo_plugin", module_path=module_path)
    meta = StarHandlerMetadata(
        event_type=EventType.OnLLMRequestEvent,
        handler_full_name=f"{module_path}_inject",
        handler_name="inject",
        handler_module_path=module_path,
        handler=_inject,
        event_filters=[],
    )
    star_handlers_registry.append(meta)
    try:
        stopped = await call_event_hook(event, EventType.OnLLMRequestEvent, req)
    finally:
        star_handlers_registry._handlers.remove(meta)
        star_handlers_registry.star_handlers_map.pop(meta.handler_full_name, None)
        star_map.pop(module_path, None)

    assert stopped is False
    items = [i for i in read_injections(event) if i["plugin"] == "demo_plugin"]
    assert len(items) == 1
    assert items[0]["handler"] == "inject"
    assert items[0]["changes"][0]["delta"] == len(" injected")


@pytest.mark.asyncio
async def test_raising_handler_leaves_no_entry_and_others_traced():
    from astrbot.core.pipeline.context_utils import call_event_hook
    from astrbot.core.star.star import StarMetadata, star_map
    from astrbot.core.star.star_handler import (
        EventType,
        StarHandlerMetadata,
        star_handlers_registry,
    )

    event = _FakeEvent()
    req = ProviderRequest()

    async def _raiser(_event, _req):
        _req.system_prompt += " half-written"
        raise RuntimeError("boom")

    async def _inject(_event, _req):
        _req.system_prompt += " injected"

    module_path = "test_llm_request_trace_raiser_mod"
    star_map[module_path] = StarMetadata(name="demo_plugin", module_path=module_path)
    metas = [
        StarHandlerMetadata(
            event_type=EventType.OnLLMRequestEvent,
            handler_full_name=f"{module_path}_{name}",
            handler_name=name,
            handler_module_path=module_path,
            handler=handler,
            event_filters=[],
        )
        for name, handler in (("raiser", _raiser), ("inject", _inject))
    ]
    for meta in metas:
        star_handlers_registry.append(meta)
    try:
        stopped = await call_event_hook(event, EventType.OnLLMRequestEvent, req)
    finally:
        for meta in metas:
            star_handlers_registry._handlers.remove(meta)
            star_handlers_registry.star_handlers_map.pop(meta.handler_full_name, None)
        star_map.pop(module_path, None)

    assert stopped is False
    items = [i for i in read_injections(event) if i["plugin"] == "demo_plugin"]
    # Only the successful injector leaves a trace; the raiser leaves none.
    assert [i["handler"] for i in items] == ["inject"]
