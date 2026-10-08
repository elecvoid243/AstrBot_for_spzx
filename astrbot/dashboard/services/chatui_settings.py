"""Shape of the ChatUI's persisted thinking-effort presets.

The ChatUI stores its thinking-effort presets in ``data/cmd_config.json``
under ``chatui.thinking_effort`` instead of browser ``localStorage``, so one
instance shares a single definition with every browser that opens it. This
module owns that shape: it normalizes whatever currently sits in the config
file into something the frontend can render, and it strictly validates the
payload the editor submits before anything is written back.

The default preset list lives in :data:`astrbot.core.config.default.DEFAULT_CONFIG`
(the single source of truth); this module only recalls it as the fallback for
unreadable input.

Author: elecvoid243, 2026-10-08
"""

from astrbot.core.config.default import DEFAULT_CONFIG

# Bounds are deliberately generous for hand-written presets but small enough
# that a runaway frontend cannot bloat cmd_config.json.
MAX_PRESETS = 32
MAX_LEVELS_PER_PRESET = 32
MAX_SNAPS_PER_SLIDER = 32
MAX_LABEL_LENGTH = 64
MAX_VALUE_LENGTH = 64
MAX_QUICK_MESSAGES = 24
# Long enough for a paragraph or a pasted template; the menu truncates for
# display and the tooltip reveals the rest.
MAX_QUICK_MESSAGE_LENGTH = 2000

MODES = ("levels", "slider")

_DEFAULT_THINKING_EFFORT = DEFAULT_CONFIG["chatui"]["thinking_effort"]
_FALLBACK_SLIDER = {"min": 1, "max": 100, "step": 1, "snaps": []}


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean_text(
    raw: object,
    limit: int,
    *,
    strict: bool,
    field: str,
) -> str | None:
    """Trim a label/value field, or reject it.

    Args:
        raw: Candidate field value.
        limit: Maximum accepted length after trimming.
        strict: Raise instead of returning None when the field is unusable.
        field: Field name used in the error message.

    Returns:
        The trimmed string, or None when the field is unusable and not strict.

    Raises:
        ValueError: If the field is unusable and ``strict`` is True.
    """
    if not isinstance(raw, str):
        if strict:
            raise ValueError(f"{field} must be a string")
        return None
    cleaned = raw.strip()
    if not cleaned or len(cleaned) > limit:
        if strict:
            raise ValueError(f"{field} must be 1-{limit} characters")
        return None
    return cleaned


def _build_levels(raw: object, *, strict: bool) -> list[dict] | None:
    if not isinstance(raw, list):
        if strict:
            raise ValueError("levels must be a list")
        return None
    items = raw
    if len(items) > MAX_LEVELS_PER_PRESET:
        if strict:
            raise ValueError(
                f"levels supports at most {MAX_LEVELS_PER_PRESET} entries",
            )
        items = items[:MAX_LEVELS_PER_PRESET]

    levels: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            if strict:
                raise ValueError("each level must be an object")
            continue
        name = _clean_text(
            item.get("name"),
            MAX_LABEL_LENGTH,
            strict=strict,
            field="level name",
        )
        value = _clean_text(
            item.get("value"),
            MAX_VALUE_LENGTH,
            strict=strict,
            field="level value",
        )
        if name is None or value is None:
            continue
        levels.append({"name": name, "value": value})
    return levels


def _build_slider(raw: object, *, strict: bool) -> dict | None:
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("slider must be an object")
        return None
    minimum = raw.get("min")
    maximum = raw.get("max")
    step = raw.get("step")
    if not (_is_number(minimum) and _is_number(maximum) and _is_number(step)):
        if strict:
            raise ValueError("slider min/max/step must be numbers")
        return None
    if maximum <= minimum:
        if strict:
            raise ValueError("slider max must be greater than min")
        return None
    if step <= 0:
        if strict:
            raise ValueError("slider step must be positive")
        return None

    snaps_raw = raw.get("snaps", [])
    if not isinstance(snaps_raw, list):
        if strict:
            raise ValueError("slider snaps must be a list")
        snaps_raw = []
    if len(snaps_raw) > MAX_SNAPS_PER_SLIDER:
        if strict:
            raise ValueError(
                f"slider supports at most {MAX_SNAPS_PER_SLIDER} snaps",
            )
        snaps_raw = snaps_raw[:MAX_SNAPS_PER_SLIDER]

    snaps: list[dict] = []
    seen_values: set[float] = set()
    for item in snaps_raw:
        if not isinstance(item, dict):
            if strict:
                raise ValueError("each snap must be an object")
            continue
        name = _clean_text(
            item.get("name"),
            MAX_LABEL_LENGTH,
            strict=strict,
            field="snap name",
        )
        value = item.get("value")
        if not _is_number(value):
            if strict:
                raise ValueError("snap value must be a number")
            continue
        if not minimum <= value <= maximum:
            if strict:
                raise ValueError("snap value must stay inside the slider range")
            continue
        if value in seen_values:
            if strict:
                raise ValueError("duplicate snap value")
            continue
        seen_values.add(value)
        if name is None:
            continue
        snaps.append({"name": name, "value": value})
    return {"min": minimum, "max": maximum, "step": step, "snaps": snaps}


def _build_preset(raw: object, *, strict: bool, index: int) -> dict | None:
    if not isinstance(raw, dict):
        if strict:
            raise ValueError(f"preset #{index + 1} must be an object")
        return None
    preset_id = _clean_text(
        raw.get("id"),
        MAX_LABEL_LENGTH,
        strict=strict,
        field="preset id",
    )
    name = _clean_text(
        raw.get("name"),
        MAX_LABEL_LENGTH,
        strict=strict,
        field="preset name",
    )
    if preset_id is None or name is None:
        return None

    mode = raw.get("mode")
    if mode not in MODES:
        if strict:
            raise ValueError("preset mode must be 'levels' or 'slider'")
        mode = "levels"

    levels = _build_levels(raw.get("levels", []), strict=strict)
    if levels is None:
        return None
    # Levels-mode presets may omit the track entirely; an omitted track gets
    # the plain grid, while an explicitly broken one is rejected in strict
    # mode and falls back field-by-field otherwise.
    slider_raw = raw.get("slider")
    if slider_raw is None:
        slider = dict(_FALLBACK_SLIDER)
    else:
        slider = _build_slider(slider_raw, strict=strict)
        if slider is None:
            slider = dict(_FALLBACK_SLIDER)
    return {
        "id": preset_id,
        "name": name,
        "mode": mode,
        "levels": levels,
        "slider": slider,
    }


def normalize_thinking_effort_settings(raw: object) -> dict:
    """Coerce stored ``chatui.thinking_effort`` into a renderable structure.

    Corrupt pieces are dropped one by one (an unusable preset disappears, an
    unusable track falls back to the default grid) so a hand-edited config
    still yields a usable UI instead of an error page.

    Args:
        raw: Value currently stored under ``chatui.thinking_effort``.

    Returns:
        ``{"active_preset": str, "value": str, "presets": list[dict]}`` with
        ``active_preset`` always naming an existing preset (empty when the
        preset list is empty, which the frontend renders as built-in levels).
    """
    data = raw if isinstance(raw, dict) else {}

    presets: list[dict] = []
    presets_raw = data.get("presets")
    if isinstance(presets_raw, list):
        seen_ids: set[str] = set()
        for index, item in enumerate(presets_raw[:MAX_PRESETS]):
            preset = _build_preset(item, strict=False, index=index)
            if preset is None or preset["id"] in seen_ids:
                continue
            seen_ids.add(preset["id"])
            presets.append(preset)

    active = data.get("active_preset")
    active = active.strip() if isinstance(active, str) else ""
    if not any(preset["id"] == active for preset in presets):
        active = presets[0]["id"] if presets else ""

    value = data.get("value")
    value = value.strip() if isinstance(value, str) else ""
    if not value or len(value) > MAX_VALUE_LENGTH:
        value = _DEFAULT_THINKING_EFFORT["value"]

    return {"active_preset": active, "value": value, "presets": presets}


def validate_thinking_effort_presets(raw: object) -> list[dict]:
    """Strictly validate a preset list submitted by the editor.

    Args:
        raw: Candidate preset list from the request body.

    Returns:
        The normalized preset list, ready to persist.

    Raises:
        ValueError: If any preset is unusable or ids are duplicated.
    """
    if not isinstance(raw, list):
        raise ValueError("presets must be a list")
    if len(raw) > MAX_PRESETS:
        raise ValueError(f"presets supports at most {MAX_PRESETS} entries")

    presets: list[dict] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        preset = _build_preset(item, strict=True, index=index)
        if preset is None:
            raise ValueError(f"preset #{index + 1} is invalid")
        if preset["id"] in seen_ids:
            raise ValueError(f"duplicate preset id: {preset['id']}")
        seen_ids.add(preset["id"])
        presets.append(preset)
    return presets


def normalize_thinking_effort_value(raw: object) -> str:
    """Validate a single selected effort value.

    Values stay free-form strings: the provider layer maps them onto whatever
    ``reasoning_effort`` accepts (``max`` / ``xhigh`` / a number as text).

    Args:
        raw: Candidate value from the request body.

    Returns:
        The trimmed value.

    Raises:
        ValueError: If the value is missing, oversized, or not a string.
    """
    value = _clean_text(raw, MAX_VALUE_LENGTH, strict=True, field="value")
    assert value is not None  # strict=True never returns None
    return value


def _build_quick_message(raw: object, *, strict: bool, index: int) -> dict | None:
    if not isinstance(raw, dict):
        if strict:
            raise ValueError(f"quick message #{index + 1} must be an object")
        return None
    message_id = _clean_text(
        raw.get("id"),
        MAX_LABEL_LENGTH,
        strict=strict,
        field="quick message id",
    )
    content = _clean_text(
        raw.get("content"),
        MAX_QUICK_MESSAGE_LENGTH,
        strict=strict,
        field="quick message content",
    )
    if message_id is None or content is None:
        return None
    return {"id": message_id, "content": content}


def normalize_quick_messages(raw: object) -> dict:
    """Coerce stored ``chatui.quick_messages`` into a renderable structure.

    Args:
        raw: Value currently stored under ``chatui.quick_messages``.

    Returns:
        ``{"items": [{"id": str, "content": str}]}`` with unusable entries
        dropped; an empty list is a legitimate state.
    """
    data = raw if isinstance(raw, dict) else {}
    items: list[dict] = []
    items_raw = data.get("items")
    if isinstance(items_raw, list):
        seen_ids: set[str] = set()
        for index, item in enumerate(items_raw[:MAX_QUICK_MESSAGES]):
            message = _build_quick_message(item, strict=False, index=index)
            if message is None or message["id"] in seen_ids:
                continue
            seen_ids.add(message["id"])
            items.append(message)
    return {"items": items}


def validate_quick_messages(raw: object) -> list[dict]:
    """Strictly validate the quick-message list submitted by the editor.

    Args:
        raw: Candidate item list from the request body.

    Returns:
        The normalized item list, ready to persist.

    Raises:
        ValueError: If any entry is unusable or ids are duplicated.
    """
    if not isinstance(raw, list):
        raise ValueError("quick messages must be a list")
    if len(raw) > MAX_QUICK_MESSAGES:
        raise ValueError(
            f"quick messages supports at most {MAX_QUICK_MESSAGES} entries",
        )

    items: list[dict] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        message = _build_quick_message(item, strict=True, index=index)
        if message is None:
            raise ValueError(f"quick message #{index + 1} is invalid")
        if message["id"] in seen_ids:
            raise ValueError(f"duplicate quick message id: {message['id']}")
        seen_ids.add(message["id"])
        items.append(message)
    return items
