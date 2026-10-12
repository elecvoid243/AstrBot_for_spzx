"""Contracted submissions through real provider requests and public workflow seams."""

import copy
import json
from types import SimpleNamespace

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from google.genai import types
from jsonschema import Draft202012Validator

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
from tests.agent_teams.test_workflow_artifacts import scripted_workflow
from tests.agent_teams.test_workflow_assets import completed_run, workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["claude", "gemini"])
@pytest.mark.parametrize(
    "contract_form", ["nested_ref", "allOf", "root_ref", "default"]
)
@pytest.mark.parametrize("tool_schema_mode", ["full", "skills_like"])
async def test_provider_requests_can_correct_and_submit_frozen_contract_results(
    workflow_env, scripted_workflow, provider_name, contract_form, tool_schema_mode
):
    env = workflow_env
    contract = {
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
        "additionalProperties": False,
    }
    if contract_form == "nested_ref":
        contract["$defs"] = {"flag": {"type": "boolean"}}
        contract["properties"]["approved"] = {"$ref": "#/$defs/flag"}
    elif contract_form == "allOf":
        contract = {"type": "object", "allOf": [contract]}
    elif contract_form == "root_ref":
        contract = {
            "type": "object",
            "$defs": {"report": contract},
            "$ref": "#/$defs/report",
        }
    else:
        contract = None
    asset = await env.service.save_asset(
        "alice",
        {"definition": {**env.asset["definition"], "output_contract": contract}},
    )
    config = {
        "id": "contract-model",
        "model": "contract-model",
        "key": ["test-key"],
        "api_base": "https://provider.test",
    }
    provider = (
        ProviderAnthropic(config, {})
        if provider_name == "claude"
        else ProviderGoogleGenAI(config, {})
    )
    original_client = provider.client
    requests = []
    submissions = []

    async def request_model(**kwargs):
        """Replace only the external SDK request while keeping provider conversion.

        Args:
            **kwargs: Actual provider request, including its converted declarations.

        Returns:
            A typed SDK tool call whose arguments fit the advertised declaration.
        """
        if provider_name == "claude":
            declaration = next(
                tool for tool in kwargs["tools"] if tool["name"] == "submit_task_result"
            )
            parameters = declaration["input_schema"]
            wire_parameters = copy.deepcopy(parameters)
            description = declaration.get("description", "")
        else:
            declaration = next(
                declaration
                for tool in kwargs["config"].tools
                for declaration in tool.function_declarations or []
                if declaration.name == "submit_task_result"
            )
            parameters = declaration.parameters.model_dump(
                mode="json", exclude_none=True
            )
            wire_parameters = copy.deepcopy(parameters)
            description = declaration.description or ""
            # Gemini SDK enums spell types in uppercase; JSON Schema uses lowercase.
            remaining = [parameters]
            while remaining:
                value = remaining.pop()
                if isinstance(value, dict):
                    if "type" in value:
                        value["type"] = value["type"].lower()
                    remaining.extend(value.values())
                elif isinstance(value, list):
                    remaining.extend(value)
        properties = parameters.get("properties", {})
        descriptions = [
            description,
            properties.get("result_json", {}).get("description", ""),
        ]
        contract_present = contract is None or any(
            "{" in item and json.loads(item[item.index("{") :]) == contract
            for item in descriptions
        )
        stage = (
            "final"
            if len(submissions) == 2
            else "parameters"
            if properties
            else "selection"
        )
        feedback_present = (
            "result.approved" if contract is not None else "Provide non-empty text"
        ) in str(kwargs)
        requests.append(
            {
                "stage": stage,
                "contract_present": contract_present,
                "feedback_present": feedback_present,
                "tool_description": description,
                "parameters": wire_parameters,
            }
        )
        if stage == "parameters":
            assert contract_present, "Actual parameter request lost the frozen contract"
            if submissions:
                assert feedback_present
                with pytest.raises(RoleAssetError, match="no completed result"):
                    await service.get_result("alice", env.observations[-1][0].run_id)
            result = (
                {"approved": None if not submissions else True}
                if contract is not None
                else {"text": " " if not submissions else "Reviewed result."}
            )
            arguments = (
                {"result_json": json.dumps(result)} if contract is not None else result
            )
            submissions.append(result)
        else:
            arguments = {}
        if stage != "final":
            Draft202012Validator(parameters).validate(arguments)
        if provider_name == "claude":
            return Message(
                id="model-submit",
                content=[TextBlock(type="text", text="Done")]
                if stage == "final"
                else [
                    ToolUseBlock(
                        type="tool_use",
                        id=f"submit-{len(requests)}",
                        name="submit_task_result",
                        input=arguments,
                    )
                ],
                model="contract-model",
                role="assistant",
                stop_reason="end_turn" if stage == "final" else "tool_use",
                stop_sequence=None,
                type="message",
                usage=Usage(input_tokens=1, output_tokens=1),
            )
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(text="Done")
                            if stage == "final"
                            else types.Part(
                                function_call=types.FunctionCall(
                                    name="submit_task_result", args=arguments
                                )
                            )
                        ],
                    ),
                    finish_reason=types.FinishReason.STOP,
                )
            ],
        )

    provider.client = (
        SimpleNamespace(messages=SimpleNamespace(create=request_model))
        if provider_name == "claude"
        else SimpleNamespace(models=SimpleNamespace(generate_content=request_model))
    )

    async def collect(session_id, message_id, member_id):
        """Run actual selection, parameter generation and bound tool execution.

        Args:
            session_id: Isolated task session.
            message_id: Persisted message identity.
            member_id: Frozen task member identity.

        Returns:
            Final model response after correcting the rejected result.
        """
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        runner = ToolLoopAgentRunner()
        await runner.reset(
            provider=provider,
            request=ProviderRequest(
                prompt="Submit the reviewed result.",
                session_id=session_id,
                func_tool=ToolSet(tools),
            ),
            run_context=ContextWrapper(
                context=SimpleNamespace(event=SimpleNamespace(), extra={})
            ),
            tool_executor=FunctionToolExecutor(),
            agent_hooks=BaseAgentRunHooks(),
            streaming=False,
            tool_schema_mode=tool_schema_mode,
        )
        async for _ in runner.step_until_done(4):
            pass
        assert runner.done()
        assert runner.get_final_llm_resp().completion_text == "Done"
        return "Done", []

    service = scripted_workflow(collect)
    try:
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        done = await completed_run(service, run["run_id"])
        assert done["status"] == "completed", done
        assert done["snapshot"]["definition"].get("output_contract") == contract
        assert done["result"] == (
            {"approved": True} if contract is not None else {"text": "Reviewed result."}
        )
        assert [request["stage"] for request in requests] == (
            ["selection", "parameters", "selection", "parameters", "final"]
            if tool_schema_mode == "skills_like"
            else ["parameters", "parameters", "final"]
        )
        assert any(event["kind"] == "result.rejected" for event in done["events"])
        print(
            json.dumps(
                {
                    "provider": provider_name,
                    "mode": tool_schema_mode,
                    "contract_form": contract_form,
                    "requests": requests,
                }
            )
        )
    finally:
        provider.client = original_client
        await provider.terminate()
