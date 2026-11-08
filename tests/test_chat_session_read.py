"""Read-only Agent history inspection through real SQLite and ChatUI tools."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from astrbot.builtin_stars.session_messages.tools import (
    AgentMessageSource,
    SessionMessageChain,
)
from astrbot.core import sp
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.db.po import DashboardAccount, PlatformMessageHistory
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.tools.interactive_choice_registry import registry
from astrbot.core.utils.webchat_access import DashboardPrincipal
from astrbot.dashboard.services.chat_service import (
    BotMessageAccumulator,
    build_webchat_unified_msg_origin,
)
from tests.test_chat_session_messages import (
    ControlledProvider,
    tool_call,
    wait_until,
)
from tests.test_chat_session_messages import (
    account_dashboard as account_dashboard,
)
from tests.test_chat_session_messages import (
    fake_core_lifecycle as fake_core_lifecycle,
)
from tests.test_chat_session_messages import (
    messaging as messaging,
)
from tests.test_chat_session_messages import (
    ordinary_chat as ordinary_chat,
)


@pytest_asyncio.fixture
async def reader(messaging):
    """Build a trusted source event and the registered permission-wrapped tool.

    Args:
        messaging: Real SQLite, service, adapter and Agent pipeline.

    Returns:
        Production fixture, source context and parsed read invocation.
    """
    m = messaging
    account = DashboardPrincipal(
        m.user["account_id"], m.user["resource_owner"], "user", m.user["username"]
    )
    event = m.adapter.create_event(
        await m.adapter.convert_message(
            (
                account.resource_owner,
                m.ids[0],
                {
                    "message": [
                        {"type": "plain", "text": "Read other saved conversations"}
                    ],
                    "message_id": "read-source",
                    "_dashboard_account": account,
                },
            )
        )
    )
    context = ContextWrapper(context=AstrAgentContext(context=m.context, event=event))
    tool = (
        m.context.get_llm_tool_manager()
        .get_full_tool_set()
        .get_tool("read_chat_session")
    )
    assert tool is not None

    async def read(**kwargs):
        """Call the real registered tool and decode its public result.

        Args:
            **kwargs: Model-facing read arguments.

        Returns:
            Decoded JSON, or the explicit rejection string.
        """
        result = await tool.call(context, **kwargs)
        return result if result.startswith("error:") else json.loads(result)

    return m, context, read


async def save_message(m, session_id, content):
    """Persist display-history fixtures through the production history manager.

    Args:
        m: Production chat fixture.
        session_id: Owning session.
        content: Saved UI message content.

    Returns:
        Inserted database row with its real ID.
    """
    return await m.service.platform_history_mgr.insert(
        platform_id="webchat", user_id=session_id, content=content
    )


@pytest.mark.asyncio
async def test_agent_discovers_and_reads_actual_human_agent_history(reader):
    """Discovery and read execute in the main tool loop without waking B.

    Args:
        reader: Real SQLite and registered read tool.
    """
    m, context, _ = reader
    a, b = m.ids
    project = await m.server.asgi_app.state.services.chat_projects.create_project(
        m.user["resource_owner"], {"title": "Research project"}
    )
    await m.db.add_session_to_project(b, project["project_id"], 0)

    async def finish(kwargs):
        return LLMResponse(role="assistant", completion_text="B saved response")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", finish)
    await m.client.post(
        "/api/v1/chat",
        headers=m.headers,
        json={"session_id": b, "message": "Human saved question"},
    )
    await wait_until(lambda: not m.service.chat_runs_by_session)
    await m.service.send_session_message(
        context.context.event, b, "Agent saved suggestion"
    )
    await wait_until(lambda: not m.service.chat_runs_by_session)
    before = await m.service.get_session(m.user["resource_owner"], b)
    events_before = len(m.events)
    observed = []

    async def inspect(kwargs):
        tools = [
            item for item in kwargs.get("contexts", []) if item.get("role") == "tool"
        ]
        if not tools:
            return tool_call("list_chat_sessions", {})
        if len(tools) == 1:
            assert b in json.dumps(tools)
            return tool_call("read_chat_session", {"session_id": b})
        payload = json.loads(tools[-1]["content"])
        text = json.dumps(payload, ensure_ascii=False)
        for expected in (
            "Human saved question",
            "Agent saved suggestion",
            "B saved response",
            "Research project",
            '"assistant"',
            '"user"',
            '"agent_source"',
        ):
            assert expected in text
        observed.append(text)
        return LLMResponse(role="assistant", completion_text="A inspected B")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", inspect)
    result = await asyncio.wait_for(
        m.client.post(
            "/api/v1/chat",
            headers=m.headers,
            json={"session_id": a, "message": "Inspect B"},
        ),
        15,
    )
    assert "A inspected B" in result.text
    assert observed
    await wait_until(lambda: not m.service.chat_runs_by_session)
    after = await m.service.get_session(m.user["resource_owner"], b)
    assert after == before
    assert len(m.events) == events_before + 1
    assert m.events[-1].get_extra("_session_message_chain") is None


@pytest.mark.asyncio
async def test_id_paging_handles_reversed_times_empty_and_old_cursors(reader):
    """Every saved ID is visited once even when timestamps run backwards.

    Args:
        reader: Trusted tool using real cursor queries.
    """
    m, _, read = reader
    a, b = m.ids
    assert (await read(session_id=b))["messages"] == []
    assert (await read(session_id=a))["has_more"] is False
    rows = []
    for number in range(7):
        row = await save_message(
            m,
            b,
            {
                "type": "user" if number % 2 else "bot",
                "message": [{"type": "plain", "text": f"record-{number}"}],
            },
        )
        rows.append(row)
        async with m.db.get_db() as connection:
            saved = await connection.get(PlatformMessageHistory, row.id)
            saved.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(
                days=number
            )
            await connection.commit()
    pages = []
    cursor = None
    while True:
        arguments = {"session_id": b, "limit": 2}
        if cursor is not None:
            arguments["before_id"] = cursor
        page = await read(**arguments)
        ids = [message["message_id"] for message in page["messages"]]
        assert ids == sorted(ids)
        pages.append(ids)
        if not page["has_more"]:
            assert page["next_before_id"] is None
            break
        assert page["next_before_id"] == ids[0]
        cursor = page["next_before_id"]
    assert [record_id for page in reversed(pages) for record_id in page] == [
        row.id for row in rows
    ]
    empty = await read(session_id=b, before_id=rows[0].id)
    assert empty["messages"] == [] and not empty["has_more"]
    assert empty["next_before_id"] is None


@pytest.mark.asyncio
async def test_tools_attachments_and_internal_fields_have_bounded_projection(reader):
    """Only display fields escape, with one budget for a multipart record.

    Args:
        reader: Trusted read tool and persisted history manager.
    """
    m, _, read = reader
    b = m.ids[1]
    await save_message(
        m,
        b,
        {
            "type": "bot",
            "reasoning": "PRIVATE_THINKING",
            "llm_request_params": {"system_prompt": "PRIVATE_SYSTEM"},
            "llm_request_injections": {"payload": "PRIVATE_INJECTION"},
            "llm_checkpoint_id": "PRIVATE_CHECKPOINT",
            "message": [
                {"type": "think", "think": "PRIVATE_THINKING"},
                {"type": "plain", "text": "Visible plain"},
                {"type": "text", "text": "Visible text"},
                {"type": "markdown", "text": "**Visible markdown**"},
                {
                    "type": "image",
                    "filename": "photo.png",
                    "base64": "PRIVATE_BINARY",
                    "path": "PRIVATE_PATH",
                },
                {"type": "file", "filename": "report.txt", "url": "PRIVATE_URL"},
                {
                    "type": "tool_call",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "lookup",
                            "args": {"query": "Saved argument"},
                            "result": {"answer": "Saved result"},
                            "llm_request_params": "PRIVATE_TOOL_PARAMS",
                        }
                    ],
                },
            ],
        },
    )
    default = await read(session_id=b)
    message = default["messages"][0]
    assert message["omitted"] == {"tool_calls": 1, "non_display_parts": 1}
    assert not any(part["type"] == "tool_call" for part in message["parts"])
    assert message["parts"][3] == {"type": "image", "filename": "photo.png"}
    included = await read(session_id=b, include_tool_results=True)
    call = included["messages"][0]["parts"][-1]
    assert call == {
        "type": "tool_call",
        "name": "lookup",
        "arguments": '{"query":"Saved argument"}',
        "result": '{"answer":"Saved result"}',
    }
    assert "PRIVATE_" not in json.dumps(included)
    short = await read(
        session_id=b, include_tool_results=True, max_chars_per_message=50
    )
    message = short["messages"][0]
    assert message["truncated"]
    assert (
        sum(
            len(value)
            for part in message["parts"]
            for key, value in part.items()
            if key != "type" and isinstance(value, str)
        )
        <= 50
    )
    assert message["message_id"] and message["created_at"]


@pytest.mark.asyncio
async def test_response_budget_preserves_the_cursor_and_bounds_large_structures(reader):
    """A total-budget stop keeps the newest rows and leaves older rows reachable.

    Args:
        reader: Real SQLite history and bounded registered tool.
    """
    m, _, read = reader
    b = m.ids[1]
    rows = []
    for number in range(12):
        rows.append(
            await save_message(
                m,
                b,
                {
                    "type": "bot",
                    "message": [
                        {
                            "type": "tool_call",
                            "tool_calls": [
                                {
                                    "name": "large",
                                    "args": {"query": "x" * 9000},
                                    "result": {"items": ["result" * 1000] * 10},
                                }
                            ],
                        },
                        {"type": "plain", "text": str(number)},
                    ],
                },
            )
        )
    pages = []
    cursor = None
    while True:
        arguments = {
            "session_id": b,
            "limit": 50,
            "include_tool_results": True,
            "max_chars_per_message": 8000,
        }
        if cursor is not None:
            arguments["before_id"] = cursor
        page = await read(**arguments)
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 40000
        ids = [message["message_id"] for message in page["messages"]]
        pages.append(ids)
        if not page["has_more"]:
            break
        assert page["response_budget_limited"]
        cursor = page["next_before_id"]
        assert cursor == ids[0]
    assert [record_id for page in reversed(pages) for record_id in page] == [
        row.id for row in rows
    ]
    await save_message(
        m, b, {"type": "bot", "message": [{"type": "plain", "text": "\x01" * 10000}]}
    )
    escaped = await read(session_id=b, limit=1, max_chars_per_message=8000)
    assert len(json.dumps(escaped, ensure_ascii=False, separators=(",", ":"))) <= 40000
    assert escaped["messages"][0]["truncated"]
    await save_message(
        m,
        b,
        {
            "type": "user",
            "message": [{"type": "image", "filename": "photo.png", "base64": "hidden"}]
            * 1000,
        },
    )
    attachments = (await read(session_id=b, limit=1))["messages"][0]
    assert len(attachments["parts"]) == 50
    assert attachments["omitted"]["parts"] == 950


@pytest.mark.asyncio
async def test_long_public_names_are_marked_and_bounded_even_on_empty_pages(reader):
    """Metadata cannot consume the response budget or hide stable IDs.

    Args:
        reader: Real project/session storage and registered read tool.
    """
    m, context, read = reader
    a, b = m.ids
    long_name = "Release verification " * 5000
    project = await m.server.asgi_app.state.services.chat_projects.create_project(
        m.user["resource_owner"], {"title": long_name}
    )
    await m.db.add_session_to_project(b, project["project_id"], 0)
    await m.db.update_platform_session(b, display_name=long_name)
    empty = await read(session_id=b)
    assert empty["metadata_truncated"]
    assert len(empty["display_name"]) == len(empty["project_name"]) == 512
    assert empty["session_id"] == b and empty["project_id"] == project["project_id"]
    assert empty["messages"] == []
    assert len(json.dumps(empty, ensure_ascii=False, separators=(",", ":"))) <= 40000
    account = context.context.event.get_extra("_dashboard_account")
    source = AgentMessageSource(
        DashboardPrincipal(
            account.account_id, account.resource_owner, "user", long_name
        ),
        a,
        long_name,
        SessionMessageChain(),
    )
    await save_message(
        m,
        b,
        {
            "type": "user",
            "message": [{"type": "plain", "text": "Saved Agent text"}],
            "agent_source": source.public_data(),
        },
    )
    page = await read(session_id=b)
    message = page["messages"][0]
    assert message["parts"][0]["text"] == "Saved Agent text"
    assert message["metadata_truncated"]
    assert message["agent_source"]["account_id"] == account.account_id
    assert message["agent_source"]["session_id"] == a
    assert (
        len(message["agent_source"]["login_name"])
        == len(message["agent_source"]["display_name"])
        == 512
    )
    assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 40000


@pytest.mark.asyncio
async def test_saved_choice_option_descriptions_share_the_message_budget(reader):
    """Keep visible option descriptions after real accumulation and persistence.

    Args:
        reader: Real SQLite history and the registered read tool.
    """
    m, _, read = reader
    b = m.ids[1]
    prompt = "Choose a plan"
    introduction = "Saved introduction"
    for description in ("Costs less but takes longer", "Detailed trade-off " * 1000):
        accumulator = BotMessageAccumulator()
        accumulator.add_plain(introduction, chain_type=None, streaming=False)
        accumulator.add_plain(
            json.dumps(
                {
                    "request_id": "described-options",
                    "spec": {
                        "prompt": prompt,
                        "options": [
                            {"id": "a", "label": "Plan A", "description": description},
                            {
                                "id": "b",
                                "label": "Plan B",
                                "description": "Keeps rollback available",
                            },
                        ],
                    },
                }
            ),
            chain_type="interactive_choice",
            streaming=False,
        )
        row = await save_message(
            m, b, {"type": "bot", "message": accumulator.build_message_parts()}
        )
        stored = await m.db.get_platform_message_history_by_id(row.id)
        assert stored.content["message"][1]["options"][0]["description"] == description
        full = (await read(session_id=b, limit=1))["messages"][0]
        choice = full["parts"][1]
        assert choice["options"][0].startswith("Plan A\n" + description[:20])
        if len(description) < 100:
            assert choice["options"] == [
                "Plan A\n" + description,
                "Plan B\nKeeps rollback available",
            ]
            assert not full["truncated"] and full["omitted"] == {}
        else:
            assert full["truncated"]
            assert full["omitted"]["choice_options"] == 1
        budget = len(introduction) + len(prompt) + len("Plan A\n") + 5
        short = (await read(session_id=b, limit=1, max_chars_per_message=budget))[
            "messages"
        ][0]
        assert short["parts"][0]["text"] == introduction
        assert short["parts"][1]["options"] == ["Plan A\n" + description[:5]]
        assert short["truncated"] and short["omitted"]["choice_options"] == 1
        assert (
            sum(
                len(part.get("text", ""))
                + len(part.get("prompt", ""))
                + sum(len(option) for option in part.get("options", []))
                for part in short["parts"]
            )
            == budget
        )


@pytest.mark.asyncio
async def test_archived_and_self_reads_preserve_busy_choice_chain_state(reader):
    """Reads neither admit input nor resolve a human choice or chain budget.

    Args:
        reader: Real history, runtime registry and trusted source execution.
    """
    m, context, read = reader
    a, b = m.ids
    saved = await save_message(
        m,
        b,
        {
            "type": "bot",
            "message": [
                {
                    "type": "interactive_choice",
                    "prompt": "Should we publish?",
                    "options": [
                        {"id": "yes", "label": "Publish"},
                        {"id": "no", "label": "Wait"},
                    ],
                    "extra_content": "Check the release notes first.",
                }
            ],
        },
    )
    await m.db.update_platform_session(b, archived=1)
    umo = build_webchat_unified_msg_origin(await m.db.get_platform_session_by_id(b))
    future = asyncio.get_running_loop().create_future()
    registry.add("read-waiting", umo, future, {"prompt": "Human only"}, 0, 99999999999)
    chain = SessionMessageChain(count=7, sessions={a, b})
    context.context.event.set_extra("_session_message_chain", chain)
    m.service.running_convs[b] = True
    m.service.chat_runs_by_session[b] = {"preparing"}
    before = await m.db.get_platform_message_history("webchat", b)
    event_count = len(m.events)
    try:
        result = await read(session_id=b)
        assert result["archived"] and result["is_running"]
        assert result["active_run_ids"] == ["preparing"]
        assert result["last_message_id"] == saved.id
        choice = result["messages"][0]["parts"][0]
        assert choice["prompt"] == "Should we publish?"
        assert choice["options"] == ["Publish", "Wait"]
        assert choice["extra_content"] == "Check the release notes first."
        short = await read(session_id=b, max_chars_per_message=5)
        assert short["messages"][0]["parts"][0]["prompt"] == "Shoul"
        assert short["messages"][0]["truncated"]
        assert (await read(session_id=a))["session_id"] == a
        assert not future.done()
        assert chain.count == 7 and chain.sessions == {a, b} and not chain.stopped
        assert m.service.chat_runs_by_session[b] == {"preparing"}
        assert m.service.running_convs[b] is True
        assert len(m.events) == event_count
        assert await m.db.get_platform_message_history("webchat", b) == before
    finally:
        registry.remove("read-waiting")
        m.service.chat_runs_by_session.pop(b)
        m.service.running_convs.pop(b)


@pytest.mark.asyncio
async def test_last_message_id_tracks_new_output_across_older_pages(reader):
    """The newest saved ID is reported even when the read pages backwards.

    Args:
        reader: Real SQLite history and the registered read tool.
    """
    m, _, read = reader
    b = m.ids[1]
    first = await save_message(
        m, b, {"type": "user", "message": [{"type": "plain", "text": "one"}]}
    )
    newest = await save_message(
        m, b, {"type": "bot", "message": [{"type": "plain", "text": "two"}]}
    )
    page = await read(session_id=b, limit=1)
    assert page["active_run_ids"] == []
    assert page["last_message_id"] == newest.id
    older = await read(session_id=b, before_id=newest.id, limit=1)
    assert older["messages"][0]["message_id"] == first.id
    assert older["last_message_id"] == newest.id


@pytest.mark.asyncio
async def test_active_run_ids_are_bounded_and_keep_the_busy_flag_true(reader):
    """A long run list is cut deterministically and still marks the session busy.

    Args:
        reader: Real SQLite history and the registered read tool.
    """
    m, _, read = reader
    b = m.ids[1]
    m.service.chat_runs_by_session[b] = {f"run-{index:02d}" for index in range(33)}
    try:
        page = await read(session_id=b)
        assert page["is_running"]
        assert page["active_run_ids"] == [f"run-{index:02d}" for index in range(32)]
        assert page["metadata_truncated"]
    finally:
        m.service.chat_runs_by_session.pop(b)


@pytest.mark.asyncio
async def test_reads_reject_foreign_hidden_thread_platform_and_invalid_identity(reader):
    """Server-owned identity and ordinary target checks precede history exposure.

    Args:
        reader: Trusted source and real database account boundary.
    """
    m, context, read = reader
    owner = m.user["resource_owner"]
    foreign = await m.db.create_platform_session(creator=m.foreign["resource_owner"])
    hidden = await m.db.create_platform_session(creator=owner, is_group=1)
    platform = await m.db.create_platform_session(creator=owner, platform_id="other")
    thread = await m.db.create_webchat_thread(
        parent_session_id=m.ids[1],
        parent_message_id=0,
        creator=owner,
        base_checkpoint_id="",
        selected_text="",
    )
    for sid in (
        foreign.session_id,
        hidden.session_id,
        platform.session_id,
        thread.thread_id,
        "deleted",
    ):
        result = await read(session_id=sid)
        assert result.startswith("error:"), result
    event = context.context.event
    account = event.get_extra("_dashboard_account")
    event.set_extra("_dashboard_account", {"account_id": account.account_id})
    assert "trusted" in await read(session_id=m.ids[1])
    event.set_extra("_dashboard_account", account)
    event.set_extra("agent_team_execution", True)
    assert "trusted" in await read(session_id=m.ids[1])
    event.set_extra("agent_team_execution", False)
    async with m.db.get_db() as connection:
        saved = await connection.get(DashboardAccount, account.account_id)
        saved.enabled = False
        await connection.commit()
    assert "unavailable" in await read(session_id=m.ids[1])


@pytest.mark.asyncio
async def test_read_arguments_and_existing_role_controls_are_enforced(reader):
    """Types, bounds and extra identity arguments cannot bypass tool controls.

    Args:
        reader: Registered permission-wrapped read tool.
    """
    m, _, read = reader
    valid = {"session_id": m.ids[1]}
    invalid = [
        {},
        {"session_id": " "},
        {"session_id": True},
        {"owner": "forged"},
        {"account": "forged"},
    ]
    for key, values in {
        "limit": [True, 0, 51, "20", 1.5],
        "before_id": [True, 0, -1, "1", None, 2**63],
        "max_chars_per_message": [True, 0, 8001, "2000"],
        "include_tool_results": [1, "true", None],
    }.items():
        invalid.extend({key: value} for value in values)
    for payload in invalid:
        arguments = {**valid, **payload} if payload else {}
        result = await read(**arguments)
        assert result.startswith("error:"), (arguments, result)
    assert (await read(**valid, limit=1, max_chars_per_message=1))["messages"] == []
    await sp.global_put(
        "tool_permissions", {"_default": {"read_chat_session": "admin"}}
    )
    try:
        assert "requires admin" in await read(**valid)
    finally:
        await sp.global_put("tool_permissions", {})


@pytest.mark.asyncio
async def test_mixed_human_image_and_agent_inputs_keep_saved_sources(reader, tmp_path):
    """Read each admitted input with its own source despite model follow-up merging.

    Args:
        reader: Real input admission, persistence and read tool.
        tmp_path: Isolated dummy attachment used by production path validation.
    """
    m, context, read = reader
    a, b = m.ids
    account = context.context.event.get_extra("_dashboard_account")
    image = tmp_path / "human-image.png"
    image.write_bytes(b"image bytes are not read by the history tool")
    attachment = await m.db.insert_attachment(str(image), "image", "image/png")
    source = AgentMessageSource(account, a, "A", SessionMessageChain())
    gate = asyncio.Event()

    async def hold(kwargs):
        await gate.wait()
        return LLMResponse(role="assistant", completion_text="Merged inputs received")

    m.providers["gpt-mini"] = ControlledProvider("gpt-mini", hold)
    await m.service.build_chat_stream(
        account.resource_owner, {"session_id": b, "message": "Initial human"}
    )
    await wait_until(lambda: bool(m.providers["gpt-mini"].calls))
    try:
        await m.service.send_session_message(
            context.context.event, b, "Agent follow-up"
        )
        await m.service.build_chat_stream(
            account.resource_owner,
            {
                "session_id": b,
                "message": [
                    {"type": "plain", "text": "Human follow-up"},
                    {
                        "type": "image",
                        "attachment_id": attachment.attachment_id,
                        "filename": "human-image.png",
                    },
                ],
                "message_id": "mixed-human",
            },
        )
        saved = await read(session_id=b)
        user_messages = [
            message for message in saved["messages"] if message["role"] == "user"
        ]
        agent = next(
            message
            for message in user_messages
            if "Agent follow-up" in json.dumps(message)
        )
        human = next(
            message
            for message in user_messages
            if "Human follow-up" in json.dumps(message)
        )
        assert agent["agent_source"] == source.public_data()
        assert "agent_source" not in human
        assert human["parts"][-1]["type"] == "image"
        assert "image bytes" not in json.dumps(saved)
        assert "path" not in human["parts"][-1]
        assert agent["message_id"] < human["message_id"]
    finally:
        gate.set()
        await wait_until(lambda: not m.service.chat_runs_by_session)
