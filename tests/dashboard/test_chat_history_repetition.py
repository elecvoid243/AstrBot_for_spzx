from astrbot.dashboard.services.chat_service import (
    _sanitize_history_bot_records,
)

REPEATED = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 20


def test_folds_bot_think_and_keeps_original():
    history = [
        {
            "content": {
                "type": "bot",
                "message": [
                    {"type": "think", "think": REPEATED},
                    {"type": "plain", "text": "answer"},
                ],
            }
        }
    ]

    _sanitize_history_bot_records(history)

    part = history[0]["content"]["message"][0]
    assert part["think_sanitized"] is True
    assert part["think_original"] == REPEATED
    assert part["think"].count("Let me go.") == 2
    assert part["think_collapsed_lines"] > 0


def test_leaves_unchanged_think_untouched():
    history = [
        {
            "content": {
                "type": "bot",
                "message": [{"type": "think", "think": "short thought"}],
            }
        }
    ]

    _sanitize_history_bot_records(history)

    part = history[0]["content"]["message"][0]
    assert part["think"] == "short thought"
    assert "think_original" not in part
    assert "think_sanitized" not in part
