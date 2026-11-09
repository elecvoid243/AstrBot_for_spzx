from astrbot.core.agent.context.repetition import (
    RepetitionSanitizeConfig,
    sanitize_history_think_parts,
    sanitize_repetitive_text,
    sanitize_think_part,
)
from astrbot.core.agent.message import ThinkPart

REPEATED = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 20


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


def test_is_idempotent_with_multiple_blocks():
    blocks = [
        "".join(
            f"block {index} line {suffix}\n" for suffix in ("alpha", "beta", "gamma")
        )
        for index in range(5)
    ]
    text = "".join(block * 10 for block in blocks)

    first = sanitize_repetitive_text(text, RepetitionSanitizeConfig())
    second = sanitize_repetitive_text(first.text, RepetitionSanitizeConfig())

    assert first.changed is True
    assert second.changed is False
    assert second.text == first.text


def test_fallback_for_pure_loop_when_scan_is_skipped():
    text = ("alpha alpha alpha\nbeta beta beta\n") * 30
    cfg = RepetitionSanitizeConfig(max_scan_lines=10)
    result = sanitize_repetitive_text(text, cfg)

    original_lines = len([line for line in text.splitlines() if line.strip()])
    assert result.changed is True
    assert "collapsed repetitive reasoning" in result.text
    assert result.collapsed_lines == original_lines - 1


def test_max_scan_lines_uses_fallback_only():
    text = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 40
    cfg = RepetitionSanitizeConfig(max_scan_lines=10)
    result = sanitize_repetitive_text(text, cfg)

    assert result.changed is True
    assert "collapsed repetitive reasoning" in result.text


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
