import abc
import typing as T
from enum import Enum, auto

from astrbot import logger
from astrbot.core.provider.entities import LLMResponse

from ..hooks import BaseAgentRunHooks
from ..response import AgentResponse, AgentResponseData
from ..run_context import ContextWrapper, TContext


class AgentState(Enum):
    """Defines the state of the agent."""

    IDLE = auto()  # Initial state
    RUNNING = auto()  # Currently processing
    PAUSED = auto()  # Waiting for explicit human handback
    DONE = auto()  # Completed
    ERROR = auto()  # Error state


class BaseAgentRunner(T.Generic[TContext]):
    agent_hooks: BaseAgentRunHooks[TContext]
    run_context: ContextWrapper[TContext]
    streaming: bool
    final_llm_resp: LLMResponse | None

    async def _finish_recovered_response(
        self, response: LLMResponse
    ) -> T.AsyncGenerator[AgentResponse, None]:
        """Deliver a recovered response through the normal completion hooks.

        Args:
            response: Complete provider response saved before plugin processing.

        Yields:
            The effective response after normal hooks, including streaming output.
        """
        self.final_llm_resp = response
        self._transition_state(AgentState.DONE)
        try:
            await self.agent_hooks.on_agent_done(self.run_context, response)
        except Exception as exc:
            logger.error("Error in on_agent_done hook: %s", exc, exc_info=True)
        assert response.result_chain is not None
        if self.streaming:
            yield AgentResponse(
                type="streaming_delta",
                data=AgentResponseData(chain=response.result_chain),
            )
        yield AgentResponse(
            type="llm_result", data=AgentResponseData(chain=response.result_chain)
        )

    @abc.abstractmethod
    async def reset(
        self,
        run_context: ContextWrapper[TContext],
        agent_hooks: BaseAgentRunHooks[TContext],
        **kwargs: T.Any,
    ) -> None:
        """Reset the agent to its initial state.
        This method should be called before starting a new run.
        """
        ...

    @abc.abstractmethod
    async def step(self) -> T.AsyncGenerator[AgentResponse, None]:
        """Process a single step of the agent."""
        ...

    @abc.abstractmethod
    async def step_until_done(
        self, max_step: int
    ) -> T.AsyncGenerator[AgentResponse, None]:
        """Process steps until the agent is done."""
        ...

    @abc.abstractmethod
    def done(self) -> bool:
        """Check if the agent has completed its task.
        Returns True if the agent is done, False otherwise.
        """
        ...

    @abc.abstractmethod
    def get_final_llm_resp(self) -> LLMResponse | None:
        """Get the final observation from the agent.
        This method should be called after the agent is done.
        """
        ...

    @property
    def state(self) -> AgentState:
        """The current agent state (read-only; transitions go through
        `_transition_state`)."""
        return self._state

    def _transition_state(self, new_state: AgentState) -> None:
        """Transition the agent state."""
        if self._state != new_state:
            logger.debug(f"Agent state transition: {self._state} -> {new_state}")
            self._state = new_state
