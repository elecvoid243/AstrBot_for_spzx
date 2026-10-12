"""Contracted submissions through real provider requests and public workflow seams."""

import json
from types import SimpleNamespace

import pytest
from anthropic.types import Message, ToolUseBlock, Usage
from google.genai import types
from jsonschema import Draft202012Validator

from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent_team_tools import AgentTeamToolRegistry
from astrbot.core.agent_teams.assets import RoleAssetError
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
from tests.agent_teams.test_workflow_artifacts import scripted_workflow
from tests.agent_teams.test_workflow_assets import completed_run, workflow_env


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["claude", "gemini"])
@pytest.mark.parametrize("contract_form", ["nested_ref", "allOf", "root_ref"])
async def test_provider_requests_can_correct_and_submit_frozen_contract_results(
    workflow_env, scripted_workflow, provider_name, contract_form
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
    else:
        contract = {
            "type": "object",
            "$defs": {"report": contract},
            "$ref": "#/$defs/report",
        }
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
            description = declaration["description"]
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
            description = declaration.description
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
        assert parameters.get("properties"), (
            "The model needs a declared result argument"
        )
        result = {"approved": None if not requests else True}
        if "result_json" in parameters["properties"]:
            arguments = {"result_json": json.dumps(result)}
            assert json.loads(description[description.index("{") :]) == contract
        else:
            arguments = result
        Draft202012Validator(parameters).validate(arguments)
        requests.append(kwargs)
        if provider_name == "claude":
            return Message(
                id="model-submit",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="submit",
                        name="submit_task_result",
                        input=arguments,
                    )
                ],
                model="contract-model",
                role="assistant",
                stop_reason="tool_use",
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
                            types.Part(
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
        """Round-trip real provider tool calls through the bound submission capability.

        Args:
            session_id: Isolated task session.
            message_id: Persisted message identity.
            member_id: Frozen task member identity.

        Returns:
            Final model response after correcting the rejected result.
        """
        tools = AgentTeamToolRegistry.get_tools(f"webchat:FriendMessage:{session_id}")
        prompt = "Submit the reviewed result."
        for step in range(2):
            response = await provider.text_chat(
                prompt=prompt, session_id=session_id, func_tool=ToolSet(tools)
            )
            assert response.tools_call_name == ["submit_task_result"]
            prompt = await tools[0].call(None, **response.tools_call_args[0])
            if step == 0:
                assert "result.approved" in prompt
                with pytest.raises(RoleAssetError, match="no completed result"):
                    await service.get_result("alice", env.observations[-1][0].run_id)
            else:
                assert prompt == "Result accepted."
        return "Done", []

    service = scripted_workflow(collect)
    try:
        run = await service.start_run("alice", asset["asset_id"], {"version": 1})
        done = await completed_run(service, run["run_id"])
        assert done["status"] == "completed", done
        assert done["snapshot"]["definition"]["output_contract"] == contract
        assert done["result"] == {"approved": True}
        assert len(requests) == 2
        assert any(event["kind"] == "result.rejected" for event in done["events"])
    finally:
        provider.client = original_client
        await provider.terminate()
