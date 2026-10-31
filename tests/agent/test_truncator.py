"""Tests for ContextTruncator."""

import copy

import pytest

from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.message import (
    ImageURLPart,
    Message,
    TextPart,
    ToolCall,
    ToolCallMessageSegment,
)
from astrbot.core.exceptions import ToolProtocolIntegrityError


def pairing_batch(*call_ids):
    """Create a batch for public-interface pairing tests.

    Args:
        *call_ids: Identities requested by this assistant message.

    Returns:
        An assistant message containing the requested calls.
    """
    return Message(
        role="assistant",
        tool_calls=[
            {"id": call_id, "function": {"name": "probe", "arguments": "{}"}}
            for call_id in call_ids
        ],
    )


def pairing_result(call_id, content):
    """Create a protocol result for pairing tests.

    Args:
        call_id: Identity referenced by the result.
        content: Original result payload.

    Returns:
        A tool message with the specified content and identity.
    """
    return Message(role="tool", tool_call_id=call_id, content=content)


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (
            [pairing_batch("A", "B"), pairing_result("A", "real A")],
            [("A", "real A"), ("B", None)],
        ),
        ([pairing_batch("A", "B")], [("A", None), ("B", None)]),
        ([pairing_result("orphan", "saved")], []),
        (
            [
                pairing_batch("A", "B"),
                pairing_result("B", "real B"),
                pairing_result("A", "real A"),
            ],
            [("B", "real B"), ("A", "real A")],
        ),
        (
            [
                pairing_batch("A"),
                pairing_result("A", "first"),
                pairing_batch("A"),
                pairing_result("A", "second"),
            ],
            [("A", "first"), ("A", "second")],
        ),
        (
            [
                pairing_batch("A"),
                Message(role="user", content="new request"),
                pairing_result("A", "late"),
            ],
            [("A", None)],
        ),
        (
            [
                pairing_batch("A"),
                pairing_batch("B"),
                pairing_result("A", "late"),
                pairing_result("B", "real B"),
            ],
            [("A", None), ("B", "real B")],
        ),
        (
            [
                pairing_batch("A"),
                pairing_result("A", "same"),
                pairing_result("A", "same"),
            ],
            [("A", "same")],
        ),
        (
            [
                pairing_batch("A"),
                pairing_result("orphan", "saved"),
                pairing_result("A", "real A"),
            ],
            [("A", "real A")],
        ),
    ],
    ids=[
        "partial",
        "all-missing",
        "orphan",
        "reversed",
        "id-reuse",
        "user-boundary",
        "batch-boundary",
        "identical-duplicate",
        "orphan-in-batch",
    ],
)
def test_pairing_public_interface_is_scoped_and_idempotent(messages, expected):
    original = copy.deepcopy(messages)
    truncator = ContextTruncator()
    fixed = truncator.fix_messages(messages)
    results = [message for message in fixed if message.role == "tool"]
    assert len(results) == len(expected)
    for result, (call_id, content) in zip(results, expected):
        assert result.tool_call_id == call_id
        if content is None:
            assert "execution status is unknown" in result.content
            assert "No result was received" in result.content
        else:
            assert result.content == content
    assert truncator.fix_messages(fixed) == fixed
    assert messages == original


@pytest.mark.parametrize(
    "messages",
    [
        [pairing_batch("A", "A")],
        [pairing_batch("")],
        [
            Message(
                role="assistant",
                tool_calls=[{"function": {"name": "probe", "arguments": "{}"}}],
            )
        ],
        [pairing_batch("A"), pairing_result(None, "no identity")],
        [
            pairing_batch("A"),
            pairing_result("A", "first"),
            pairing_result("A", "conflict"),
        ],
    ],
    ids=[
        "duplicate-call",
        "empty-call",
        "missing-call",
        "missing-result",
        "conflicting-result",
    ],
)
def test_pairing_public_interface_rejects_ambiguous_history(messages):
    with pytest.raises(ToolProtocolIntegrityError):
        ContextTruncator().fix_messages(messages)


def test_pairing_valid_typed_history_preserves_media_and_metadata():
    call = Message(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="A",
                function=ToolCall.FunctionBody(name="probe", arguments="{}"),
                extra_content={"signature": "signed"},
            )
        ],
    )
    result = pairing_result(
        "A",
        [
            TextPart(text="evidence"),
            ImageURLPart(
                image_url=ImageURLPart.ImageURL(
                    url="data:image/png;base64,AA==", id="image-evidence"
                )
            ),
        ],
    )
    result._no_save = True
    messages = [call, result]
    fixed = ContextTruncator().fix_messages(messages)
    assert fixed == messages
    assert all(left is right for left, right in zip(messages, fixed))


def test_pairing_duplicate_protocol_is_independent_of_python_message_type():
    result = pairing_result("A", "same")
    duplicate = ToolCallMessageSegment(tool_call_id="A", content="same")
    fixed = ContextTruncator().fix_messages([pairing_batch("A"), result, duplicate])
    assert len(fixed) == 2
    assert fixed[1] is result


class TestContextTruncator:
    """Test suite for ContextTruncator."""

    def create_message(self, role: str, content: str = "test content") -> Message:
        """Helper to create a simple test message."""
        return Message(role=role, content=content)

    def create_messages(
        self, count: int, include_system: bool = False
    ) -> list[Message]:
        """Helper to create alternating user/assistant messages.

        Args:
            count: Number of messages to create
            include_system: Whether to include a system message at the start

        Returns:
            List of messages
        """
        messages = []
        if include_system:
            messages.append(self.create_message("system", "System prompt"))

        for i in range(count):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append(self.create_message(role, f"Message {i}"))
        return messages

    # ==================== fix_messages Tests ====================

    def test_fix_messages_empty_list(self):
        """Test fix_messages with an empty list."""
        truncator = ContextTruncator()
        result = truncator.fix_messages([])
        assert result == []

    def test_fix_messages_normal_messages(self):
        """Test fix_messages with normal user/assistant messages."""
        truncator = ContextTruncator()
        messages = [
            self.create_message("user", "Hello"),
            self.create_message("assistant", "Hi"),
            self.create_message("user", "How are you?"),
        ]
        result = truncator.fix_messages(messages)
        assert len(result) == 3
        assert result == messages

    def test_fix_messages_tool_without_context(self):
        """Test fix_messages with tool message without enough context."""
        truncator = ContextTruncator()
        messages = [
            self.create_message("tool", "Tool result"),
        ]
        result = truncator.fix_messages(messages)
        # Tool message without context should be removed
        assert len(result) == 0

    # ==================== truncate_by_turns Tests ====================

    def test_truncate_by_turns_no_limit(self):
        """Test truncate_by_turns with -1 (no limit)."""
        truncator = ContextTruncator()
        messages = self.create_messages(20)
        result = truncator.truncate_by_turns(messages, keep_most_recent_turns=-1)
        assert len(result) == 20
        assert result == messages

    def test_truncate_by_turns_basic(self):
        """Test basic truncate_by_turns functionality."""
        truncator = ContextTruncator()
        # Create 10 messages = 5 turns (user/assistant pairs)
        messages = self.create_messages(10)
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=3, drop_turns=1
        )

        # Should keep 3 most recent turns (6 messages)
        assert len(result) <= 8  # (3-1+1)*2 = 6, but may adjust for correct format

    def test_truncate_by_turns_with_system_message(self):
        """Test truncate_by_turns preserves system messages."""
        truncator = ContextTruncator()
        messages = self.create_messages(10, include_system=True)
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=2, drop_turns=1
        )

        # System message should always be preserved
        assert result[0].role == "system"
        assert result[0].content == "System prompt"

    def test_truncate_by_turns_zero_keep(self):
        """Test truncate_by_turns with keep_most_recent_turns=0."""
        truncator = ContextTruncator()
        messages = self.create_messages(10)
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=0, drop_turns=1
        )

        # 截断后至少保留一条 user 消息 (#6196)
        assert len(result) >= 1
        assert result[0].role == "user"

    def test_truncate_by_turns_below_threshold(self):
        """Test truncate_by_turns when messages are below threshold."""
        truncator = ContextTruncator()
        # Create 4 messages = 2 turns
        messages = self.create_messages(4)
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=5, drop_turns=1
        )

        # No truncation should happen
        assert len(result) == 4
        assert result == messages

    def test_truncate_by_turns_exact_threshold(self):
        """Test truncate_by_turns when messages exactly match threshold."""
        truncator = ContextTruncator()
        # Create 6 messages = 3 turns
        messages = self.create_messages(6)
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=3, drop_turns=1
        )

        # No truncation should happen
        assert len(result) == 6
        assert result == messages

    def test_truncate_by_turns_ensures_user_first(self):
        """Test that truncate_by_turns ensures user message comes first."""
        truncator = ContextTruncator()
        # Create scenario where truncation might start with assistant
        messages = self.create_messages(20)
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=3, drop_turns=1
        )

        # First non-system message should be user
        assert result[0].role == "user"

    def test_truncate_by_turns_multiple_drop(self):
        """Test truncate_by_turns with multiple turns dropped at once."""
        truncator = ContextTruncator()
        messages = self.create_messages(20)
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=5, drop_turns=3
        )

        # Should drop 3 turns when limit exceeded
        assert len(result) < len(messages)

    # ==================== truncate_by_dropping_oldest_turns Tests ====================

    def test_truncate_by_dropping_oldest_turns_zero(self):
        """Test truncate_by_dropping_oldest_turns with drop_turns=0."""
        truncator = ContextTruncator()
        messages = self.create_messages(10)
        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=0)
        assert result == messages

    def test_truncate_by_dropping_oldest_turns_negative(self):
        """Test truncate_by_dropping_oldest_turns with negative drop_turns."""
        truncator = ContextTruncator()
        messages = self.create_messages(10)
        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=-1)
        assert result == messages

    def test_truncate_by_dropping_oldest_turns_basic(self):
        """Test basic truncate_by_dropping_oldest_turns functionality."""
        truncator = ContextTruncator()
        # Create 10 messages = 5 turns
        messages = self.create_messages(10)
        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=2)

        # Should drop 2 oldest turns (4 messages)
        assert len(result) == 6
        # Should start with user message
        assert result[0].role == "user"

    def test_truncate_by_dropping_oldest_turns_with_system(self):
        """Test truncate_by_dropping_oldest_turns preserves system messages."""
        truncator = ContextTruncator()
        messages = self.create_messages(10, include_system=True)
        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=2)

        # System message should be preserved
        assert result[0].role == "system"
        assert result[0].content == "System prompt"

    def test_truncate_by_dropping_oldest_turns_drop_all(self):
        """Test truncate_by_dropping_oldest_turns dropping all turns."""
        truncator = ContextTruncator()
        # Create 4 messages = 2 turns
        messages = self.create_messages(4)
        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=2)

        # 即使 drop 掉所有 turn，也会把 user 消息补回来 (#6196)
        assert len(result) >= 1
        assert result[0].role == "user"

    def test_truncate_by_dropping_oldest_turns_drop_more_than_available(self):
        """Test truncate_by_dropping_oldest_turns with drop_turns > available turns."""
        truncator = ContextTruncator()
        # Create 4 messages = 2 turns
        messages = self.create_messages(4)
        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=5)

        # 同理，user 消息会被保留 (#6196)
        assert len(result) >= 1
        assert result[0].role == "user"

    def test_truncate_by_dropping_oldest_turns_ensures_user_first(self):
        """Test that result starts with user message after dropping."""
        truncator = ContextTruncator()
        messages = self.create_messages(20)
        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=3)

        # First message should be user
        if len(result) > 0:
            assert result[0].role == "user"

    # ==================== truncate_by_token_budget Tests ====================

    def test_truncate_by_token_budget_keeps_recent_rounds(self):
        """Basic: drops oldest rounds until remaining tokens fit the budget."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        # 10 messages = 5 turns. Each "Message i" estimates to 2 tokens,
        # so a round (user+assistant) is about 4 tokens.
        messages = self.create_messages(10)
        result = truncator.truncate_by_token_budget(
            messages, budget_tokens=8, token_counter=counter
        )
        # Keep 2 recent rounds (4 messages), drop 3 oldest turns.
        assert len(result) == 4
        assert result[0].role == "user"
        assert result[-1].content == "Message 9"

    def test_truncate_by_token_budget_preserves_system(self):
        """System messages are always preserved."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        messages = self.create_messages(10, include_system=True)
        result = truncator.truncate_by_token_budget(
            messages, budget_tokens=4, token_counter=counter
        )
        assert result[0].role == "system"
        assert result[0].content == "System prompt"
        assert result[1].role == "user"

    def test_truncate_by_token_budget_keeps_latest_round(self):
        """Even a tiny budget keeps the latest round."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        messages = self.create_messages(10)
        result = truncator.truncate_by_token_budget(
            messages, budget_tokens=1, token_counter=counter
        )
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[1].role == "assistant"

    def test_truncate_by_token_budget_large_budget_no_drop(self):
        """A budget covering everything keeps the context unchanged."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        messages = self.create_messages(10)
        result = truncator.truncate_by_token_budget(
            messages, budget_tokens=10000, token_counter=counter
        )
        assert result == messages

    def test_truncate_by_token_budget_min_drop_turns(self):
        """min_drop_turns forces extra drops even when budget is generous."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        messages = self.create_messages(10)
        result = truncator.truncate_by_token_budget(
            messages, budget_tokens=10000, token_counter=counter, min_drop_turns=2
        )
        # Drop 2 oldest turns (4 messages), keep 3 turns.
        assert len(result) == 6
        assert result[0].role == "user"

    def test_truncate_by_token_budget_non_positive_budget(self):
        """Non-positive budget disables truncation."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        messages = self.create_messages(10)
        result = truncator.truncate_by_token_budget(
            messages, budget_tokens=0, token_counter=counter
        )
        assert result == messages

    def test_truncate_by_token_budget_cannot_keep_min(self):
        """min_drop_turns never drops all rounds."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        messages = self.create_messages(4)  # 2 turns
        result = truncator.truncate_by_token_budget(
            messages, budget_tokens=1, token_counter=counter, min_drop_turns=5
        )
        # At least one round (user message) remains.
        assert len(result) >= 1
        assert result[0].role == "user"

    def test_truncate_by_token_budget_tool_chain(self):
        """Tool chains are cleaned up after budget truncation."""
        truncator = ContextTruncator()
        counter = EstimateTokenCounter()
        # 3 rounds, each: user -> assistant -> tool (isolated, no tool_calls).
        msgs = [self.create_message("system", "System prompt")]
        for r in range(3):
            msgs.append(self.create_message("user", f"user {r}"))
            msgs.append(self.create_message("assistant", f"调用工具 {r}"))
            msgs.append(self.create_message("tool", f"工具结果 {r}"))
        result = truncator.truncate_by_token_budget(
            msgs, budget_tokens=8, token_counter=counter
        )
        roles = [m.role for m in result]
        assert "user" in roles
        # tool messages without a pending assistant(tool_calls) are pruned.
        assert "tool" not in roles

    # ==================== truncate_by_halving Tests ====================

    def test_truncate_by_halving_empty(self):
        """Test truncate_by_halving with empty list."""
        truncator = ContextTruncator()
        result = truncator.truncate_by_halving([])
        assert result == []

    def test_truncate_by_halving_single_message(self):
        """Test truncate_by_halving with single message."""
        truncator = ContextTruncator()
        messages = [self.create_message("user", "Hello")]
        result = truncator.truncate_by_halving(messages)
        # Should not truncate if <= 2 messages
        assert result == messages

    def test_truncate_by_halving_two_messages(self):
        """Test truncate_by_halving with two messages."""
        truncator = ContextTruncator()
        messages = self.create_messages(2)
        result = truncator.truncate_by_halving(messages)
        # Should not truncate if <= 2 messages
        assert result == messages

    def test_truncate_by_halving_basic(self):
        """Test basic truncate_by_halving functionality."""
        truncator = ContextTruncator()
        # Create 20 messages
        messages = self.create_messages(20)
        result = truncator.truncate_by_halving(messages)

        # Should delete 50% = 10 messages, keep 10
        assert len(result) == 10
        # First message should be user
        assert result[0].role == "user"

    def test_truncate_by_halving_with_system_message(self):
        """Test truncate_by_halving preserves system messages."""
        truncator = ContextTruncator()
        messages = self.create_messages(20, include_system=True)
        result = truncator.truncate_by_halving(messages)

        # System message should be preserved
        assert result[0].role == "system"
        assert result[0].content == "System prompt"

    def test_truncate_by_halving_odd_count(self):
        """Test truncate_by_halving with odd number of messages."""
        truncator = ContextTruncator()
        messages = self.create_messages(11)
        result = truncator.truncate_by_halving(messages)

        # Should delete floor(11/2) = 5 messages, keep 6
        # But after ensuring user first, may be 5
        assert len(result) >= 5
        assert result[0].role == "user"

    def test_truncate_by_halving_ensures_user_first(self):
        """Test that result starts with user message."""
        truncator = ContextTruncator()
        # Create messages starting with user
        messages = self.create_messages(30)
        result = truncator.truncate_by_halving(messages)

        # First message should be user
        assert result[0].role == "user"

    def test_truncate_by_halving_preserves_recent_messages(self):
        """Test that truncate_by_halving keeps the most recent 50%."""
        truncator = ContextTruncator()
        messages = [
            self.create_message("user", "Message 0"),
            self.create_message("assistant", "Message 1"),
            self.create_message("user", "Message 2"),
            self.create_message("assistant", "Message 3"),
        ]
        result = truncator.truncate_by_halving(messages)

        # Should keep last 2 messages
        assert len(result) == 2
        assert result[0].content == "Message 2"
        assert result[1].content == "Message 3"

    # ==================== Integration Tests ====================

    def test_truncate_with_tool_messages(self):
        """Test truncation with tool messages."""
        truncator = ContextTruncator()
        messages = [
            self.create_message("user", "Run tool"),
            self.create_message("assistant", "Running..."),
            self.create_message("tool", "Tool result"),
            self.create_message("user", "Thanks"),
            self.create_message("assistant", "Welcome"),
        ]

        result = truncator.truncate_by_dropping_oldest_turns(messages, drop_turns=1)

        # First turn (user+assistant+tool) should be dropped
        # Tool message should be cleaned up by fix_messages
        assert len(result) <= 2

    def test_chain_multiple_truncations(self):
        """Test chaining multiple truncation methods."""
        truncator = ContextTruncator()
        messages = self.create_messages(40, include_system=True)

        # First: truncate by turns
        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=10, drop_turns=2
        )
        # Then: halve
        result = truncator.truncate_by_halving(result)

        # Should have system message + truncated content
        assert result[0].role == "system"
        assert len(result) < len(messages)

    def test_empty_after_system_message(self):
        """Test truncation when only system message exists."""
        truncator = ContextTruncator()
        messages = [self.create_message("system", "System prompt")]

        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=5, drop_turns=1
        )

        # Should keep system message
        assert len(result) == 1
        assert result[0].role == "system"

    def test_all_system_messages(self):
        """Test truncation with only system messages."""
        truncator = ContextTruncator()
        messages = [
            self.create_message("system", "System 1"),
            self.create_message("system", "System 2"),
        ]

        result = truncator.truncate_by_turns(
            messages, keep_most_recent_turns=0, drop_turns=1
        )

        # System messages should be preserved, but since there are no non-system
        # messages and keep_most_recent_turns=0, result should be system messages only
        assert len(result) >= 0  # May keep system messages or clear all
        if len(result) > 0:
            assert all(msg.role == "system" for msg in result)

    # ==================== #6196: 长 tool chain 只有一条 user 消息 ====================

    def _build_tool_chain(self, tool_rounds: int = 20) -> list[Message]:
        """构造 system -> user -> (assistant -> tool) * N 的长链，只有一条 user。"""
        msgs = [
            self.create_message("system", "You are a helpful assistant."),
            self.create_message("user", "帮我查一下天气"),
        ]
        for i in range(tool_rounds):
            msgs.append(self.create_message("assistant", f"调用工具 {i}"))
            msgs.append(self.create_message("tool", f"工具结果 {i}"))
        return msgs

    def test_drop_oldest_preserves_sole_user(self):
        """#6196: drop 1 turn 不应丢掉唯一的 user 消息。"""
        truncator = ContextTruncator()
        msgs = self._build_tool_chain(20)  # 1 system + 1 user + 40 asst/tool = 42
        result = truncator.truncate_by_dropping_oldest_turns(msgs, drop_turns=1)
        roles = [m.role for m in result]
        assert "user" in roles, "唯一的 user 消息被丢掉了"
        assert roles[0] == "system"

    def test_halving_preserves_sole_user(self):
        """#6196: 对半砍不应丢掉唯一的 user 消息。"""
        truncator = ContextTruncator()
        msgs = self._build_tool_chain(20)
        result = truncator.truncate_by_halving(msgs)
        roles = [m.role for m in result]
        assert "user" in roles, "唯一的 user 消息被丢掉了"

    def test_truncate_by_turns_preserves_sole_user(self):
        """#6196: keep_most_recent_turns 也不应丢掉唯一的 user 消息。"""
        truncator = ContextTruncator()
        msgs = self._build_tool_chain(20)
        result = truncator.truncate_by_turns(
            msgs, keep_most_recent_turns=3, drop_turns=1
        )
        roles = [m.role for m in result]
        assert "user" in roles, "唯一的 user 消息被丢掉了"

    def test_drop_oldest_heavy_drops_still_has_user(self):
        """#6196: 大量 drop 也不会丢 user。"""
        truncator = ContextTruncator()
        msgs = self._build_tool_chain(30)
        result = truncator.truncate_by_dropping_oldest_turns(msgs, drop_turns=10)
        roles = [m.role for m in result]
        assert "user" in roles

    def test_normal_multi_user_not_affected(self):
        """正常多 user 对话不受影响。"""
        truncator = ContextTruncator()
        msgs = self.create_messages(20, include_system=True)
        result_before = truncator.truncate_by_dropping_oldest_turns(msgs, drop_turns=2)
        # 多 user 场景下截断后仍有 user
        roles = [m.role for m in result_before]
        assert "user" in roles
