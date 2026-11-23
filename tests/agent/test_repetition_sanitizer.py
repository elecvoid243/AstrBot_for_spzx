import json
from pathlib import Path

from astrbot.core.agent.context.reasoning_ngram import (
    WINDOW_CHARS,
    WINDOW_COUNT,
    observe_loop,
)
from astrbot.core.agent.context.repetition import (
    RepetitionSanitizeConfig,
    sanitize_history_think_parts,
    sanitize_repetitive_text,
    sanitize_think_part,
)
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.message import Message, ThinkPart

REPEATED = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 20


def test_ngram_observes_alternating_reworded_steps():
    text = (
        "Check the permissions on the configuration file before proceeding. "
        "Compare the deployment settings against the expected environment. "
        "Recheck the permissions on the configuration file before proceeding. "
        "Compare the deployment settings against the expected environment again. "
        "Please check the permissions on the configuration file before proceeding. "
        "Now compare the deployment settings against the expected environment."
    )

    result = observe_loop(text)

    assert result.start == 0
    assert result.period == 2


def test_ngram_sampling_limits_work_and_prefers_the_end():
    prefix = "".join(
        f"Inspect the active profile revision {index:07d} before proceeding.\n"
        for index in range(18_000)
    )
    loop = (
        "先核对配置文件中的访问权限和部署相关设置。"
        "再核对配置文件中的访问权限和部署相关设置。"
        "仍需核对配置文件中的访问权限和部署相关设置。"
    )

    no_match = observe_loop(prefix)
    match = observe_loop(prefix + loop)

    assert no_match.start is None
    assert no_match.window_chars == WINDOW_COUNT * WINDOW_CHARS
    assert match.window_chars == WINDOW_CHARS
    assert match.start >= len(prefix)
    assert match.period == 1


def test_collapses_periodic_block():
    result = sanitize_repetitive_text(REPEATED, RepetitionSanitizeConfig())

    assert result.changed is True
    assert result.text.count("Let me go.") == 2
    assert "[context sanitizer: collapsed 19 x 4-line repeated block]" in result.text


def test_is_idempotent():
    first = sanitize_repetitive_text(REPEATED, RepetitionSanitizeConfig())
    second = sanitize_repetitive_text(first.text, RepetitionSanitizeConfig())

    assert second.changed is False
    assert second.text == first.text


def test_disabled_is_noop():
    result = sanitize_repetitive_text(REPEATED, RepetitionSanitizeConfig(enabled=False))

    assert result.changed is False
    assert result.text == REPEATED


def test_short_text_is_noop():
    text = "Let me go.\n" * 5
    result = sanitize_repetitive_text(text, RepetitionSanitizeConfig())

    assert result.changed is False
    assert result.text == text


def test_period_two_is_not_collapsed():
    # Distinct lines make the true period 2, not 1.
    text = ("alpha alpha\nbeta beta\n") * 30
    # Keep the fallback out of the way so this test isolates the block scanner.
    cfg = RepetitionSanitizeConfig(fallback_dup_ratio=1.0)
    result = sanitize_repetitive_text(text, cfg)

    assert result.changed is False
    assert result.text == text


def test_period_nine_is_not_collapsed():
    block = "".join(f"unique long line {index}\n" for index in range(9))
    text = block * 10
    cfg = RepetitionSanitizeConfig(fallback_dup_ratio=1.0)
    result = sanitize_repetitive_text(text, cfg)

    assert result.changed is False
    assert result.text == text


def test_three_line_loop_is_collapsed():
    text = ("alpha alpha alpha\nbeta beta beta\ngamma gamma gamma\n") * 20
    result = sanitize_repetitive_text(text, RepetitionSanitizeConfig())

    assert result.changed is True
    assert result.text.count("alpha alpha alpha") == 1
    assert "19 x 3-line repeated block" in result.text


def test_preserves_original_identical_runs():
    text = ("unique long filler line\n") * 50
    cfg = RepetitionSanitizeConfig(fallback_dup_ratio=1.0)
    result = sanitize_repetitive_text(text, cfg)

    assert result.changed is False
    assert result.text == text


def test_preserves_original_code_and_table_runs():
    code = "".join(f"int value_{index} = {index};\n" for index in range(20))
    code += "}\n" * 8
    table = "".join(f"| row {index} | value {index} |\n" for index in range(10))
    table += "|---|---|\n" * 8
    text = code + table
    cfg = RepetitionSanitizeConfig(fallback_dup_ratio=1.0)
    result = sanitize_repetitive_text(text, cfg)

    assert result.changed is False
    assert result.text == text


def test_is_idempotent_with_multiple_blocks():
    blocks = [
        "".join(
            f"block {index} line {suffix}\n" for suffix in ("alpha", "beta", "gamma")
        )
        for index in range(17)
    ]
    text = "".join(block * 10 for block in blocks)

    first = sanitize_repetitive_text(text, RepetitionSanitizeConfig())
    second = sanitize_repetitive_text(first.text, RepetitionSanitizeConfig())

    assert first.changed is True
    assert first.text.count("[context sanitizer:") == len(blocks)
    assert second.changed is False
    assert second.text == first.text


def test_fallback_folds_only_contiguous_runs_and_keeps_conclusion():
    repeated_line = "Checksum validation completed.\n"
    conclusion = "Use the verified artifact from build 19."
    text = repeated_line * 39 + conclusion
    cfg = RepetitionSanitizeConfig(max_scan_lines=10)
    result = sanitize_repetitive_text(text, cfg)

    assert result.changed is True
    assert result.text == (
        repeated_line
        + "[context sanitizer: collapsed 38 x repeated line]\n"
        + conclusion
    )


def test_max_scan_lines_leaves_periodic_blocks_unchanged():
    text = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 40
    cfg = RepetitionSanitizeConfig(max_scan_lines=10)
    result = sanitize_repetitive_text(text, cfg)

    assert result.changed is False
    assert result.text == text


def test_preserves_matching_blocks_in_distant_sections():
    block = "if (ready) {\n    commit_batch();\n}\n"
    text = "".join(
        block
        + "".join(
            f"Independent operation {section} row {row}.\n"
            for row in range(40 + section)
        )
        for section in range(8)
    )

    assert sanitize_repetitive_text(text, RepetitionSanitizeConfig()).text == text


def test_control_corpus_is_byte_identical():
    text = """```c
for (int i = 0; i < n; i++) {
    if (pulse[i].valid) {
        count++;
    }
}
```

| field | value |
|---|---|
| pri | 1000 |
| pw | 12 |

The derivation repeats x, y and z but never in the same
three-line block eight times. This paragraph exists to keep
the line count above the guard while staying non-repetitive.
unique line 01
unique line 02
unique line 03
unique line 04
unique line 05
unique line 06
unique line 07
unique line 08
unique line 09
unique line 10
unique line 11
unique line 12
unique line 13
unique line 14
unique line 15
unique line 16
unique line 17
unique line 18
unique line 19
unique line 20
unique line 21
unique line 22
unique line 23
unique line 24
unique line 25
unique line 26
unique line 27
unique line 28
unique line 29
unique line 30
"""
    result = sanitize_repetitive_text(text, RepetitionSanitizeConfig())

    assert result.changed is False
    assert result.text == text


def test_provider_config_clamps_values():
    cfg = RepetitionSanitizeConfig.from_provider_config(
        {
            "min_period": 9,
            "max_period": 2,
            "min_repeats": 0,
            "fallback_dup_ratio": 2,
        }
    )

    assert cfg.min_period == 9
    assert cfg.max_period == 9
    assert cfg.min_repeats == 2
    assert cfg.fallback_dup_ratio == 1.0


def test_provider_config_parses_string_booleans():
    assert (
        RepetitionSanitizeConfig.from_provider_config({"enabled": "false"}).enabled
        is False
    )
    assert (
        RepetitionSanitizeConfig.from_provider_config({"enabled": "true"}).enabled
        is True
    )


def test_sanitize_think_part_skips_encrypted():
    part = ThinkPart(think=REPEATED, encrypted="signed-state")

    assert sanitize_think_part(part, RepetitionSanitizeConfig()) is part


def test_sanitize_think_part_preserves_private_marker():
    part = ThinkPart(think=REPEATED)
    part._no_save = True

    sanitized = sanitize_think_part(part, RepetitionSanitizeConfig())

    assert sanitized.think.count("Let me go.") == 2
    assert sanitized._no_save is True


def test_history_skips_encrypted_think():
    history = [
        {
            "content": {
                "type": "bot",
                "message": [{"type": "think", "think": REPEATED, "encrypted": "sig"}],
            }
        }
    ]

    sanitize_history_think_parts(history, RepetitionSanitizeConfig())

    part = history[0]["content"]["message"][0]
    assert part["think"] == REPEATED
    assert "think_original" not in part
    assert "think_sanitized" not in part


FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "context_sanitizer" / "d40_think.json"
)
FILLER = {
    "OK.",
    "Let me write.",
    "Let me go.",
    "OK — writing.",
    "OK — writing now.",
    "Let me write it.",
    "Hmm — writing now.",
    "Let me be efficient ✓.",
    "Let me be quick.",
    "Let me do it.",
    "Let me do it ✓.",
    "```",
}


def _d40_think() -> dict[int, str]:
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {item["id"]: item["think"] for item in items}


def _meaningful_lines(text: str) -> list[str]:
    return sorted(
        {
            line.strip()
            for line in text.splitlines()
            if line.strip() and line.strip() not in FILLER
        }
    )


def test_d40_2503_collapses_loop():
    original = _d40_think()[2503]
    result = sanitize_repetitive_text(original, RepetitionSanitizeConfig())

    assert result.changed is True
    assert 0 < result.text.count("Let me go.") < original.count("Let me go.")
    for line in _meaningful_lines(original):
        assert line in result.text


def test_d40_2314_collapses_loop():
    original = _d40_think()[2314]
    result = sanitize_repetitive_text(original, RepetitionSanitizeConfig())

    assert result.changed is True
    assert 0 < result.text.count("Let me go.") < original.count("Let me go.")
    for line in _meaningful_lines(original):
        assert line in result.text


def test_d40_2503_result_passes_truncator_fix_messages():
    original = _d40_think()[2503]
    result = sanitize_repetitive_text(original, RepetitionSanitizeConfig())
    messages = [Message(role="assistant", content=[ThinkPart(think=result.text)])]

    fixed = ContextTruncator().fix_messages(messages)

    assert len(fixed) == 1
    assert fixed[0].role == "assistant"
    assert fixed[0].content[0].think == result.text
