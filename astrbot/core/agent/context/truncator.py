from astrbot.core.exceptions import ToolProtocolIntegrityError

from ..message import Message
from .round_utils import split_into_rounds
from .token_counter import TokenCounter


class ContextTruncator:
    """Context truncator."""

    def drop_oldest_round(
        self, messages: list[Message], protected_messages: list[Message] | None = None
    ) -> list[Message]:
        """Remove the oldest eligible round without deleting retained context.

        Args:
            messages: Paired model history, including generated summaries.
            protected_messages: Inputs awaiting durable delivery confirmation.

        Returns:
            History without one ordinary old round, or the unchanged list when
            only summaries, system instructions and the current round remain.
        """
        start = next(
            (i for i, message in enumerate(messages) if message.role != "system"),
            len(messages),
        )
        rounds = split_into_rounds(messages[start:])
        for index, round_messages in enumerate(rounds[:-1]):
            if any(
                message.role == "system"
                or message.compaction_id
                or message in (protected_messages or [])
                for message in round_messages
            ):
                continue
            return messages[:start] + [
                message
                for position, group in enumerate(rounds)
                if position != index
                for message in group
            ]
        return messages

    def _has_tool_calls(self, message: Message) -> bool:
        """Check if a message contains tool calls."""
        return (
            message.role == "assistant"
            and message.tool_calls is not None
            and len(message.tool_calls) > 0
        )

    @staticmethod
    def _split_system_rest(
        messages: list[Message],
    ) -> tuple[list[Message], list[Message]]:
        """Split messages into system messages and the rest.

        Returns:
            tuple: (system_messages, non_system_messages)
        """
        first_non_system = 0
        for i, msg in enumerate(messages):
            if msg.role != "system":
                first_non_system = i
                break
        return messages[:first_non_system], messages[first_non_system:]

    @staticmethod
    def _ensure_user_message(
        system_messages: list[Message],
        truncated: list[Message],
        original_messages: list[Message],
    ) -> list[Message]:
        """Ensure the result always contains the first user message right after
        system messages. This is required by many LLM APIs (e.g. Zhipu) that
        mandate a ``user`` message immediately following the ``system`` message.
        """
        if truncated and truncated[0].role == "user":
            return system_messages + truncated

        # Locate the first user message from the *original* list.
        first_user = next((m for m in original_messages if m.role == "user"), None)
        if first_user is None:
            return system_messages + truncated

        return system_messages + [first_user] + truncated

    def fix_messages(self, messages: list[Message]) -> list[Message]:
        """Build a paired model view of completed tool-call batches.

        Args:
            messages: History inspected only after execution has ended or stopped.

        Returns:
            Messages with one result per call, without modifying original records.
            Missing results describe unknown execution state; orphan results are
            excluded only from this view. Existing results retain their order;
            intervening system injections follow the complete result group.

        Raises:
            ToolProtocolIntegrityError: Required identities are absent, repeated
                within a batch, or associated with conflicting protocol results.
        """
        if not messages:
            return messages

        fixed_messages: list[Message] = []
        index = 0
        while index < len(messages):
            msg = messages[index]
            index += 1
            if msg.role == "tool":
                # Orphan results remain in the original execution history.
                continue

            fixed_messages.append(msg)
            if not self._has_tool_calls(msg):
                continue

            call_ids: dict[str, None] = {}
            for call in msg.tool_calls or []:
                call_id = call.get("id") if isinstance(call, dict) else call.id
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ToolProtocolIntegrityError("Tool call is missing its call ID")
                if call_id in call_ids:
                    raise ToolProtocolIntegrityError(
                        f"Duplicate tool call ID in one assistant batch: {call_id}"
                    )
                call_ids[call_id] = None

            results: dict[str, Message] = {}
            system_injections: list[Message] = []
            # A real user message or any new assistant batch closes this scope.
            while index < len(messages) and messages[index].role in {"tool", "system"}:
                result = messages[index]
                index += 1
                if result.role == "system":
                    # Keep instructions without splitting the protocol result group.
                    system_injections.append(result)
                    continue
                call_id = result.tool_call_id
                if not call_id or not call_id.strip():
                    raise ToolProtocolIntegrityError(
                        "Tool result is missing its call ID"
                    )
                if call_id not in call_ids:
                    continue
                if call_id in results:
                    if results[call_id].model_dump() != result.model_dump():
                        raise ToolProtocolIntegrityError(
                            f"Conflicting tool results for call ID: {call_id}"
                        )
                    continue
                results[call_id] = result
                fixed_messages.append(result)

            for call_id in call_ids:
                if call_id not in results:
                    fixed_messages.append(
                        Message(
                            role="tool",
                            tool_call_id=call_id,
                            _tool_result_unknown=True,
                            content=(
                                "No result was received for this tool call. "
                                "Its execution status is unknown; do not assume "
                                "that it succeeded or that it was not executed."
                            ),
                        )
                    )

            fixed_messages.extend(system_injections)

        return fixed_messages

    def truncate_by_turns(
        self,
        messages: list[Message],
        keep_most_recent_turns: int,
        drop_turns: int = 1,
        protected_messages: list[Message] | None = None,
    ) -> list[Message]:
        """
        Turn-based truncation strategy, which drops the oldest turns while keeping the most recent N turns.
        A turn consists of a user message and an assistant message.
        This method ensures that the truncated context list conforms to OpenAI's context format.

        Args:
            messages: The original list of messages in the context.
            keep_most_recent_turns: The number of most recent turns to keep. If set to -1, it means keeping all turns (no truncation).
            drop_turns: The number of turns to drop from the beginning.
            protected_messages: Inputs awaiting durable delivery confirmation.

        Returns:
            The truncated list of messages.
        """
        if keep_most_recent_turns == -1:
            return messages

        if protected_messages or any(message.compaction_id for message in messages):
            target = max(1, keep_most_recent_turns - drop_turns + 1)
            while (
                sum(
                    not any(
                        message.compaction_id
                        or message.role == "system"
                        or message in (protected_messages or [])
                        for message in group
                    )
                    for group in split_into_rounds(messages)
                )
                > target
            ):
                replacement = self.drop_oldest_round(messages, protected_messages)
                if replacement is messages:
                    break
                messages = replacement
            return self.fix_messages(messages)

        system_messages, non_system_messages = self._split_system_rest(messages)

        if len(non_system_messages) // 2 <= keep_most_recent_turns:
            return messages

        num_to_keep = keep_most_recent_turns - drop_turns + 1
        if num_to_keep <= 0:
            truncated_contexts = []
        else:
            truncated_contexts = non_system_messages[-num_to_keep * 2 :]

        # Find the first user message
        index = next(
            (i for i, item in enumerate(truncated_contexts) if item.role == "user"),
            None,
        )
        if index is not None and index > 0:
            truncated_contexts = truncated_contexts[index:]

        result = self._ensure_user_message(
            system_messages, truncated_contexts, messages
        )
        return self.fix_messages(result)

    def truncate_by_dropping_oldest_turns(
        self,
        messages: list[Message],
        drop_turns: int = 1,
    ) -> list[Message]:
        """Drop the oldest N turns, regardless of the number of turns to keep."""
        if drop_turns <= 0:
            return messages

        if any(message.compaction_id for message in messages):
            for _ in range(drop_turns):
                replacement = self.drop_oldest_round(messages)
                if replacement is messages:
                    break
                messages = replacement
            return self.fix_messages(messages)

        system_messages, non_system_messages = self._split_system_rest(messages)

        if len(non_system_messages) // 2 <= drop_turns:
            truncated_non_system = []
        else:
            truncated_non_system = non_system_messages[drop_turns * 2 :]

        # Find the first user message
        index = next(
            (i for i, item in enumerate(truncated_non_system) if item.role == "user"),
            None,
        )
        if index is not None:
            truncated_non_system = truncated_non_system[index:]

        result = self._ensure_user_message(
            system_messages, truncated_non_system, messages
        )
        return self.fix_messages(result)

    def truncate_by_token_budget(
        self,
        messages: list[Message],
        budget_tokens: int,
        token_counter: TokenCounter,
        min_drop_turns: int = 0,
    ) -> list[Message]:
        """Drop the oldest rounds until the remaining tokens fit the budget.

        This strategy is token-aware: starting from the most recent round, it
        keeps rounds while their accumulated token count stays within
        ``budget_tokens`` and drops all older rounds. The latest round is
        always preserved (round-granular). Dropping to the budget at once gives
        the request a large headroom below the compression trigger, so
        subsequent requests can keep a stable prefix and reuse the
        provider-side prefix cache instead of truncating one turn at a time.

        Args:
            messages: The original list of messages in the context.
            budget_tokens: The maximum token budget for the kept context.
            token_counter: Token counter used to estimate each round.
            min_drop_turns: Minimum number of oldest turns to drop; applied
                when the budget alone would drop fewer turns.

        Returns:
            The truncated list of messages.
        """
        if budget_tokens <= 0 or len(messages) <= 2:
            return messages

        if any(message.compaction_id for message in messages):
            dropped = 0
            while (
                dropped < min_drop_turns
                or token_counter.count_tokens(messages) > budget_tokens
            ):
                replacement = self.drop_oldest_round(messages)
                if replacement is messages:
                    break
                messages = replacement
                dropped += 1
            return self.fix_messages(messages)

        system_messages, non_system_messages = self._split_system_rest(messages)

        if non_system_messages:
            rounds = [
                [seg for seg in rnd if isinstance(seg, Message)]
                for rnd in split_into_rounds(non_system_messages)
            ]
            rounds = [rnd for rnd in rounds if rnd]
        else:
            rounds = []

        if len(rounds) <= 1:
            return messages

        # Keep the most recent rounds while the accumulated tokens stay within
        # the budget. The latest round is always kept (round-granular).
        kept_tokens = 0
        keep_start = len(rounds)
        for idx in range(len(rounds) - 1, -1, -1):
            round_tokens = token_counter.count_tokens(rounds[idx])
            if keep_start < len(rounds) and kept_tokens + round_tokens > budget_tokens:
                break
            kept_tokens += round_tokens
            keep_start = idx

        drop_turns = keep_start
        if drop_turns < min_drop_turns:
            drop_turns = min(min_drop_turns, len(rounds) - 1)

        if drop_turns <= 0:
            return messages

        truncated_non_system = [seg for rnd in rounds[drop_turns:] for seg in rnd]

        result = self._ensure_user_message(
            system_messages, truncated_non_system, messages
        )
        return self.fix_messages(result)

    def truncate_by_halving(
        self,
        messages: list[Message],
    ) -> list[Message]:
        """Halve the number of messages, keeping the most recent ones."""
        if len(messages) <= 2:
            return messages

        if any(message.compaction_id for message in messages):
            for _ in range(max(1, len(split_into_rounds(messages)) // 2)):
                replacement = self.drop_oldest_round(messages)
                if replacement is messages:
                    break
                messages = replacement
            return self.fix_messages(messages)

        system_messages, non_system_messages = self._split_system_rest(messages)

        messages_to_delete = len(non_system_messages) // 2
        if messages_to_delete == 0:
            return messages

        truncated_non_system = non_system_messages[messages_to_delete:]

        # Find the first user message
        index = next(
            (i for i, item in enumerate(truncated_non_system) if item.role == "user"),
            None,
        )
        if index is not None:
            truncated_non_system = truncated_non_system[index:]

        result = self._ensure_user_message(
            system_messages, truncated_non_system, messages
        )
        return self.fix_messages(result)
