from typing import Any, Generic

from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeVar

from .message import Message

TContext = TypeVar("TContext", default=Any)


@dataclass
class ContextWrapper(Generic[TContext]):
    """A context for running an agent, which can be used to pass additional data or state."""

    context: TContext
    messages: list[Message] = Field(default_factory=list)
    """This field stores the llm message context for the agent run, agent runners will maintain this field automatically."""
    tool_call_timeout: float = 120  # Default tool call timeout in seconds
    tool_call_timeout_exclude: list[str] = Field(
        default_factory=lambda: ["wait_for_subagent", "orchestrate_tasks"]
    )
    """Tool names that are excluded from tool_call_timeout and wait indefinitely instead."""


NoContext = ContextWrapper[None]
