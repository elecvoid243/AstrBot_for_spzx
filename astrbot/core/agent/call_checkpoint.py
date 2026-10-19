"""Lossless built-in invocation state, including temporary provider content."""

from dataclasses import asdict

from openai.types.chat.parsed_chat_completion import ParsedChatCompletion

from astrbot.core.agent.message import Message
from astrbot.core.message.components import ComponentTypes
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import (
    AnthropicMessage,
    ChatCompletion,
    GenerateContentResponse,
    LLMResponse,
    Response,
    TokenUsage,
)


def dump_call_messages(messages: list[Message] | list[dict]) -> list[dict]:
    """Serialize effective messages without discarding temporary content.

    Args:
        messages: Actual runner messages, including provider-only framing.

    Returns:
        Complete messages with temporary flags and history checkpoint links.
    """
    result = []
    for message in messages:
        if isinstance(message, dict):
            result.append(message)
            continue
        data = message.model_dump(mode="json")
        if message._no_save:
            data["_no_save"] = True
        if isinstance(message.content, list):
            data["content"] = [
                part.model_dump_for_context() for part in message.content
            ]
        result.append(data)
        if message._checkpoint_after is not None:
            result.append(
                {
                    "role": "_checkpoint",
                    "content": message._checkpoint_after.model_dump(mode="json"),
                }
            )
    return result


def dump_call_response(response: LLMResponse) -> dict:
    """Save the complete provider response independently of display preferences.

    Args:
        response: Complete response, before any dependent tool execution.

    Returns:
        Original fields needed by the standard runner and provider hooks.
    """
    return {
        "role": response.role,
        "completion_text": response.completion_text,
        "result_chain": {
            "type": response.result_chain.type,
            "chain": [component.toDict() for component in response.result_chain.chain],
        }
        if response.result_chain
        else None,
        "tools_call_args": response.tools_call_args,
        "tools_call_name": response.tools_call_name,
        "tools_call_ids": response.tools_call_ids,
        "tools_call_extra_content": response.tools_call_extra_content,
        "reasoning_content": response.reasoning_content,
        "reasoning_signature": response.reasoning_signature,
        "raw_completion": response.raw_completion.model_dump(mode="json")
        if response.raw_completion
        else None,
        "raw_type": (
            "ParsedChatCompletion"
            if isinstance(response.raw_completion, ParsedChatCompletion)
            else type(response.raw_completion).__name__
        )
        if response.raw_completion
        else None,
        "id": response.id,
        "usage": asdict(response.usage) if response.usage else None,
    }


def load_call_response(data: dict) -> LLMResponse:
    """Rebuild the original response using existing provider and message types.

    Args:
        data: Complete saved response, never a process-display summary.

    Returns:
        Provider response consumed by the normal tool loop.

    Raises:
        ValueError: An unknown provider response type cannot be restored safely.
    """
    fields = {key: value for key, value in data.items() if key not in {"raw_type"}}
    if fields["usage"] is not None:
        fields["usage"] = TokenUsage(**fields["usage"])
    if fields["raw_completion"] is not None:
        raw_type = {
            "ChatCompletion": ChatCompletion,
            "ParsedChatCompletion": ParsedChatCompletion,
            # Existing streamed checkpoints used the SDK's generated type name.
            "ParsedChatCompletion[TypeVar]": ParsedChatCompletion,
            "Response": Response,
            "GenerateContentResponse": GenerateContentResponse,
            "Message": AnthropicMessage,
        }.get(data["raw_type"])
        if raw_type is None:
            raise ValueError("Unsupported saved provider response type")
        fields["raw_completion"] = raw_type.model_validate(fields["raw_completion"])
    chain = fields["result_chain"]
    if chain is not None:
        fields["result_chain"] = MessageChain(
            type=chain["type"],
            chain=[
                ComponentTypes[item["type"]](**item["data"]) for item in chain["chain"]
            ],
        )
    return LLMResponse(**fields)
