"""Tests for ChatUI thinking-effort preset normalization and validation.

Author: elecvoid243, 2026-10-08
"""

import pytest

from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.dashboard.services.chatui_settings import (
    MAX_PRESETS,
    MAX_QUICK_MESSAGES,
    normalize_quick_messages,
    normalize_thinking_effort_settings,
    normalize_thinking_effort_value,
    validate_quick_messages,
    validate_thinking_effort_presets,
)

BUILTIN = DEFAULT_CONFIG["chatui"]["thinking_effort"]


def test_missing_section_yields_empty_presets_and_default_value():
    settings = normalize_thinking_effort_settings(None)

    assert settings == {
        "active_preset": "",
        "value": BUILTIN["value"],
        "presets": [],
    }


def test_builtin_presets_round_trip():
    settings = normalize_thinking_effort_settings(BUILTIN)

    assert [preset["id"] for preset in settings["presets"]] == [
        "deepseek-v4",
        "deepseek-v4.1",
        "qwen-3.8",
    ]
    assert settings["active_preset"] == "deepseek-v4"
    assert settings["value"] == BUILTIN["value"]

    v41 = settings["presets"][1]
    assert v41["mode"] == "slider"
    assert [snap["value"] for snap in v41["slider"]["snaps"]] == [20, 50, 75, 100]


def test_corrupt_entries_are_dropped_one_by_one():
    raw = {
        "active_preset": "ghost",
        "value": "high",
        "presets": [
            {
                "id": "ok",
                "name": "OK",
                "mode": "levels",
                "levels": [{"name": "低", "value": "low"}],
                "slider": {"min": 1, "max": 100, "step": 1, "snaps": []},
            },
            "not-a-dict",
            {"id": "", "name": "blank id"},
            {"id": "ok", "name": "duplicate id"},
        ],
    }

    settings = normalize_thinking_effort_settings(raw)

    assert [preset["id"] for preset in settings["presets"]] == ["ok"]
    # An active id that names nothing falls back to the first surviving preset.
    assert settings["active_preset"] == "ok"
    assert settings["value"] == "high"


def test_broken_slider_falls_back_to_default_grid():
    raw = {
        "presets": [
            {
                "id": "p",
                "name": "P",
                "mode": "slider",
                "levels": [],
                "slider": {"min": 100, "max": 1, "step": 0, "snaps": "nope"},
            },
        ],
    }

    settings = normalize_thinking_effort_settings(raw)

    assert settings["presets"][0]["slider"] == {
        "min": 1,
        "max": 100,
        "step": 1,
        "snaps": [],
    }


def test_empty_preset_list_is_preserved():
    settings = normalize_thinking_effort_settings(
        {"presets": [], "active_preset": "x", "value": "max"},
    )

    assert settings == {"active_preset": "", "value": "max", "presets": []}


def test_invalid_mode_falls_back_to_levels_and_keeps_levels():
    raw = {
        "presets": [
            {
                "id": "p",
                "name": "P",
                "mode": "trippy",
                "levels": [{"name": "低", "value": "low"}],
            },
        ],
    }

    settings = normalize_thinking_effort_settings(raw)

    assert settings["presets"][0]["mode"] == "levels"
    assert settings["presets"][0]["levels"] == [{"name": "低", "value": "low"}]


def test_validate_accepts_builtin_presets():
    presets = validate_thinking_effort_presets(BUILTIN["presets"])

    assert len(presets) == len(BUILTIN["presets"])


@pytest.mark.parametrize(
    ("preset", "message"),
    [
        (
            {
                "id": "p",
                "name": "P",
                "mode": "slider",
                "levels": [],
                "slider": {
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "snaps": [{"name": "x", "value": 200}],
                },
            },
            "inside the slider range",
        ),
        (
            {
                "id": "p",
                "name": "P",
                "mode": "slider",
                "levels": [],
                "slider": {"min": 10, "max": 5, "step": 1, "snaps": []},
            },
            "greater than min",
        ),
        (
            {
                "id": "p",
                "name": "P",
                "mode": "slider",
                "levels": [],
                "slider": {"min": 1, "max": 10, "step": 0, "snaps": []},
            },
            "step must be positive",
        ),
        (
            {
                "id": "p",
                "name": "P",
                "mode": "levels",
                "levels": [{"name": " ", "value": "x"}],
            },
            "level name",
        ),
        (
            {"id": "p", "name": "", "mode": "levels", "levels": []},
            "preset name",
        ),
        (
            {"id": "p", "name": "P", "mode": "trippy", "levels": []},
            "mode must be",
        ),
    ],
)
def test_validate_rejects_malformed_presets(preset, message):
    with pytest.raises(ValueError, match=message):
        validate_thinking_effort_presets([preset])


def test_validate_rejects_duplicate_ids():
    preset = {"id": "p", "name": "P", "mode": "levels", "levels": []}

    with pytest.raises(ValueError, match="duplicate preset id"):
        validate_thinking_effort_presets([preset, dict(preset)])


def test_validate_rejects_too_many_presets():
    preset = {"id": "p", "name": "P", "mode": "levels", "levels": []}

    with pytest.raises(ValueError, match="at most"):
        validate_thinking_effort_presets(
            [dict(preset, id=f"p{index}") for index in range(MAX_PRESETS + 1)],
        )


def test_normalize_value_rejects_blank_and_non_string():
    with pytest.raises(ValueError, match="value must be"):
        normalize_thinking_effort_value("   ")
    with pytest.raises(ValueError, match="value must be a string"):
        normalize_thinking_effort_value(72)


def test_normalize_value_trims_the_string_channel():
    assert normalize_thinking_effort_value("  raw-value  ") == "raw-value"


def test_quick_messages_missing_section_yields_empty_items():
    assert normalize_quick_messages(None) == {"items": []}


def test_quick_messages_drop_corrupt_entries_one_by_one():
    raw = {
        "items": [
            {"id": "qm-1", "content": "继续"},
            "not-a-dict",
            {"id": "", "content": "blank id"},
            {"id": "qm-2", "content": "   "},
            {"id": "qm-1", "content": "duplicate id"},
        ],
    }

    assert normalize_quick_messages(raw) == {
        "items": [{"id": "qm-1", "content": "继续"}],
    }


def test_quick_messages_trim_edges_but_keep_inner_newlines():
    raw = {"items": [{"id": "qm-1", "content": "  第一行\n第二行  "}]}

    assert normalize_quick_messages(raw)["items"][0]["content"] == "第一行\n第二行"


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"id": "qm-1", "content": "   "}, "quick message content"),
        ({"id": "  ", "content": "x"}, "quick message id"),
        ("not-a-dict", "must be an object"),
    ],
)
def test_validate_quick_messages_rejects_malformed_entries(item, message):
    with pytest.raises(ValueError, match=message):
        validate_quick_messages([item])


def test_validate_quick_messages_rejects_duplicate_ids():
    item = {"id": "qm-1", "content": "继续"}

    with pytest.raises(ValueError, match="duplicate quick message id"):
        validate_quick_messages([item, dict(item)])


def test_validate_quick_messages_rejects_too_many():
    with pytest.raises(ValueError, match="at most"):
        validate_quick_messages(
            [
                {"id": f"qm-{index}", "content": "x"}
                for index in range(MAX_QUICK_MESSAGES + 1)
            ],
        )


def test_validate_quick_messages_accepts_an_empty_list():
    assert validate_quick_messages([]) == []
