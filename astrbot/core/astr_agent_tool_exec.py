import asyncio
import inspect
import json
import time
import traceback
import typing as T
import uuid
from collections.abc import Sequence
from collections.abc import Set as AbstractSet

import mcp

from astrbot import logger
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.mcp_client import MCPTool
from astrbot.core.agent.message import Message
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_main_agent_resources import (
    BACKGROUND_TASK_RESULT_WOKE_SYSTEM_PROMPT,
)
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import (
    CommandResult,
    MessageChain,
    MessageEventResult,
)
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.provider.entites import ProviderRequest
from astrbot.core.provider.register import llm_tools
from astrbot.core.subagent_event_sink import SubAgentEventSink
from astrbot.core.subagent_manager import (
    RET_PENDING_TASK_CREATE_FAILED,
    SubAgentManager,
    SubAgentStatus,
)
from astrbot.core.tools.computer_tools import (
    CuaKeyboardTypeTool,
    CuaMouseClickTool,
    CuaScreenshotTool,
    ExecuteShellTool,
    FileDownloadTool,
    FileEditTool,
    FileReadTool,
    FileUploadTool,
    FileWriteTool,
    GrepTool,
    LocalExecuteShellTool,
    LocalPythonTool,
    PythonTool,
    ShellSessionTool,
)
from astrbot.core.tools.message_tools import SendMessageToUserTool
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.config_number import coerce_int_config
from astrbot.core.utils.history_saver import persist_agent_history
from astrbot.core.utils.image_ref_utils import is_supported_image_ref
from astrbot.core.utils.string_utils import normalize_and_dedupe_strings

# Auto-inherit decision line: fork only when the estimated inherited
# prefix stays at or below this fraction of the subagent provider's
# max_context_tokens. Deliberately well below the 0.82 compression
# trigger — the estimate excludes per-request tool schemas, and the
# subagent's own steps (tool calls / results) consume unbounded tokens
# on top of the prefix. A prefix near the compression line would make
# the runner compress after a step or two, which is strictly worse than
# a clean normal start (cache lost, context truncated, full reprocess).
_AUTO_FORK_MAX_USAGE_RATIO = 0.45


class FunctionToolExecutor(BaseFunctionToolExecutor[AstrAgentContext]):
    @classmethod
    def _collect_image_urls_from_args(cls, image_urls_raw: T.Any) -> list[str]:
        if image_urls_raw is None:
            return []

        if isinstance(image_urls_raw, str):
            return [image_urls_raw]

        if isinstance(image_urls_raw, (Sequence, AbstractSet)) and not isinstance(
            image_urls_raw, (str, bytes, bytearray)
        ):
            return [item for item in image_urls_raw if isinstance(item, str)]

        logger.debug(
            "Unsupported image_urls type in handoff tool args: %s",
            type(image_urls_raw).__name__,
        )
        return []

    @classmethod
    async def _collect_image_urls_from_message(
        cls, run_context: ContextWrapper[AstrAgentContext]
    ) -> list[str]:
        urls: list[str] = []
        event = getattr(run_context.context, "event", None)
        message_obj = getattr(event, "message_obj", None)
        message = getattr(message_obj, "message", None)
        if message:
            for idx, component in enumerate(message):
                if not isinstance(component, Image):
                    continue
                try:
                    path = await component.convert_to_file_path()
                    if path:
                        urls.append(path)
                except Exception as e:
                    logger.error(
                        "Failed to convert handoff image component at index %d: %s",
                        idx,
                        e,
                        exc_info=True,
                    )
        return urls

    @classmethod
    async def _collect_handoff_image_urls(
        cls,
        run_context: ContextWrapper[AstrAgentContext],
        image_urls_raw: T.Any,
    ) -> list[str]:
        candidates: list[str] = []
        candidates.extend(cls._collect_image_urls_from_args(image_urls_raw))
        candidates.extend(await cls._collect_image_urls_from_message(run_context))

        normalized = normalize_and_dedupe_strings(candidates)
        extensionless_local_roots = (get_astrbot_temp_path(),)
        sanitized = [
            item
            for item in normalized
            if is_supported_image_ref(
                item,
                allow_extensionless_existing_local_file=True,
                extensionless_local_roots=extensionless_local_roots,
            )
        ]
        dropped_count = len(normalized) - len(sanitized)
        if dropped_count > 0:
            logger.debug(
                "Dropped %d invalid image_urls entries in handoff image inputs.",
                dropped_count,
            )
        return sanitized

    @classmethod
    def _get_session_from_context(cls, run_context: ContextWrapper[AstrAgentContext]):
        """Extract the SubAgentSession from run_context.

        Walks through run_context -> context -> event -> unified_msg_origin
        to locate the session_id, then returns the corresponding session
        from SubAgentManager.  Returns ``None`` when any step fails.
        """
        run_context_context = getattr(run_context, "context", None)
        event = (
            getattr(run_context_context, "event", None) if run_context_context else None
        )
        session_id = getattr(event, "unified_msg_origin", None) if event else None
        if not session_id:
            return None

        return SubAgentManager.get_session(session_id)

    @classmethod
    def _resolve_handoff_by_name(
        cls, run_context: ContextWrapper[AstrAgentContext], name: str
    ) -> HandoffTool | None:
        """Resolve a HandoffTool from SubAgentManager by subagent name."""
        session = cls._get_session_from_context(run_context)
        if not session:
            return None

        return session.handoff_tools.get(name, None)

    @classmethod
    def _list_available_subagents(
        cls, run_context: ContextWrapper[AstrAgentContext]
    ) -> list[str]:
        """List available subagent names for the current session."""
        session = cls._get_session_from_context(run_context)
        if not session:
            return []

        return list(session.handoff_tools.keys())

    @classmethod
    async def execute(cls, tool, run_context, **tool_args):
        """执行函数调用。

        Args:
            event (AstrMessageEvent): 事件对象, 当 origin 为 local 时必须提供。
            **kwargs: 函数调用的参数。

        Returns:
            AsyncGenerator[None | mcp.types.CallToolResult, None]

        """
        # 防止subagent的名字叫"subagent"造成工具歧义（在create中已经不会发生，此处用于兜底）
        if (
            isinstance(tool, FunctionTool)
            and not isinstance(tool, HandoffTool)
            and tool.name == "transfer_to_subagent"
        ):
            tool_args = dict(tool_args)
            subagent_name = tool_args.pop("name", None)
            if not subagent_name:
                yield mcp.types.CallToolResult(
                    content=[
                        mcp.types.TextContent(
                            type="text",
                            text="Error: 'name' parameter is required for transfer_to_subagent. Use list_subagents to see available names.",
                        )
                    ]
                )
                return

            handoff_tool = cls._resolve_handoff_by_name(run_context, subagent_name)
            if handoff_tool is None:
                available = cls._list_available_subagents(run_context)
                yield mcp.types.CallToolResult(
                    content=[
                        mcp.types.TextContent(
                            type="text",
                            text=f"Error: Subagent '{subagent_name}' not found. Available subagents: {available}. Use create_subagent to create new ones.",
                        )
                    ]
                )
                return

            # Reject delegation while the target subagent is already running to
            # keep each subagent strictly single-task at a time.
            resolved_agent_name = getattr(handoff_tool.agent, "name", None)
            if (
                resolved_agent_name
                and SubAgentManager.get_subagent_status(
                    run_context.context.event.unified_msg_origin, resolved_agent_name
                )
                == SubAgentStatus.RUNNING
            ):
                yield mcp.types.CallToolResult(
                    content=[
                        mcp.types.TextContent(
                            type="text",
                            text=(
                                f"Error: SubAgent '{resolved_agent_name}' is currently RUNNING. "
                                "Wait for it to finish (e.g. via wait_for_subagent) or pick another subagent."
                            ),
                        )
                    ]
                )
                return

            is_bg = tool_args.pop("background_task", False)
            if is_bg:
                async for r in cls._execute_handoff_background(
                    handoff_tool, run_context, **tool_args
                ):
                    yield r
            else:
                async for r in cls._execute_handoff(
                    handoff_tool, run_context, **tool_args
                ):
                    yield r
            return

        if isinstance(tool, HandoffTool):
            # Same single-task guard as above for direct HandoffTool invocations.
            direct_agent_name = getattr(tool.agent, "name", None)
            if (
                direct_agent_name
                and SubAgentManager.get_subagent_status(
                    run_context.context.event.unified_msg_origin, direct_agent_name
                )
                == SubAgentStatus.RUNNING
            ):
                yield mcp.types.CallToolResult(
                    content=[
                        mcp.types.TextContent(
                            type="text",
                            text=(
                                f"Error: SubAgent '{direct_agent_name}' is currently RUNNING. "
                                "Wait for it to finish (e.g. via wait_for_subagent) or pick another subagent."
                            ),
                        )
                    ]
                )
                return

            is_bg = tool_args.pop("background_task", False)
            if is_bg:
                async for r in cls._execute_handoff_background(
                    tool, run_context, **tool_args
                ):
                    yield r
                return
            async for r in cls._execute_handoff(tool, run_context, **tool_args):
                yield r
            return

        elif isinstance(tool, MCPTool):
            async for r in cls._execute_mcp(tool, run_context, **tool_args):
                yield r
            return

        elif tool.is_background_task:
            task_id = uuid.uuid4().hex

            async def _run_in_background() -> None:
                try:
                    await cls._execute_background(
                        tool=tool,
                        run_context=run_context,
                        task_id=task_id,
                        **tool_args,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        f"Background task {task_id} failed: {e!s}",
                        exc_info=True,
                    )

            asyncio.create_task(_run_in_background())
            text_content = mcp.types.TextContent(
                type="text",
                text=f"Background task submitted. task_id={task_id}",
            )
            yield mcp.types.CallToolResult(content=[text_content])

            return
        else:
            async for r in cls._execute_local(tool, run_context, **tool_args):
                yield r
            return

    @classmethod
    def _get_runtime_computer_tools(
        cls,
        runtime: str,
        tool_mgr,
        booter: str | None = None,
    ) -> dict[str, FunctionTool]:
        booter = "" if booter is None else str(booter).lower()
        if runtime == "sandbox":
            shell_tool = tool_mgr.get_builtin_tool(ExecuteShellTool)
            python_tool = tool_mgr.get_builtin_tool(PythonTool)
            upload_tool = tool_mgr.get_builtin_tool(FileUploadTool)
            download_tool = tool_mgr.get_builtin_tool(FileDownloadTool)
            read_tool = tool_mgr.get_builtin_tool(FileReadTool)
            write_tool = tool_mgr.get_builtin_tool(FileWriteTool)
            edit_tool = tool_mgr.get_builtin_tool(FileEditTool)
            grep_tool = tool_mgr.get_builtin_tool(GrepTool)
            tools = {
                shell_tool.name: shell_tool,
                python_tool.name: python_tool,
                upload_tool.name: upload_tool,
                download_tool.name: download_tool,
                read_tool.name: read_tool,
                write_tool.name: write_tool,
                edit_tool.name: edit_tool,
                grep_tool.name: grep_tool,
            }
            if booter == "cua":
                screenshot_tool = tool_mgr.get_builtin_tool(CuaScreenshotTool)
                mouse_click_tool = tool_mgr.get_builtin_tool(CuaMouseClickTool)
                keyboard_type_tool = tool_mgr.get_builtin_tool(CuaKeyboardTypeTool)
                tools.update(
                    {
                        screenshot_tool.name: screenshot_tool,
                        mouse_click_tool.name: mouse_click_tool,
                        keyboard_type_tool.name: keyboard_type_tool,
                    }
                )
            return tools
        if runtime == "local":
            shell_tool = LocalExecuteShellTool()
            shell_session_tool = tool_mgr.get_builtin_tool(ShellSessionTool)
            python_tool = tool_mgr.get_builtin_tool(LocalPythonTool)
            read_tool = tool_mgr.get_builtin_tool(FileReadTool)
            write_tool = tool_mgr.get_builtin_tool(FileWriteTool)
            edit_tool = tool_mgr.get_builtin_tool(FileEditTool)
            grep_tool = tool_mgr.get_builtin_tool(GrepTool)
            return {
                shell_tool.name: shell_tool,
                shell_session_tool.name: shell_session_tool,
                python_tool.name: python_tool,
                read_tool.name: read_tool,
                write_tool.name: write_tool,
                edit_tool.name: edit_tool,
                grep_tool.name: grep_tool,
            }
        return {}

    @classmethod
    def _build_handoff_toolset(
        cls,
        run_context: ContextWrapper[AstrAgentContext],
        tools: list[str | FunctionTool] | None,
    ) -> ToolSet | None:
        ctx = run_context.context.context
        event = run_context.context.event
        cfg = ctx.get_config(umo=event.unified_msg_origin)
        provider_settings = cfg.get("provider_settings", {})
        runtime = str(provider_settings.get("computer_use_runtime", "local"))
        tool_mgr = (
            ctx.get_llm_tool_manager()
            if hasattr(ctx, "get_llm_tool_manager")
            else llm_tools
        )
        runtime_computer_tools = cls._get_runtime_computer_tools(
            runtime,
            tool_mgr,
            provider_settings.get("sandbox", {}).get("booter"),
        )

        # Keep persona semantics aligned with the main agent: tools=None means
        # "all tools", including runtime computer-use tools.
        if tools is None:
            toolset = ToolSet()
            handoff_names = {
                tool.name
                for tool in tool_mgr.func_list
                if isinstance(tool, HandoffTool)
            }
            for registered_tool in tool_mgr.get_full_tool_set():
                if registered_tool.name in handoff_names:
                    continue
                if registered_tool.active:
                    toolset.add_tool(registered_tool)
            for runtime_tool in runtime_computer_tools.values():
                toolset.add_tool(runtime_tool)
            return None if toolset.empty() else toolset

        if not tools:
            return None

        toolset = ToolSet()
        for tool_name_or_obj in tools:
            if isinstance(tool_name_or_obj, str):
                registered_tool = llm_tools.get_func(tool_name_or_obj)
                if registered_tool and registered_tool.active:
                    toolset.add_tool(registered_tool)
                    continue
                runtime_tool = runtime_computer_tools.get(tool_name_or_obj)
                if runtime_tool:
                    toolset.add_tool(runtime_tool)
            elif isinstance(tool_name_or_obj, FunctionTool):
                toolset.add_tool(tool_name_or_obj)

        # Always add send_shared_context tool for shared context feature
        try:
            from astrbot.core.subagent_manager import (
                SEND_SHARED_CONTEXT_TOOL,
                SubAgentManager,
            )

            session_id = event.unified_msg_origin
            session = SubAgentManager.get_session(session_id)
            if session and session.shared_context_enabled:
                toolset.add_tool(SEND_SHARED_CONTEXT_TOOL)
        except Exception as e:
            logger.debug(f"[SubAgent] Failed to add shared context tool: {e}")

        return None if toolset.empty() else toolset

    @classmethod
    def _maybe_create_subagent_sink(
        cls,
        event,
        prov_settings: dict,
        agent_name: str,
        input_text: str | None,
    ) -> SubAgentEventSink | None:
        """Create a progress sink for webchat foreground handoffs.

        Returns None (feature off) for non-webchat platforms, when
        ``provider_settings.show_subagent_progress`` is False, or when the
        event carries no message id to bind the back queue to.

        Args:
            event: The current message event.
            prov_settings: The ``provider_settings`` config section.
            agent_name: Name of the subagent being delegated to.
            input_text: The delegated task text (truncated for preview).

        Returns:
            A bound SubAgentEventSink, or None when progress streaming is off.
        """
        if not prov_settings.get("show_subagent_progress", True):
            return None
        if event.get_platform_name() != "webchat":
            return None
        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if not message_id:
            return None
        return SubAgentEventSink(
            message_id=str(message_id),
            agent_name=agent_name,
            input_preview=(input_text or "")[:200],
            # Send the full task text so the dashboard "expand" toggle can
            # show the complete delegated task instead of a truncated copy.
            input_full=input_text or "",
        )

    @classmethod
    async def _execute_handoff(
        cls,
        tool: HandoffTool,
        run_context: ContextWrapper[AstrAgentContext],
        *,
        image_urls_prepared: bool = False,
        **tool_args: T.Any,
    ):
        tool_args = dict(tool_args)
        input_ = tool_args.get("input")
        if image_urls_prepared:
            prepared_image_urls = tool_args.get("image_urls")
            if isinstance(prepared_image_urls, list):
                image_urls = prepared_image_urls
            else:
                logger.debug(
                    "Expected prepared handoff image_urls as list[str], got %s.",
                    type(prepared_image_urls).__name__,
                )
                image_urls = []
        else:
            image_urls = await cls._collect_handoff_image_urls(
                run_context,
                tool_args.get("image_urls"),
            )
        tool_args["image_urls"] = image_urls

        ctx = run_context.context.context
        event = run_context.context.event
        umo = event.unified_msg_origin
        agent_name = getattr(tool.agent, "name", "unknown")

        # Use per-subagent provider override if configured; otherwise fall back
        # to the current/default provider resolution.
        prov_id = getattr(
            tool, "provider_id", None
        ) or await ctx.get_current_chat_provider_id(umo)

        config = ctx.get_config(umo=umo)
        prov_settings: dict = config.get("provider_settings", {})

        # Build the subagent's own instruction block once. Fork mode reuses it
        # as the appended user message; normal mode uses it as the system prompt.
        subagent_system_prompt = cls._build_subagent_system_prompt(
            umo, tool, prov_settings
        )

        # Resolve the effective context inherit mode ("normal" / "fork").
        # "auto" decides per delegation based on the estimated context usage
        # against the subagent provider's compression threshold.
        inherit_mode = SubAgentManager.get_context_inherit_mode()
        if inherit_mode == "auto":
            inherit_mode = await cls._resolve_auto_inherit_mode(
                run_context, ctx, prov_id, agent_name, subagent_system_prompt, input_
            )
        use_fork_context = inherit_mode == "fork"
        if use_fork_context:
            # Fork only pays off when the inherited prefix can hit the
            # provider's prefix cache — a subagent on a DIFFERENT provider
            # never can, so the inheritance would be pure overhead. Compare
            # the delegated provider against the main runner's actual
            # provider and fall back to normal mode on mismatch. Applies to
            # both forced "fork" and auto-resolved fork. When the main
            # runner (or its provider) cannot be determined, keep fork —
            # that matches the existing toolset fallback path.
            main_runner = (run_context.context.extra or {}).get("main_agent_runner")
            main_provider = getattr(main_runner, "provider", None)
            main_provider_id = (
                str(main_provider.provider_config.get("id") or "")
                if main_provider is not None
                else ""
            )
            if main_provider_id and prov_id != main_provider_id:
                logger.info(
                    "[SubAgent:Fork] subagent provider %s != main agent provider %s; "
                    "fork prefix cache cannot hit, falling back to normal mode",
                    prov_id,
                    main_provider_id,
                )
                inherit_mode = "normal"
                use_fork_context = False
            else:
                # Same provider, but the fork can still miss: provider prefix
                # caches are namespaced per model. The main agent may pin a
                # model for this turn (req.model) while the forked subagent
                # always runs the provider default model, so fall back to
                # normal mode whenever the effective models diverge. When the
                # models cannot be determined, keep fork.
                main_model = getattr(getattr(main_runner, "req", None), "model", None)
                if isinstance(main_model, str) and main_model:
                    sub_model = None
                    try:
                        sub_provider = await ctx.provider_manager.get_provider_by_id(
                            prov_id
                        )
                        sub_model = (
                            sub_provider.get_model()
                            if sub_provider is not None
                            else None
                        )
                    except Exception:
                        sub_model = None
                    if (
                        isinstance(sub_model, str)
                        and sub_model
                        and sub_model != main_model
                    ):
                        logger.info(
                            "[SubAgent:Fork] main agent model %s != subagent model %s; "
                            "fork prefix cache cannot hit, falling back to normal mode",
                            main_model,
                            sub_model,
                        )
                        inherit_mode = "normal"
                        use_fork_context = False

        if use_fork_context:
            # Fork mode: byte-identical inheritance of the main agent's message
            # prefix. The handoff tool call itself is not in
            # run_context.messages yet (it is appended only after tool
            # execution), so the snapshot ends at the current user message -
            # exactly the prefix the provider has already seen for the main
            # agent.
            contexts = [msg.model_dump() for msg in run_context.messages]
            toolset, fork_schema_mode = cls._build_fork_toolset(run_context, tool)
            prompt_text = cls._build_fork_prompt(
                agent_name, subagent_system_prompt, input_
            )
            system_prompt = ""
            # Inherit the main agent's per-request LLM params (e.g.
            # thinking_effort). Providers map them onto request fields such as
            # reasoning_effort that some backends render into the prompt, so a
            # fork request without them would diverge from the cached prefix at
            # token 0. Shallow-copied because the main runner keeps running
            # while a background handoff executes.
            main_llm_params = getattr(
                getattr(main_runner, "req", None), "llm_params", None
            )
            fork_llm_params = (
                dict(main_llm_params) if isinstance(main_llm_params, dict) else {}
            )
        else:
            # Build handoff toolset from registered tools plus runtime computer tools.
            toolset = cls._build_handoff_toolset(run_context, tool.agent.tools)
            fork_schema_mode = None
            prompt_text = input_
            system_prompt = subagent_system_prompt
            fork_llm_params = {}

            # prepare begin dialogs
            contexts = None
            dialogs = tool.agent.begin_dialogs
            if dialogs:
                contexts = []
                for dialog in dialogs:
                    try:
                        contexts.append(
                            dialog
                            if isinstance(dialog, Message)
                            else Message.model_validate(dialog)
                        )
                    except Exception:
                        continue

        agent_max_step = coerce_int_config(
            config.get("agent_runner", {})
            .get("config", {})
            .get("misc", {})
            .get("max_steps", 30),
            default=30,
            min_value=1,
            field_name="agent_runner.config.misc.max_steps",
        )
        stream = prov_settings.get("streaming_response", False)
        # Progress sink for webchat ChatUI (None when platform/config opts out).
        sink = cls._maybe_create_subagent_sink(event, prov_settings, agent_name, input_)

        # Create trace span for subagent execution
        from astrbot.core.utils.trace import TraceSpan

        parent_trace = getattr(event, "trace", None)
        subagent_trace = TraceSpan(
            name=f"SubAgent:{agent_name}",
            umo=event.unified_msg_origin,
            sender_name=event.get_sender_name()
            if hasattr(event, "get_sender_name")
            else None,
            message_outline=f"Handoff to {agent_name}: {input_[:100] if input_ else ''}",
            parent_span_id=parent_trace.span_id if parent_trace else None,
        )
        subagent_trace.record(
            "subagent_execution_begin",
            agent_name=agent_name,
            input=input_ if input_ else None,
            image_count=len(image_urls),
            tools=[t.name for t in toolset] if toolset else [],
            max_steps=agent_max_step,
            stream=stream,
        )
        subagent_trace.record("subagent_context_inherit_mode", mode=inherit_mode)

        if not use_fork_context:
            # Fork mode skips per-subagent history: prepending it would break
            # the inherited prefix, and the main context already carries
            # everything relevant to this turn.
            # 获取子代理的历史上下文
            subagent_history, agent_name = cls._load_subagent_history(umo, tool)
            # 如果有历史上下文，合并到 contexts 中
            if subagent_history:
                subagent_trace.record(
                    "subagent_history_loaded",
                    agent_name=agent_name,
                    history_messages_count=len(subagent_history),
                )
                if contexts is None:
                    contexts = subagent_history
                else:
                    contexts = subagent_history + contexts

        subagent_trace.record(
            "subagent_system_prompt",
            agent_name=agent_name,
            prompt_length=len(subagent_system_prompt),
            prompt=subagent_system_prompt if subagent_system_prompt else None,
        )

        # 构建子代理的追加内容
        extra_content_parts = SubAgentManager.build_subagent_extra_content_parts(
            umo, agent_name
        )

        # 获取子代理的超时时间
        execution_timeout = cls._get_subagent_execution_timeout()

        # 用于存储本轮的完整历史上下文
        runner_messages = []

        # Mark the subagent's agent context so the agent runner denies
        # orchestration and handoff tool calls from within a subagent. Their
        # schemas stay visible in the payload (fork mode needs byte-identical
        # tool segments for prefix-cache consistency); execution is rejected
        # with a tool-result error the subagent can perceive.
        denied_subagent_tools = SubAgentManager.get_main_agent_only_tools()
        if toolset:
            denied_subagent_tools |= {
                t.name for t in toolset.tools if isinstance(t, HandoffTool)
            }
        subagent_agent_context = AstrAgentContext(
            context=ctx,
            event=event,
            trace_span=subagent_trace,
            extra={
                "is_subagent": True,
                "subagent_name": agent_name,
                "denied_tools": denied_subagent_tools,
            },
        )

        # 构建 tool_loop_agent 协程
        async def _run_subagent():
            return await ctx.tool_loop_agent(
                event=event,
                chat_provider_id=prov_id,
                prompt=prompt_text,
                image_urls=image_urls,
                system_prompt=system_prompt,
                tools=toolset,
                contexts=contexts,
                max_steps=agent_max_step,
                tool_call_timeout=run_context.tool_call_timeout,
                stream=stream,
                runner_messages=runner_messages,
                extra_user_content_parts=extra_content_parts,
                trace_span=subagent_trace,
                response_sink=sink,
                agent_context=subagent_agent_context,
                **({"tool_schema_mode": fork_schema_mode} if fork_schema_mode else {}),
                **({"llm_params": fork_llm_params} if fork_llm_params else {}),
            )

        # 添加执行超时控制
        if sink:
            await sink.start()
        try:
            if execution_timeout > 0:
                llm_resp = await asyncio.wait_for(
                    _run_subagent(), timeout=execution_timeout
                )
            else:
                # 不设置超时
                llm_resp = await _run_subagent()
        except asyncio.TimeoutError:
            # 若超时，保存已产生的部分历史
            if not use_fork_context:
                cls._save_subagent_history(umo, runner_messages, agent_name)
            subagent_trace.record(
                "subagent_execution_timeout",
                timeout_seconds=execution_timeout,
            )
            error_msg = f"SubAgent '{agent_name}' execution timeout after {execution_timeout:.1f} seconds."
            logger.warning(f"[SubAgent:Timeout] {error_msg}")

            cls._handle_subagent_timeout(umo=umo, agent_name=agent_name)

            if sink:
                await sink.fail("timeout", error_msg)
            yield mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"error: {error_msg}")]
            )
            return
        except Exception as exc:
            if sink:
                await sink.fail("failed", str(exc))
            raise

        execution_time = time.time() - subagent_trace.started_at
        subagent_trace.record(
            "subagent_execution_complete",
            agent_name=agent_name,
            result=llm_resp.completion_text
            if hasattr(llm_resp, "completion_text") and llm_resp.completion_text
            else None,
            result_length=len(llm_resp.completion_text)
            if hasattr(llm_resp, "completion_text") and llm_resp.completion_text
            else 0,
            execution_time=execution_time,
        )

        # 保存历史上下文
        if not use_fork_context:
            cls._save_subagent_history(umo, runner_messages, agent_name)
            subagent_trace.record(
                "subagent_history_saved",
                messages_count=len(runner_messages),
            )

        if sink:
            await sink.complete(
                llm_resp.completion_text
                if hasattr(llm_resp, "completion_text") and llm_resp.completion_text
                else "",
                execution_time,
            )

        yield mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=llm_resp.completion_text)]
        )

    @classmethod
    async def _execute_handoff_background(
        cls,
        tool: HandoffTool,
        run_context: ContextWrapper[AstrAgentContext],
        **tool_args,
    ):
        """Execute a handoff as a background task.

        Immediately yields a success response with a task_id, then runs
        the subagent asynchronously.  When the subagent finishes, a
        ``CronMessageEvent`` is created so the main LLM can inform the
        user of the result – the same pattern used by
        ``_execute_background`` for regular background tasks.

        当启用增强SubAgent时，会在 SubAgentManager 中创建 pending 任务，
        并返回 task_id 给主 Agent，以便后续通过 wait_for_subagent 获取结果。
        """
        event = run_context.context.event
        umo = event.unified_msg_origin
        agent_name = getattr(tool.agent, "name", None)

        # check if enhanced subagent
        subagent_task_id = cls._register_subagent_task(umo, agent_name)

        original_task_id = uuid.uuid4().hex

        # Create trace span for background task creation
        from astrbot.core.utils.trace import TraceSpan

        parent_trace = getattr(event, "trace", None)
        bg_trace = TraceSpan(
            name=f"SubAgentBackground:{agent_name}",
            umo=event.unified_msg_origin,
            sender_name=event.get_sender_name()
            if hasattr(event, "get_sender_name")
            else None,
            message_outline=f"Background handoff to {agent_name}",
            parent_span_id=parent_trace.span_id if parent_trace else None,
        )
        bg_trace.record(
            "subagent_background_task_created",
            agent_name=agent_name,
            subagent_task_id=subagent_task_id,
            original_task_id=original_task_id,
        )

        async def _run_handoff_in_background() -> None:
            try:
                await cls._do_handoff_background(
                    tool=tool,
                    run_context=run_context,
                    task_id=original_task_id,
                    subagent_task_id=subagent_task_id,
                    **tool_args,
                )

            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Background handoff {original_task_id} ({tool.name}) failed: {e!s}",
                    exc_info=True,
                )

        asyncio.create_task(_run_handoff_in_background())

        text_content = cls._build_background_submission_message(
            agent_name, original_task_id, subagent_task_id
        )
        yield mcp.types.CallToolResult(content=[text_content])

    @classmethod
    async def _do_handoff_background(
        cls,
        tool: HandoffTool,
        run_context: ContextWrapper[AstrAgentContext],
        task_id: str,
        **tool_args,
    ) -> None:
        """Run the subagent handoff.
        当增强版 SubAgent 启用时，结果存储到 SubAgentManager，主 Agent 可通过 wait_for_subagent 获取。
        否则使用原有的 _wake_main_agent_for_background_result 流程。
        """

        start_time = time.time()
        result_text = ""
        error_text = None
        tool_args = dict(tool_args)
        tool_args["image_urls"] = await cls._collect_handoff_image_urls(
            run_context,
            tool_args.get("image_urls"),
        )

        event = run_context.context.event
        umo = event.unified_msg_origin
        agent_name = getattr(tool.agent, "name", None)

        # Create trace span for background subagent execution
        from astrbot.core.utils.trace import TraceSpan

        parent_trace = getattr(event, "trace", None)
        bg_trace = TraceSpan(
            name=f"SubAgentBackground:{agent_name}",
            umo=event.unified_msg_origin,
            sender_name=event.get_sender_name()
            if hasattr(event, "get_sender_name")
            else None,
            message_outline=f"Background handoff to {agent_name}",
            parent_span_id=parent_trace.span_id if parent_trace else None,
        )
        bg_trace.record(
            "subagent_background_execution_start",
            agent_name=agent_name,
        )

        # 获取SubAgent的超时时间
        execution_timeout = cls._get_subagent_execution_timeout()

        try:

            async def _run():
                nonlocal result_text
                async for r in cls._execute_handoff(
                    tool,
                    run_context,
                    image_urls_prepared=True,
                    **tool_args,
                ):
                    if isinstance(r, mcp.types.CallToolResult):
                        for content in r.content:
                            if isinstance(content, mcp.types.TextContent):
                                result_text += content.text + "\n"

            if execution_timeout > 0:
                await asyncio.wait_for(_run(), timeout=execution_timeout)
            else:
                await _run()

        except asyncio.TimeoutError:
            error_text = f"Execution timeout after {execution_timeout:.1f} seconds."
            result_text = f"error: Background SubAgent '{agent_name}' {error_text}"
            logger.warning(f"[SubAgent:BackgroundTask] {error_text}")

        except Exception as e:
            error_text = str(e)
            result_text = (
                f"error: Background task execution failed, internal error: {e!s}"
            )

        execution_time = time.time() - start_time
        bg_trace.record(
            "subagent_background_execution_end",
            agent_name=agent_name,
            success=error_text is None,
            result_preview=result_text[:500] if result_text else None,
            execution_time=execution_time,
        )

        # Check if it's enhanced subagent
        is_managed = cls._is_managed_subagent(umo, agent_name)
        if is_managed:
            await cls._handle_subagent_background_result(
                umo=umo,
                agent_name=agent_name,
                task_id=tool_args.get("subagent_task_id"),
                result_text=result_text,
                error_text=error_text,
                execution_time=execution_time,
                run_context=run_context,
                tool=tool,
                tool_args=tool_args,
            )
        else:
            await cls._wake_main_agent_for_background_result(
                run_context=run_context,
                task_id=task_id,
                tool_name=tool.name,
                result_text=result_text,
                tool_args=tool_args,
                note=(
                    event.get_extra("background_note")
                    or f"Background task for subagent '{agent_name}' finished."
                ),
                summary_name=f"Dedicated to subagent `{agent_name}`",
                extra_result_fields={"subagent_name": agent_name},
            )

    @classmethod
    async def _execute_background(
        cls,
        tool: FunctionTool,
        run_context: ContextWrapper[AstrAgentContext],
        task_id: str,
        **tool_args,
    ) -> None:
        # run the tool
        result_text = ""
        try:
            async for r in cls._execute_local(
                tool, run_context, tool_call_timeout=3600, **tool_args
            ):
                # collect results, currently we just collect the text results
                if isinstance(r, mcp.types.CallToolResult):
                    result_text = ""
                    for content in r.content:
                        if isinstance(content, mcp.types.TextContent):
                            result_text += content.text + "\n"
        except Exception as e:
            result_text = (
                f"error: Background task execution failed, internal error: {e!s}"
            )

        event = run_context.context.event

        await cls._wake_main_agent_for_background_result(
            run_context=run_context,
            task_id=task_id,
            tool_name=tool.name,
            result_text=result_text,
            tool_args=tool_args,
            note=(
                event.get_extra("background_note")
                or f"Background task {tool.name} finished."
            ),
            summary_name=tool.name,
        )

    @classmethod
    async def _wake_main_agent_for_background_result(
        cls,
        run_context: ContextWrapper[AstrAgentContext],
        *,
        task_id: str,
        tool_name: str,
        result_text: str,
        tool_args: dict[str, T.Any],
        note: str,
        summary_name: str,
        extra_result_fields: dict[str, T.Any] | None = None,
    ) -> None:
        from astrbot.core.astr_main_agent import (
            MainAgentBuildConfig,
            _get_session_conv,
            build_main_agent,
        )

        event = run_context.context.event
        ctx = run_context.context.context

        task_result = {
            "task_id": task_id,
            "tool_name": tool_name,
            "result": result_text or "",
            "tool_args": tool_args,
        }
        if extra_result_fields:
            task_result.update(extra_result_fields)
        extras = {"background_task_result": task_result}

        session = MessageSession.from_str(event.unified_msg_origin)
        cron_event = CronMessageEvent(
            context=ctx,
            session=session,
            message=note,
            extras=extras,
            message_type=session.message_type,
        )
        cron_event.role = event.role
        cfg = ctx.get_config(umo=event.unified_msg_origin) or {}
        provider_settings = cfg.get("provider_settings") or {}
        agent_max_step = coerce_int_config(
            cfg.get("agent_runner", {})
            .get("config", {})
            .get("misc", {})
            .get("max_steps", 30),
            default=30,
            min_value=1,
            field_name="agent_runner.config.misc.max_steps",
        )
        config = MainAgentBuildConfig(
            tool_call_timeout=run_context.tool_call_timeout,
            tool_call_timeout_exclude=run_context.tool_call_timeout_exclude,
            streaming_response=provider_settings.get("stream", False),
            provider_settings=provider_settings,
        )

        req = ProviderRequest()
        conv = await _get_session_conv(event=cron_event, plugin_context=ctx)
        req.conversation = conv
        req.contexts = json.loads(conv.history)

        bg = json.dumps(extras["background_task_result"], ensure_ascii=False)
        req.system_prompt += BACKGROUND_TASK_RESULT_WOKE_SYSTEM_PROMPT.format(
            background_task_result=bg
        )
        req.prompt = (
            "Proceed according to your system instructions. "
            "Output using same language as previous conversation. "
            "If you need to deliver the result to the user immediately, "
            "you MUST use `send_message_to_user` tool to send the message directly to the user, "
            "otherwise the user will not see the result. "
            "After completing your task, summarize and output your actions and results. "
        )
        if not req.func_tool:
            req.func_tool = ToolSet()
        req.func_tool.add_tool(
            ctx.get_llm_tool_manager().get_builtin_tool(SendMessageToUserTool)
        )

        result = await build_main_agent(
            event=cron_event, plugin_context=ctx, config=config, req=req
        )
        if not result:
            logger.error(f"Failed to build main agent for background task {tool_name}.")
            return

        runner = result.agent_runner
        async for _ in runner.step_until_done(agent_max_step):
            # agent will send message to user via using tools
            pass
        llm_resp = runner.get_final_llm_resp()
        task_meta = extras.get("background_task_result", {})
        summary_note = (
            f"[BackgroundTask] {summary_name} "
            f"(task_id={task_meta.get('task_id', task_id)}) finished. "
            f"Result: {task_meta.get('result') or result_text or 'no content'}"
        )
        if llm_resp and llm_resp.completion_text:
            summary_note += (
                f"I finished the task, here is the result: {llm_resp.completion_text}"
            )
        await persist_agent_history(
            ctx.conversation_manager,
            event=cron_event,
            req=req,
            summary_note=summary_note,
        )
        if not llm_resp:
            logger.warning("background task agent got no response")
            return

    @classmethod
    async def _execute_local(
        cls,
        tool: FunctionTool,
        run_context: ContextWrapper[AstrAgentContext],
        *,
        tool_call_timeout: int | None = None,
        **tool_args,
    ):
        event = run_context.context.event
        if not event:
            raise ValueError("Event must be provided for local function tools.")

        is_override_call = False
        for ty in type(tool).mro():
            if "call" in ty.__dict__ and ty.__dict__["call"] is not FunctionTool.call:
                is_override_call = True
                break

        # 检查 tool 下有没有 run 方法
        if not tool.handler and not hasattr(tool, "run") and not is_override_call:
            raise ValueError("Tool must have a valid handler or override 'run' method.")

        awaitable = None
        method_name = ""
        if tool.handler:
            awaitable = tool.handler
            method_name = "decorator_handler"
        elif is_override_call:
            awaitable = tool.call
            method_name = "call"
        elif hasattr(tool, "run"):
            awaitable = getattr(tool, "run")
            method_name = "run"
        if awaitable is None:
            raise ValueError("Tool must have a valid handler or override 'run' method.")

        wrapper = call_local_llm_tool(
            context=run_context,
            handler=awaitable,
            method_name=method_name,
            **tool_args,
        )
        while True:
            try:
                timeout: int | None = None
                if tool.name in run_context.tool_call_timeout_exclude:
                    # Excluded tools wait indefinitely: they manage their own
                    # waiting (e.g. polling a subagent or waiting for user
                    # input) and always return on their own.
                    resp = await anext(wrapper)
                else:
                    timeout = tool_call_timeout or run_context.tool_call_timeout
                    resp = await asyncio.wait_for(
                        anext(wrapper),
                        timeout=timeout,
                    )
                if resp is not None:
                    if isinstance(resp, mcp.types.CallToolResult):
                        yield resp
                    else:
                        text_content = mcp.types.TextContent(
                            type="text",
                            text=str(resp),
                        )
                        yield mcp.types.CallToolResult(content=[text_content])
                else:
                    # NOTE: Tool 在这里直接请求发送消息给用户
                    # TODO: 是否需要判断 event.get_result() 是否为空?
                    # 如果为空,则说明没有发送消息给用户,并且返回值为空,将返回一个特殊的 TextContent,其内容如"工具没有返回内容"
                    if res := run_context.context.event.get_result():
                        if res.chain:
                            try:
                                await event.send(
                                    MessageChain(
                                        chain=res.chain,
                                        type="tool_direct_result",
                                    )
                                )
                            except Exception as e:
                                logger.error(
                                    f"Tool 直接发送消息失败: {e}",
                                    exc_info=True,
                                )
                    yield None
            except asyncio.TimeoutError:
                raise Exception(
                    f"tool {tool.name} execution timeout after {timeout} seconds.",
                )
            except StopAsyncIteration:
                break

    @classmethod
    async def _execute_mcp(
        cls,
        tool: FunctionTool,
        run_context: ContextWrapper[AstrAgentContext],
        **tool_args,
    ):
        res = await tool.call(run_context, **tool_args)
        if not res:
            return
        yield res

    @staticmethod
    def _load_subagent_history(
        umo: str, tool: HandoffTool
    ) -> tuple[list[Message], str]:
        agent_name = getattr(tool.agent, "name", None)
        subagent_history = []
        if agent_name:
            # 仅在历史功能启用时加载历史
            if SubAgentManager.is_history_enabled():
                try:
                    stored_history = SubAgentManager.get_subagent_history(
                        umo, agent_name
                    )
                    if stored_history:
                        # 将历史消息转换为 Message 对象
                        for hist_msg in stored_history:
                            try:
                                if isinstance(hist_msg, dict):
                                    subagent_history.append(
                                        Message.model_validate(hist_msg)
                                    )
                                elif isinstance(hist_msg, Message):
                                    subagent_history.append(hist_msg)
                            except Exception:
                                continue
                        if subagent_history:
                            logger.debug(
                                f"[SubAgentHistory] Loaded {len(subagent_history)} history messages for {agent_name}"
                            )

                except Exception as e:
                    logger.warning(
                        f"[SubAgentHistory] Failed to load history for {agent_name}: {e}"
                    )
            else:
                logger.debug(
                    f"[SubAgentHistory] History is disabled, skipping load for {agent_name}"
                )
        return subagent_history, agent_name

    @staticmethod
    def _build_subagent_system_prompt(
        umo: str, tool: HandoffTool, prov_settings: dict
    ) -> str:
        agent_name = getattr(tool.agent, "name", None)
        base = tool.agent.instructions or ""
        subagent_system_prompt = (
            f"# Role\nYour name is **{agent_name}** (used for tool calling)\n{base}\n"
        )
        if agent_name:
            runtime = prov_settings.get("computer_use_runtime", "local")
            subagent_system_prompt += SubAgentManager.build_subagent_system_prompt(
                umo, agent_name, runtime
            )
        return subagent_system_prompt

    @staticmethod
    def _build_fork_prompt(
        agent_name: str, subagent_system_prompt: str, input_: T.Any
    ) -> str:
        """Build the fork-mode instruction appended after the inherited prefix.

        Args:
            agent_name: Name of the forked subagent.
            subagent_system_prompt: The subagent's own instruction block.
            input_: The delegated task text from the handoff tool call.

        Returns:
            The user message text appended after the inherited context.
        """
        return (
            "[SUBAGENT MODE: FORK] You are now acting as the subagent "
            f'"{agent_name}", forked from the orchestrating agent.\n'
            "The conversation above is inherited from the orchestrator. "
            "Do not re-answer it; focus only on the task below.\n"
            f"{subagent_system_prompt}\n\n"
            f"## Your Task\n{input_ if input_ is not None else ''}"
        )

    @classmethod
    def _build_fork_toolset(
        cls,
        run_context: ContextWrapper[AstrAgentContext],
        tool: HandoffTool,
    ) -> tuple[ToolSet | None, str | None]:
        """Inherit the main agent's tool set for a forked subagent.

        The inherited ToolSet object serializes byte-identically to what the
        main agent already sent, so the provider prefix cache also covers the
        tools block.

        Args:
            run_context: The main agent's run context.
            tool: The handoff tool being executed.

        Returns:
            A tuple of (toolset, tool_schema_mode). tool_schema_mode is set to
            "skills_like" when the main agent runs in that mode so the
            subagent runner applies the same two-stage schema handling. Falls
            back to the subagent's configured toolset when the main runner is
            unavailable.
        """
        main_runner = (run_context.context.extra or {}).get("main_agent_runner")
        if main_runner is None:
            logger.warning(
                "[SubAgent:Fork] Main agent runner not found in run context; "
                "falling back to normal-mode toolset construction."
            )
            return cls._build_handoff_toolset(run_context, tool.agent.tools), None
        schema_mode = None
        if getattr(main_runner, "tool_schema_mode", "full") == "skills_like":
            schema_mode = "skills_like"
        return main_runner.effective_raw_tool_set, schema_mode

    @classmethod
    async def _resolve_auto_inherit_mode(
        cls,
        run_context: ContextWrapper[AstrAgentContext],
        ctx: T.Any,
        prov_id: str,
        agent_name: str,
        subagent_system_prompt: str,
        input_: T.Any,
    ) -> str:
        """Choose between fork and normal mode for one delegation.

        Forking only pays off while the subagent's ContextManager will not
        compress the inherited prefix early in its run, so this uses the
        same estimator (EstimateTokenCounter) and the same
        max_context_tokens the subagent runner will apply on its first
        step, but a decision line well below the compression trigger
        (``_AUTO_FORK_MAX_USAGE_RATIO``): the estimate does not include
        per-request tool schemas, and the subagent's own steps consume
        unbounded tokens on top of the prefix, so only a prefix that
        leaves ample run headroom keeps the fork beneficial.

        Args:
            run_context: The main agent's run context.
            ctx: The plugin Context (for provider resolution).
            prov_id: The chat provider id the subagent will use.
            agent_name: Name of the delegated subagent.
            subagent_system_prompt: The subagent's instruction block.
            input_: The delegated task text.

        Returns:
            "fork" or "normal".
        """
        try:
            provider = await ctx.provider_manager.get_provider_by_id(prov_id)
        except Exception:
            provider = None

        max_context_tokens = 0
        if provider is not None:
            try:
                max_context_tokens = int(
                    provider.provider_config.get("max_context_tokens", 0) or 0
                )
            except (TypeError, ValueError):
                max_context_tokens = 0

        # Token guarding disabled (or provider unknown): compression never
        # triggers, the inherited prefix stays intact.
        if max_context_tokens <= 0:
            logger.debug(
                "[SubAgent:Fork] auto mode -> fork (no max_context_tokens limit)"
            )
            return "fork"

        fork_prompt = cls._build_fork_prompt(agent_name, subagent_system_prompt, input_)
        estimate_messages = list(run_context.messages) + [
            Message(role="user", content=fork_prompt)
        ]
        estimated_tokens = EstimateTokenCounter().count_tokens(estimate_messages)
        mode = (
            "fork"
            if estimated_tokens <= max_context_tokens * _AUTO_FORK_MAX_USAGE_RATIO
            else "normal"
        )
        logger.debug(
            "[SubAgent:Fork] auto mode -> %s (estimated %d tokens, limit %d, auto-fork line %.2f)",
            mode,
            estimated_tokens,
            max_context_tokens,
            _AUTO_FORK_MAX_USAGE_RATIO,
        )
        return mode

    @staticmethod
    def _save_subagent_history(
        umo: str, runner_messages: list[Message], agent_name: str
    ) -> None:
        if agent_name and runner_messages:
            # 仅在历史功能启用时保存历史
            if SubAgentManager.is_history_enabled():
                SubAgentManager.update_subagent_history(
                    umo, agent_name, runner_messages
                )
            else:
                logger.debug(
                    f"[SubAgentHistory] History is disabled, skipping save for {agent_name}"
                )
        else:
            return

    @staticmethod
    def _register_subagent_task(umo: str, agent_name: str | None) -> str | None:
        if not agent_name:
            return None
        try:
            session = SubAgentManager.get_session(umo)
            if session and (agent_name in session.subagents):
                subagent_task_id = SubAgentManager.create_pending_subagent_task(
                    session_id=umo, agent_name=agent_name
                )

                if subagent_task_id.startswith(RET_PENDING_TASK_CREATE_FAILED):
                    logger.info(
                        f"[SubAgent:BackgroundTask] Failed to created background task {subagent_task_id} for {agent_name}"
                    )
                else:
                    SubAgentManager.set_subagent_status(
                        session_id=umo,
                        agent_name=agent_name,
                        status=SubAgentStatus.RUNNING,
                    )

                    logger.info(
                        f"[SubAgent:BackgroundTask] Created background task {subagent_task_id} for {agent_name}"
                    )
                return subagent_task_id
        except Exception as e:
            logger.info(
                f"[SubAgent:BackgroundTask] Failed to created background task for {agent_name}: {e}"
            )
            return None

    @staticmethod
    def _build_background_submission_message(
        agent_name: str | None,
        original_task_id: str,
        subagent_task_id: str | None,
    ) -> mcp.types.TextContent:
        if subagent_task_id and not subagent_task_id.startswith(
            RET_PENDING_TASK_CREATE_FAILED
        ):
            return mcp.types.TextContent(
                type="text",
                text=(
                    f"Background task submitted. subagent_task_id={subagent_task_id}. "
                    f"SubAgent '{agent_name}' is working on the task. "
                    f"Use wait_for_subagent(subagent_name='{agent_name}', task_id='{subagent_task_id}') to get the result."
                ),
            )
        else:
            return mcp.types.TextContent(
                type="text",
                text=(
                    f"Background task submitted. task_id={original_task_id}. "
                    f"SubAgent '{agent_name}' is working on the task. "
                    f"You will be notified when it finishes."
                ),
            )

    @staticmethod
    def _get_subagent_execution_timeout() -> float:
        try:
            return SubAgentManager.get_execution_timeout()
        except Exception:
            return -1

    @staticmethod
    def _handle_subagent_timeout(
        umo: str,
        agent_name: str,
    ) -> None:
        SubAgentManager.set_subagent_status(
            session_id=umo,
            agent_name=agent_name,
            status=SubAgentStatus.FAILED,
        )

    @staticmethod
    def _is_managed_subagent(umo: str, agent_name: str | None) -> bool:
        if not agent_name:
            return False
        session = SubAgentManager.get_session(umo)
        if session and agent_name in session.subagents:
            return True
        return False

    @classmethod
    async def _handle_subagent_background_result(
        cls,
        *,
        umo: str,
        agent_name: str,
        task_id: str | None,
        result_text: str,
        error_text: str | None,
        execution_time: float,
        run_context: ContextWrapper[AstrAgentContext],
        tool: HandoffTool,
        tool_args: dict,
    ) -> None:
        success = error_text is None
        status = SubAgentStatus.COMPLETED if success else SubAgentStatus.FAILED
        SubAgentManager.set_subagent_status(
            session_id=umo, agent_name=agent_name, status=status
        )

        SubAgentManager.store_subagent_result(
            session_id=umo,
            agent_name=agent_name,
            success=success,
            result=result_text,
            task_id=task_id,
            error=error_text,
            execution_time=execution_time,
        )

        if not await cls._maybe_wake_main_agent_after_background(
            run_context=run_context,
            tool=tool,
            task_id=task_id,
            agent_name=agent_name,
            result_text=result_text,
            tool_args=tool_args,
        ):
            return

    @classmethod
    async def _maybe_wake_main_agent_after_background(
        cls,
        *,
        run_context: ContextWrapper[AstrAgentContext],
        tool: HandoffTool,
        task_id: str,
        agent_name: str | None,
        result_text: str,
        tool_args: dict,
    ) -> bool:
        event = run_context.context.event
        try:
            context_extra = getattr(run_context.context, "extra", None)
            if context_extra and isinstance(context_extra, dict):
                main_agent_runner = context_extra.get("main_agent_runner")
                main_agent_is_running = (
                    main_agent_runner is not None and not main_agent_runner.done()
                )
            else:
                main_agent_is_running = False
        except Exception as e:
            logger.error("Failed to check main agent status: %s", e)
            main_agent_is_running = False  # 异常时尝试通知，避免结果丢失

        if main_agent_is_running:
            return False
        else:
            await cls._wake_main_agent_for_background_result(
                run_context=run_context,
                task_id=task_id,
                tool_name=tool.name,
                result_text=result_text,
                tool_args=tool_args,
                note=(
                    event.get_extra("background_note")
                    or f"Background task for subagent '{agent_name}' finished."
                ),
                summary_name=f"Dedicated to subagent `{agent_name}`",
                extra_result_fields={"subagent_name": agent_name},
            )
            return True


async def call_local_llm_tool(
    context: ContextWrapper[AstrAgentContext],
    handler: T.Callable[
        ...,
        T.Awaitable[MessageEventResult | mcp.types.CallToolResult | str | None]
        | T.AsyncGenerator[MessageEventResult | CommandResult | str | None, None],
    ],
    method_name: str,
    *args,
    **kwargs,
) -> T.AsyncGenerator[T.Any, None]:
    """执行本地 LLM 工具的处理函数并处理其返回结果"""
    ready_to_call = None  # 一个协程或者异步生成器

    trace_ = None

    event = context.context.event

    try:
        if method_name == "run" or method_name == "decorator_handler":
            ready_to_call = handler(event, *args, **kwargs)
        elif method_name == "call":
            ready_to_call = handler(context, *args, **kwargs)
        else:
            raise ValueError(f"未知的方法名: {method_name}")
    except ValueError as e:
        raise Exception(f"Tool execution ValueError: {e}") from e
    except TypeError as e:
        # 获取函数的签名（包括类型），除了第一个 event/context 参数。
        try:
            sig = inspect.signature(handler)
            params = list(sig.parameters.values())
            # 跳过第一个参数（event 或 context）
            if params:
                params = params[1:]

            param_strs = []
            for param in params:
                param_str = param.name
                if param.annotation != inspect.Parameter.empty:
                    # 获取类型注解的字符串表示
                    if isinstance(param.annotation, type):
                        type_str = param.annotation.__name__
                    else:
                        type_str = str(param.annotation)
                    param_str += f": {type_str}"
                if param.default != inspect.Parameter.empty:
                    param_str += f" = {param.default!r}"
                param_strs.append(param_str)

            handler_param_str = (
                ", ".join(param_strs) if param_strs else "(no additional parameters)"
            )
        except Exception:
            handler_param_str = "(unable to inspect signature)"

        raise Exception(
            f"Tool handler parameter mismatch, please check the handler definition. Handler parameters: {handler_param_str}"
        ) from e
    except Exception as e:
        trace_ = traceback.format_exc()
        raise Exception(f"Tool execution error: {e}. Traceback: {trace_}") from e

    if not ready_to_call:
        return

    if inspect.isasyncgen(ready_to_call):
        _has_yielded = False
        try:
            async for ret in ready_to_call:
                # 这里逐步执行异步生成器, 对于每个 yield 返回的 ret, 执行下面的代码
                # 返回值只能是 MessageEventResult 或者 None（无返回值）
                _has_yielded = True
                if isinstance(ret, MessageEventResult | CommandResult):
                    # 如果返回值是 MessageEventResult, 设置结果并继续
                    event.set_result(ret)
                    yield
                else:
                    # 如果返回值是 None, 则不设置结果并继续
                    # 继续执行后续阶段
                    yield ret
            if not _has_yielded:
                # 如果这个异步生成器没有执行到 yield 分支
                yield
        except Exception as e:
            logger.error(f"Previous Error: {trace_}")
            raise e
    elif inspect.iscoroutine(ready_to_call):
        # 如果只是一个协程, 直接执行
        ret = await ready_to_call
        if isinstance(ret, MessageEventResult | CommandResult):
            event.set_result(ret)
            yield
        else:
            yield ret
