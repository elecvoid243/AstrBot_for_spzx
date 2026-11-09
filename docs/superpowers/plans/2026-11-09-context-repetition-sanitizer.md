# 上下文重复推理消毒 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 折叠历史中的重复思维链，避免退化推理再次进入模型上下文，并让 ChatUI 刷新后默认显示消毒版、可展开原文。

**Architecture:** 新增一个无 I/O 的纯函数消毒器；在 assistant `ThinkPart` 写入点（A）、conversation 落库点（C）、ChatUI 历史读取点（D）三处调用。模型上下文永久消毒；`platform_message_history` 落库保持原始，读取时折叠并附带 `think_original`。

**Tech Stack:** Python 3.10+、pytest、pytest-asyncio、Vue 3、TypeScript、Vitest、Vue Test Utils。

## Global Constraints

- Python 3.10+；不得引入新依赖。
- 所有新增 Python docstring 使用 Google 格式（`Args:` / `Returns:` / `Raises:`）。
- 注释与日志使用英文；提交信息使用 conventional commit，type/scope 英文、描述中文。
- 路径处理使用 `pathlib.Path`；不新增数据库字段、不写迁移脚本、不改 OpenAPI 类型定义。
- 只处理 assistant 消息中的 `ThinkPart`；不碰 `TextPart`、tool 结果、`result_chain`。
- `ThinkPart.encrypted` 非空时完全跳过消毒。
- 不触碰工作区中与本计划无关的未提交改动。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `astrbot/core/agent/context/repetition.py` | 纯函数消毒器、配置、ThinkPart/Message/历史记录包装 |
| `tests/agent/test_repetition_sanitizer.py` | 纯函数单测、对照语料 |
| `tests/fixtures/context_sanitizer/d40_think.json` | D40 真实思维链基线 |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | A1/A2：构造 `ThinkPart` 前消毒 |
| `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py` | C：conversation 落库前消毒 |
| `astrbot/dashboard/services/chat_service.py` | D：ChatUI 历史读取时折叠 |
| `dashboard/src/components/chat/message_list_comps/ReasoningTimeline.vue` | 默认渲染消毒版 + 单条展开原文 |
| `dashboard/src/i18n/locales/*/features/chat.json` | 新增 `showOriginal` / `showSanitized` 文案 |
| `dashboard/src/components/chat/message_list_comps/ReasoningTimeline.spec.ts` | 前端展开/收起测试 |

---

### Task 1: 纯函数消毒器与单元测试

**Files:**
- Create: `astrbot/core/agent/context/repetition.py`
- Test: `tests/agent/test_repetition_sanitizer.py`

**Interfaces:**
- Consumes: `astrbot.core.agent.message.ThinkPart`、`Message`。
- Produces:
  - `RepetitionSanitizeConfig`
  - `SanitizeResult`
  - `sanitize_repetitive_text(text: str, cfg: RepetitionSanitizeConfig) -> SanitizeResult`
  - `sanitize_think_part(part: ThinkPart, cfg: RepetitionSanitizeConfig) -> ThinkPart`
  - `sanitize_message_think_parts(messages: list[Message], cfg: RepetitionSanitizeConfig) -> list[Message]`
  - `sanitize_history_think_parts(history_data: list[dict], cfg: RepetitionSanitizeConfig) -> None`

- [ ] **Step 1: 写失败测试**

创建 `tests/agent/test_repetition_sanitizer.py`：

```python
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
    result = sanitize_repetitive_text(
        REPEATED, RepetitionSanitizeConfig(enabled=False)
    )

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
            f"block {index} line {suffix}\n"
            for suffix in ("alpha", "beta", "gamma")
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
                "message": [
                    {"type": "think", "think": REPEATED, "encrypted": "sig"}
                ],
            }
        }
    ]

    sanitize_history_think_parts(history, RepetitionSanitizeConfig())

    part = history[0]["content"]["message"][0]
    assert part["think"] == REPEATED
    assert "think_original" not in part
    assert "think_sanitized" not in part
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
uv run pytest tests/agent/test_repetition_sanitizer.py -v
```

Expected: FAIL，`ModuleNotFoundError: astrbot.core.agent.context.repetition`。

- [ ] **Step 3: 实现核心模块**

创建 `astrbot/core/agent/context/repetition.py`：

```python
"""Collapse repetitive reasoning before it re-enters model context or ChatUI."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from math import gcd
from typing import Any

from astrbot.core.agent.message import Message, ThinkPart

logger = logging.getLogger("astrbot")

DEFAULT_MIN_PERIOD = 3
DEFAULT_MAX_PERIOD = 8
DEFAULT_MIN_REPEATS = 8
DEFAULT_MIN_LINES = 40
DEFAULT_MIN_CHARS = 512
DEFAULT_MAX_SCAN_LINES = 20_000
DEFAULT_FALLBACK_DUP_RATIO = 0.8
DEFAULT_FALLBACK_MAX_UNIQUE_LINES = 8
MAX_SANITIZE_PASSES = 16


@dataclass(frozen=True, slots=True)
class RepetitionSanitizeConfig:
    """Thresholds for folding repeated reasoning blocks."""

    enabled: bool = True
    min_period: int = DEFAULT_MIN_PERIOD
    max_period: int = DEFAULT_MAX_PERIOD
    min_repeats: int = DEFAULT_MIN_REPEATS
    min_lines: int = DEFAULT_MIN_LINES
    min_chars: int = DEFAULT_MIN_CHARS
    max_scan_lines: int = DEFAULT_MAX_SCAN_LINES
    fallback_dup_ratio: float = DEFAULT_FALLBACK_DUP_RATIO

    @classmethod
    def from_provider_config(cls, raw: Any) -> "RepetitionSanitizeConfig":
        """Build a config from a provider's ``context_sanitizer`` mapping.

        Args:
            raw: Provider configuration value; non-mapping values use defaults.

        Returns:
            A validated, conservative configuration.
        """
        if not isinstance(raw, dict):
            return cls()

        def _int(name: str, default: int, minimum: int) -> int:
            try:
                value = int(raw.get(name, default))
            except (TypeError, ValueError):
                return default
            return max(minimum, value)

        def _ratio(name: str, default: float) -> float:
            try:
                value = float(raw.get(name, default))
            except (TypeError, ValueError):
                return default
            return min(1.0, max(0.0, value))

        def _bool(name: str, default: bool) -> bool:
            value = raw.get(name, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "on"}:
                    return True
                if lowered in {"false", "0", "no", "off"}:
                    return False
            return default

        min_period = _int("min_period", DEFAULT_MIN_PERIOD, 2)
        return cls(
            enabled=_bool("enabled", True),
            min_period=min_period,
            max_period=_int("max_period", DEFAULT_MAX_PERIOD, min_period),
            min_repeats=_int("min_repeats", DEFAULT_MIN_REPEATS, 2),
            min_lines=_int("min_lines", DEFAULT_MIN_LINES, 1),
            min_chars=_int("min_chars", DEFAULT_MIN_CHARS, 0),
            max_scan_lines=_int("max_scan_lines", DEFAULT_MAX_SCAN_LINES, 1),
            fallback_dup_ratio=_ratio(
                "fallback_dup_ratio", DEFAULT_FALLBACK_DUP_RATIO
            ),
        )


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    """Folded text and counters for one reasoning string."""

    text: str
    collapsed_blocks: int = 0
    collapsed_lines: int = 0
    original_chars: int = 0
    changed: bool = False


def _duplicate_ratio(lines: list[str]) -> float:
    meaningful = [line for line in lines if len(line) >= 4]
    if not meaningful:
        return 0.0
    counts = Counter(meaningful)
    duplicated = sum(count for count in counts.values() if count > 1)
    return duplicated / len(meaningful)


def _common_stride(starts: list[int]) -> int:
    """Return the greatest common stride between consecutive occurrences.

    Args:
        starts: Non-overlapping occurrence start indices.

    Returns:
        The common stride in lines; 0 when fewer than two starts exist.
    """
    stride = 0
    for index in range(len(starts) - 1):
        stride = gcd(stride, starts[index + 1] - starts[index])
    return stride


def _find_dominant_block(
    text: str, cfg: RepetitionSanitizeConfig
) -> tuple[int, list[int]] | None:
    """Find the block whose removal deletes the most lines.

    Args:
        text: Current reasoning text.
        cfg: Sanitizer thresholds.

    Returns:
        ``(period, non_overlapping_start_indices)`` or None.
    """
    keys = [line.strip() for line in text.splitlines()]
    best: tuple[int, int, list[int]] | None = None
    for period in range(cfg.min_period, cfg.max_period + 1):
        positions: dict[tuple[str, ...], list[int]] = {}
        for index in range(0, len(keys) - period + 1):
            block = tuple(keys[index : index + period])
            if not any(len(line) >= 8 for line in block):
                continue
            if _minimal_period(block) < cfg.min_period:
                continue
            positions.setdefault(block, []).append(index)
        for starts in positions.values():
            if len(starts) < cfg.min_repeats:
                continue
            selected: list[int] = []
            last = -1
            for start in starts:
                if start < last:
                    continue
                selected.append(start)
                last = start + period
            if len(selected) < cfg.min_repeats:
                continue
            if _common_stride(selected) > cfg.max_period:
                continue
            removed = (len(selected) - 1) * period
            if best is None or removed > best[0]:
                best = (removed, period, selected)
    if best is None:
        return None
    return best[1], best[2]


def _minimal_period(block: tuple[str, ...]) -> int:
    """Return the smallest period that reproduces the block.

    Args:
        block: Normalized line block.

    Returns:
        The minimal period in lines.
    """
    for period in range(1, len(block)):
        if all(
            block[index] == block[index % period]
            for index in range(len(block))
        ):
            return period
    return len(block)


def _remove_block_occurrences(
    text: str, period: int, starts: list[int]
) -> tuple[str, int]:
    """Keep the first occurrence and remove the rest.

    Args:
        text: Current reasoning text.
        period: Block length in lines.
        starts: Non-overlapping occurrence start indices.

    Returns:
        ``(new_text, removed_occurrences)``.
    """
    lines = text.splitlines(keepends=True)
    skip: set[int] = set()
    for start in starts[1:]:
        skip.update(range(start, start + period))
    out: list[str] = []
    index = 0
    marker_done = False
    first_end = starts[0] + period
    while index < len(lines):
        if index in skip:
            index += 1
            continue
        out.append(lines[index])
        index += 1
        if not marker_done and index == first_end:
            out.append(
                f"[context sanitizer: collapsed {len(starts) - 1} x "
                f"{period}-line repeated block]\n"
            )
            marker_done = True
    return "".join(out), len(starts) - 1


def sanitize_repetitive_text(
    text: str, cfg: RepetitionSanitizeConfig
) -> SanitizeResult:
    """Collapse consecutive repeated line blocks in a reasoning string.

    Args:
        text: Original reasoning content.
        cfg: Sanitizer thresholds.

    Returns:
        The folded text and counters; the original text when nothing matched.
    """
    original_chars = len(text)
    if not cfg.enabled or original_chars < cfg.min_chars:
        return SanitizeResult(text=text, original_chars=original_chars)
    try:
        all_lines = text.splitlines()
        non_empty = [line.strip() for line in all_lines if line.strip()]
        if len(non_empty) < cfg.min_lines:
            return SanitizeResult(text=text, original_chars=original_chars)

        collapsed_blocks = 0
        if len(all_lines) <= cfg.max_scan_lines:
            for _ in range(MAX_SANITIZE_PASSES):
                found = _find_dominant_block(text, cfg)
                if found is None:
                    break
                period, starts = found
                text, removed = _remove_block_occurrences(
                    text, period, starts
                )
                collapsed_blocks += removed
                if removed == 0:
                    break

        remaining = [line.strip() for line in text.splitlines() if line.strip()]
        remaining_unique = {line for line in remaining if len(line) >= 4}
        if (
            len(remaining) >= cfg.min_lines
            and len(remaining_unique) <= DEFAULT_FALLBACK_MAX_UNIQUE_LINES
            and _duplicate_ratio(remaining) > cfg.fallback_dup_ratio
        ):
            return SanitizeResult(
                text=(
                    "[context sanitizer: collapsed repetitive reasoning "
                    f"({len(non_empty)} lines, {original_chars} chars)]"
                ),
                collapsed_blocks=max(1, collapsed_blocks),
                collapsed_lines=max(0, len(non_empty) - 1),
                original_chars=original_chars,
                changed=True,
            )
        return SanitizeResult(
            text=text,
            collapsed_blocks=collapsed_blocks,
            collapsed_lines=max(0, len(non_empty) - len(remaining)),
            original_chars=original_chars,
            changed=collapsed_blocks > 0,
        )
    except Exception:
        logger.warning(
            "context sanitizer failed; keeping original text", exc_info=True
        )
        return SanitizeResult(text=text, original_chars=original_chars)


def sanitize_think_part(
    part: ThinkPart, cfg: RepetitionSanitizeConfig
) -> ThinkPart:
    """Fold one ThinkPart unless it carries an encrypted reasoning signature.

    Args:
        part: Assistant reasoning part.
        cfg: Sanitizer thresholds.

    Returns:
        A new ThinkPart when folding changed the text, otherwise the input.
    """
    if part.encrypted:
        return part
    result = sanitize_repetitive_text(part.think, cfg)
    if not result.changed:
        return part
    return part.model_copy(update={"think": result.text})


def sanitize_message_think_parts(
    messages: list[Message], cfg: RepetitionSanitizeConfig
) -> list[Message]:
    """Fold ThinkParts in assistant messages without touching other roles.

    Args:
        messages: Model-visible messages.
        cfg: Sanitizer thresholds.

    Returns:
        A new list when any message changed, otherwise the input list.
    """
    changed = False
    output: list[Message] = []
    for message in messages:
        if message.role != "assistant" or not isinstance(message.content, list):
            output.append(message)
            continue
        parts = []
        message_changed = False
        for part in message.content:
            if isinstance(part, ThinkPart):
                sanitized = sanitize_think_part(part, cfg)
                if sanitized is not part:
                    message_changed = True
                parts.append(sanitized)
            else:
                parts.append(part)
        if message_changed:
            output.append(message.model_copy(update={"content": parts}))
            changed = True
        else:
            output.append(message)
    return output if changed else messages


def sanitize_history_think_parts(
    history_data: list[dict], cfg: RepetitionSanitizeConfig
) -> None:
    """Fold bot think parts in a ChatUI history payload in place.

    Args:
        history_data: Platform-history record dicts.
        cfg: Sanitizer thresholds.
    """
    for record in history_data:
        if not isinstance(record, dict):
            continue
        content = record.get("content")
        if not isinstance(content, dict) or content.get("type") != "bot":
            continue
        message = content.get("message")
        if not isinstance(message, list):
            continue
        for part in message:
            if not isinstance(part, dict) or part.get("type") != "think":
                continue
            if part.get("encrypted"):
                continue
            original = part.get("think")
            if not isinstance(original, str):
                continue
            result = sanitize_repetitive_text(original, cfg)
            if not result.changed:
                continue
            part["think"] = result.text
            part["think_original"] = original
            part["think_sanitized"] = True
            part["think_collapsed_lines"] = result.collapsed_lines
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
uv run pytest tests/agent/test_repetition_sanitizer.py -v
```

Expected: PASS（11 passed）。

- [ ] **Step 5: 格式化并检查**

Run:

```powershell
uv run ruff format astrbot/core/agent/context/repetition.py tests/agent/test_repetition_sanitizer.py
uv run ruff check astrbot/core/agent/context/repetition.py tests/agent/test_repetition_sanitizer.py
```

Expected: `All checks passed!`。

- [ ] **Step 6: 提交**

```powershell
git add astrbot/core/agent/context/repetition.py tests/agent/test_repetition_sanitizer.py
git commit -m "feat(agent): 新增重复推理折叠核心模块"
```

---

### Task 2: D40 真实数据基线与回放测试

**Files:**
- Create: `tests/fixtures/context_sanitizer/d40_think.json`
- Modify: `tests/agent/test_repetition_sanitizer.py`

**Interfaces:**
- Consumes: Task 1 的 `sanitize_repetitive_text`、`RepetitionSanitizeConfig`。
- Produces: 真实数据回归基线，供后续任务复用。

- [ ] **Step 1: 导出 D40 真实思维链**

在仓库根目录运行：

```powershell
@'
import json
import sqlite3
from pathlib import Path

DB = Path("data/data_v4.db")
SESSION = "a49d8c10-0b34-478d-a122-088f007497a8"
IDS = (2314, 2503, 2507, 2509, 2512)

con = sqlite3.connect(DB)
out = []
for mid in IDS:
    raw = con.execute(
        "SELECT content FROM platform_message_history WHERE id=?", (mid,)
    ).fetchone()[0]
    message = json.loads(raw)["message"]
    think = "".join(
        part.get("think", "")
        for part in message
        if part.get("type") == "think"
    )
    out.append({"id": mid, "think": think})

path = Path("tests/fixtures/context_sanitizer/d40_think.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(path, path.stat().st_size)
'@ | uv run python -
```

Expected: 打印 `tests/fixtures/context_sanitizer/d40_think.json` 及文件大小（约 60KB）。

- [ ] **Step 2: 写真实数据回放测试**

追加到 `tests/agent/test_repetition_sanitizer.py`：

```python
import json
from pathlib import Path

FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "context_sanitizer"
    / "d40_think.json"
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
    return sorted({
        line.strip()
        for line in text.splitlines()
        if line.strip() and line.strip() not in FILLER
    })


def test_d40_2503_collapses_loop():
    original = _d40_think()[2503]
    result = sanitize_repetitive_text(original, RepetitionSanitizeConfig())

    assert result.changed is True
    assert result.text.count("Let me go.") <= 50
    assert result.collapsed_lines >= 1300
    for line in _meaningful_lines(original):
        assert line in result.text


def test_d40_2314_collapses_loop():
    original = _d40_think()[2314]
    result = sanitize_repetitive_text(original, RepetitionSanitizeConfig())

    assert result.changed is True
    assert result.text.count("Let me go.") <= 50
    assert result.collapsed_lines >= 6800
    for line in _meaningful_lines(original):
        assert line in result.text
```

- [ ] **Step 3: 运行测试确认通过**

Run:

```powershell
uv run pytest tests/agent/test_repetition_sanitizer.py -v
```

Expected: PASS（13 passed）。如果 2503/2314 的阈值不满足，先打印 `result.collapsed_lines` 与 `result.text[:200]` 调整断言，不要放宽“非填充行必须保留”的检查。

- [ ] **Step 4: 格式化并提交**

```powershell
uv run ruff format tests/agent/test_repetition_sanitizer.py
uv run ruff check tests/agent/test_repetition_sanitizer.py
git add tests/fixtures/context_sanitizer/d40_think.json tests/agent/test_repetition_sanitizer.py
git commit -m "test(agent): 增加 D40 真实思维链折叠基线"
```

---

### Task 3: Runner 写入点集成（A1/A2）

**Files:**
- Modify: `astrbot/core/agent/runners/tool_loop_agent_runner.py`
- Test: `tests/test_tool_loop_agent_runner.py`

**Interfaces:**
- Consumes: Task 1 的 `RepetitionSanitizeConfig`、`sanitize_repetitive_text`。
- Produces:
  - `ToolLoopAgentRunner.get_repetition_sanitize_config() -> RepetitionSanitizeConfig`
  - `ToolLoopAgentRunner._build_think_part(llm_resp: LLMResponse) -> ThinkPart`

- [ ] **Step 1: 写失败测试**

在 `tests/test_tool_loop_agent_runner.py` 的 import 区补：

```python
from astrbot.core.agent.message import ThinkPart
```

在文件末尾追加：

```python
REPEATED_REASONING = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 20


def test_build_think_part_sanitizes_repetition():
    runner = object.__new__(ToolLoopAgentRunner)
    runner.provider = SimpleNamespace(provider_config={})
    response = LLMResponse(
        role="assistant",
        reasoning_content=REPEATED_REASONING,
    )

    part = runner._build_think_part(response)

    assert isinstance(part, ThinkPart)
    assert part.think.count("Let me go.") == 2
    assert "[context sanitizer:" in part.think


def test_build_think_part_keeps_encrypted_reasoning():
    runner = object.__new__(ToolLoopAgentRunner)
    runner.provider = SimpleNamespace(provider_config={})
    response = LLMResponse(
        role="assistant",
        reasoning_content=REPEATED_REASONING,
        reasoning_signature="signed-state",
    )

    part = runner._build_think_part(response)

    assert part.think == REPEATED_REASONING
    assert part.encrypted == "signed-state"


def test_build_think_part_respects_provider_switch():
    runner = object.__new__(ToolLoopAgentRunner)
    runner.provider = SimpleNamespace(
        provider_config={"context_sanitizer": {"enabled": False}}
    )
    response = LLMResponse(
        role="assistant",
        reasoning_content=REPEATED_REASONING,
    )

    part = runner._build_think_part(response)

    assert part.think == REPEATED_REASONING
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
uv run pytest tests/test_tool_loop_agent_runner.py -k "build_think_part" -v
```

Expected: FAIL，`AttributeError: 'ToolLoopAgentRunner' object has no attribute '_build_think_part'`。

- [ ] **Step 3: 实现**

在 `tool_loop_agent_runner.py` 的 import 区补：

```python
from astrbot.core.agent.context.repetition import (
    RepetitionSanitizeConfig,
    sanitize_repetitive_text,
)
```

在 `_complete_with_assistant_response()` 之前新增：

```python
    def get_repetition_sanitize_config(self) -> RepetitionSanitizeConfig:
        """Read the provider-level repetition sanitizer configuration.

        Returns:
            The configured thresholds, or conservative defaults when absent.
        """
        return RepetitionSanitizeConfig.from_provider_config(
            self.provider.provider_config.get("context_sanitizer")
        )

    def _build_think_part(self, llm_resp: LLMResponse) -> ThinkPart:
        """Build the assistant ThinkPart after folding repeated reasoning.

        Args:
            llm_resp: Provider response carrying reasoning content.

        Returns:
            A ThinkPart with folded text and the original encrypted signature.
        """
        if llm_resp.reasoning_signature:
            return ThinkPart(
                think=llm_resp.reasoning_content or "",
                encrypted=llm_resp.reasoning_signature,
            )
        cfg = self.get_repetition_sanitize_config()
        result = sanitize_repetitive_text(
            llm_resp.reasoning_content or "", cfg
        )
        if result.changed:
            log = logger.info if result.collapsed_lines >= 200 else logger.debug
            log(
                "context sanitizer: collapsed %d blocks / %d lines "
                "(%d -> %d chars)",
                result.collapsed_blocks,
                result.collapsed_lines,
                result.original_chars,
                len(result.text),
            )
        return ThinkPart(think=result.text)
```

把 `_complete_with_assistant_response()` 里的：

```python
        if llm_resp.reasoning_content is not None or llm_resp.reasoning_signature:
            parts.append(
                ThinkPart(
                    think=llm_resp.reasoning_content or "",
                    encrypted=llm_resp.reasoning_signature,
                )
            )
```

替换为：

```python
        if llm_resp.reasoning_content is not None or llm_resp.reasoning_signature:
            parts.append(self._build_think_part(llm_resp))
```

把工具调用分支里同样的 `ThinkPart(...)` 构造替换为：

```python
            if llm_resp.reasoning_content is not None or llm_resp.reasoning_signature:
                parts.append(self._build_think_part(llm_resp))
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
uv run pytest tests/test_tool_loop_agent_runner.py -k "build_think_part" -v
```

Expected: PASS（3 passed）。

- [ ] **Step 5: 回归 runner 测试**

Run:

```powershell
uv run pytest tests/test_tool_loop_agent_runner.py -v
```

Expected: PASS（原有测试全部通过）。

- [ ] **Step 6: 格式化并提交**

```powershell
uv run ruff format astrbot/core/agent/runners/tool_loop_agent_runner.py tests/test_tool_loop_agent_runner.py
uv run ruff check astrbot/core/agent/runners/tool_loop_agent_runner.py tests/test_tool_loop_agent_runner.py
git add astrbot/core/agent/runners/tool_loop_agent_runner.py tests/test_tool_loop_agent_runner.py
git commit -m "feat(agent): 在 ThinkPart 写入点折叠重复推理"
```

---

### Task 4: Conversation 落库点集成（C）

**Files:**
- Modify: `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`
- Test: `tests/agent/test_repetition_sanitizer_history.py`

**Interfaces:**
- Consumes: Task 1 的 `sanitize_message_think_parts`、`RepetitionSanitizeConfig`；Task 3 的 `get_repetition_sanitize_config()`。
- Produces: `_save_to_history(..., repetition_cfg: RepetitionSanitizeConfig | None = None)`。

- [ ] **Step 1: 写失败测试**

创建 `tests/agent/test_repetition_sanitizer_history.py`：

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.agent.context.repetition import (
    RepetitionSanitizeConfig,
    sanitize_message_think_parts,
)
from astrbot.core.agent.message import Message, TextPart, ThinkPart
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse

REPEATED = ("OK.\nLet me write.\nLet me go.\nLet me go.\n") * 20


def test_sanitizes_assistant_think_parts_only():
    messages = [
        Message(
            role="assistant",
            content=[ThinkPart(think=REPEATED), TextPart(text="answer")],
        ),
        Message(role="user", content=[TextPart(text=REPEATED)]),
    ]
    messages[0]._no_save = True

    result = sanitize_message_think_parts(
        messages, RepetitionSanitizeConfig()
    )

    assert result[0] is not messages[0]
    assert result[0]._no_save is True
    assert result[0].content[0].think.count("Let me go.") == 2
    assert result[1] is messages[1]
    assert result[1].content[0].text == REPEATED


@pytest.mark.asyncio
async def test_save_to_history_sanitizes_before_dump():
    stage = object.__new__(InternalAgentSubStage)
    stage.conv_manager = SimpleNamespace(
        update_conversation=AsyncMock()
    )
    event = SimpleNamespace(
        unified_msg_origin="webchat:FriendMessage:test",
        get_extra=lambda key: None,
    )
    req = SimpleNamespace(
        conversation=SimpleNamespace(cid="cid", token_usage=0),
        tool_calls_result=None,
    )
    messages = [
        Message(role="assistant", content=[ThinkPart(think=REPEATED)])
    ]
    response = LLMResponse(role="assistant", completion_text="done")

    await stage._save_to_history(event, req, response, messages, None)

    history = stage.conv_manager.update_conversation.call_args.kwargs["history"]
    think_parts = [
        part
        for message in history
        for part in (message.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "think"
    ]
    assert think_parts
    assert think_parts[0]["think"].count("Let me go.") == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
uv run pytest tests/agent/test_repetition_sanitizer_history.py -v
```

Expected: FAIL，`TypeError: _save_to_history() got an unexpected keyword argument` 或历史中仍有完整重复。

- [ ] **Step 3: 实现**

在 `internal.py` 的 import 区补：

```python
from astrbot.core.agent.context.repetition import (
    RepetitionSanitizeConfig,
    sanitize_message_think_parts,
)
```

把 `_save_to_history` 签名改为：

```python
    async def _save_to_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        llm_response: LLMResponse | None,
        all_messages: list[Message],
        runner_stats: AgentStats | None,
        user_aborted: bool = False,
        repetition_cfg: RepetitionSanitizeConfig | None = None,
    ) -> None:
```

在 `messages_to_save` 构建完成之后、`checkpoint_id = ...` 之前插入：

```python
        messages_to_save = sanitize_message_think_parts(
            messages_to_save,
            repetition_cfg or RepetitionSanitizeConfig(),
        )
```

把两个 `_save_to_history(...)` 调用点各补一行：

```python
                                repetition_cfg=agent_runner.get_repetition_sanitize_config(),
```

和：

```python
                            repetition_cfg=agent_runner.get_repetition_sanitize_config(),
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
uv run pytest tests/agent/test_repetition_sanitizer_history.py -v
```

Expected: PASS（2 passed）。

- [ ] **Step 5: 回归相关测试**

Run:

```powershell
uv run pytest tests/agent/test_compaction_retention.py tests/test_tool_loop_compression_integrity.py -v
```

Expected: PASS。

- [ ] **Step 6: 格式化并提交**

```powershell
uv run ruff format astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py tests/agent/test_repetition_sanitizer_history.py
uv run ruff check astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py tests/agent/test_repetition_sanitizer_history.py
git add astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py tests/agent/test_repetition_sanitizer_history.py
git commit -m "feat(agent): conversation 落库前折叠重复推理"
```

---

### Task 5: ChatUI 历史读取点集成（D）

**Files:**
- Modify: `astrbot/dashboard/services/chat_service.py`
- Test: `tests/dashboard/test_chat_history_repetition.py`

**Interfaces:**
- Consumes: Task 1 的 `sanitize_history_think_parts`、`RepetitionSanitizeConfig`。
- Produces: ChatUI 历史响应中的 `think` 为折叠版，并附带 `think_original` / `think_sanitized` / `think_collapsed_lines`。

- [ ] **Step 1: 写失败测试**

创建 `tests/dashboard/test_chat_history_repetition.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
uv run pytest tests/dashboard/test_chat_history_repetition.py -v
```

Expected: FAIL，`KeyError: 'think_sanitized'`。

- [ ] **Step 3: 实现**

在 `chat_service.py` 的 import 区补：

```python
from astrbot.core.agent.context.repetition import (
    RepetitionSanitizeConfig,
    sanitize_history_think_parts,
)
```

在 `_sanitize_history_bot_records()` 的 `return history_data` 之前插入：

```python
    sanitize_history_think_parts(history_data, RepetitionSanitizeConfig())
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
uv run pytest tests/dashboard/test_chat_history_repetition.py -v
```

Expected: PASS（2 passed）。

- [ ] **Step 5: 回归 ChatUI 历史测试**

Run:

```powershell
uv run pytest tests/dashboard/test_chat_side_thread.py tests/test_chat_session_messages.py -v
```

Expected: PASS。

- [ ] **Step 6: 格式化并提交**

```powershell
uv run ruff format astrbot/dashboard/services/chat_service.py tests/dashboard/test_chat_history_repetition.py
uv run ruff check astrbot/dashboard/services/chat_service.py tests/dashboard/test_chat_history_repetition.py
git add astrbot/dashboard/services/chat_service.py tests/dashboard/test_chat_history_repetition.py
git commit -m "feat(chatui): 历史读取时折叠重复思考并保留原文"
```

---

### Task 6: 前端“查看原文”与 i18n

**Files:**
- Modify: `dashboard/src/components/chat/message_list_comps/ReasoningTimeline.vue`
- Modify: `dashboard/src/i18n/locales/zh-CN/features/chat.json`
- Modify: `dashboard/src/i18n/locales/en-US/features/chat.json`
- Modify: `dashboard/src/i18n/locales/ja-JP/features/chat.json`
- Modify: `dashboard/src/i18n/locales/ru-RU/features/chat.json`
- Test: `dashboard/src/components/chat/message_list_comps/ReasoningTimeline.spec.ts`

**Interfaces:**
- Consumes: ChatUI 历史响应中的 `think_original`、`think_sanitized`。
- Produces: 默认渲染 `think`，点击“查看原文”切换到 `think_original`。

- [ ] **Step 1: 写失败测试**

创建 `dashboard/src/components/chat/message_list_comps/ReasoningTimeline.spec.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import ReasoningTimeline from "./ReasoningTimeline.vue";

vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
}));

vi.mock("markstream-vue", () => ({
  MarkdownRender: {
    props: ["content"],
    template: '<div class="markdown-render">{{ content }}</div>',
  },
}));

vi.mock("./MarkdownMessagePart.vue", () => ({
  default: {
    props: ["content"],
    template: '<div class="markdown-part">{{ content }}</div>',
  },
}));

vi.mock("./ToolCallCard.vue", () => ({
  default: { template: "<div />" },
}));

vi.mock("./ToolCallItem.vue", () => ({
  default: { template: "<div />" },
}));

vi.mock("./IPythonToolBlock.vue", () => ({
  default: { template: "<div />" },
}));

vi.mock("./FileChangeCard.vue", () => ({
  default: { template: "<div />" },
}));

vi.mock("@/components/chat/markdownRenderConfig", () => ({
  CHAT_MARKDOWN_HEADING_STYLE: {},
  MARKDOWN_RENDER_MAX_LIVE_NODES: 0,
}));

vi.mock("@/utils/fileChangeTool", () => ({
  collectFileChanges: () => [],
}));

vi.mock("@/utils/scrollAnchor", () => ({
  anchorBottom: vi.fn(),
}));

const ORIGINAL = ("OK.\nLet me write.\nLet me go.\nLet me go.\n").repeat(20);

describe("ReasoningTimeline sanitized think", () => {
  it("shows the folded text and expands the original on demand", async () => {
    const wrapper = mount(ReasoningTimeline, {
      props: {
        parts: [
          {
            type: "think",
            think: "folded text",
            think_original: ORIGINAL,
            think_sanitized: true,
            think_collapsed_lines: 76,
          },
        ],
      },
    });

    expect(wrapper.text()).toContain("folded text");
    expect(wrapper.text()).not.toContain("Let me go.");

    const toggle = wrapper.get('[data-testid="think-original-toggle"]');
    expect(toggle.text()).toBe("reasoning.showOriginal");

    await toggle.trigger("click");

    expect(wrapper.text()).toContain("Let me go.");
    expect(toggle.text()).toBe("reasoning.showSanitized");
  });

  it("does not show the toggle for untouched think parts", () => {
    const wrapper = mount(ReasoningTimeline, {
      props: { parts: [{ type: "think", think: "plain thought" }] },
    });

    expect(wrapper.find('[data-testid="think-original-toggle"]').exists()).toBe(
      false,
    );
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
cd dashboard
pnpm vitest run src/components/chat/message_list_comps/ReasoningTimeline.spec.ts
```

Expected: FAIL，找不到 `[data-testid="think-original-toggle"]`。

- [ ] **Step 3: 实现模板与脚本**

在 `ReasoningTimeline.vue` 的模板中，把两处 `entry.think || ''` 替换为 `displayedThink(entry)`：

```vue
        <MarkdownMessagePart
          v-if="inert && entry.kind === 'think' && !thinkCollapsed(entry)"
          :content="displayedThink(entry)" :refs="null" :is-dark="Boolean(isDark)"
          :custom-html-tags="[]" :is-streaming="isStreaming" inert
        />
        <MarkdownRender
          v-else-if="entry.kind === 'think' && !thinkCollapsed(entry)"
          :content="displayedThink(entry)"
          class="chat-markdown reasoning-text markdown-content"
          :final="!isStreaming"
          :smooth-streaming="isStreaming ? 'auto' : false"
          :fade="false"
          :typewriter="false"
          :is-dark="isDark"
          :max-live-nodes="MARKDOWN_RENDER_MAX_LIVE_NODES"
          :style="CHAT_MARKDOWN_HEADING_STYLE"
        />
```

在 `think-collapse-toggle` 之前新增：

```vue
        <button
          v-if="entry.kind === 'think' && entry.thinkSanitized"
          class="think-collapse-toggle"
          data-testid="think-original-toggle"
          type="button"
          @click="toggleOriginalThink(entry.key)"
        >
          {{
            showOriginalThink.has(entry.key)
              ? tm("reasoning.showSanitized")
              : tm("reasoning.showOriginal")
          }}
        </button>
```

在 `<script setup>` 中扩展 `TimelineEntry` 的 think 分支：

```ts
  | {
      key: string;
      kind: "think";
      title: string;
      think: string;
      thinkOriginal?: string;
      thinkSanitized?: boolean;
    }
```

在 `timelineEntries` 的 think 分支中补充字段：

```ts
        think,
        thinkOriginal:
          typeof part.think_original === "string"
            ? part.think_original
            : undefined,
        thinkSanitized: part.think_sanitized === true,
```

在 `timelineEntries` 之后新增：

```ts
const showOriginalThink = ref(new Set<string>());

function displayedThink(entry: TimelineEntry): string {
  if (entry.kind !== "think") return "";
  if (entry.thinkSanitized && showOriginalThink.value.has(entry.key)) {
    return entry.thinkOriginal ?? entry.think;
  }
  return entry.think;
}

function toggleOriginalThink(key: string): void {
  const next = new Set(showOriginalThink.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  showOriginalThink.value = next;
}
```

把 `updateLiveThinkPreview()` 中的 `last.think` 改为 `displayedThink(last)`：

```ts
  liveThinkPreview.value =
    last && last.kind === "think"
      ? tailPreview(displayedThink(last)).text
      : "";
```

把 `thinkPreviewText()` 中的 `entry.think` 改为 `displayedThink(entry)`：

```ts
  return entry.kind === "think"
    ? tailPreview(displayedThink(entry)).text
    : "";
```

- [ ] **Step 4: 补 i18n 文案**

在四个 `chat.json` 的顶层 `reasoning` 对象中，紧跟 `"expandThink"` / `"collapseThink"` 之后加入：

`zh-CN`：

```json
    "showOriginal": "查看原文",
    "showSanitized": "收起原文",
```

`en-US`：

```json
    "showOriginal": "Show original",
    "showSanitized": "Hide original",
```

`ja-JP`：

```json
    "showOriginal": "原文を表示",
    "showSanitized": "原文を隠す",
```

`ru-RU`：

```json
    "showOriginal": "Показать оригинал",
    "showSanitized": "Скрыть оригинал",
```

- [ ] **Step 5: 运行测试确认通过**

Run:

```powershell
cd dashboard
pnpm vitest run src/components/chat/message_list_comps/ReasoningTimeline.spec.ts
```

Expected: PASS（2 passed）。

- [ ] **Step 6: 类型检查与提交**

Run:

```powershell
cd dashboard
pnpm typecheck
```

Expected: 无类型错误。

```powershell
git add dashboard/src/components/chat/message_list_comps/ReasoningTimeline.vue dashboard/src/components/chat/message_list_comps/ReasoningTimeline.spec.ts dashboard/src/i18n/locales/zh-CN/features/chat.json dashboard/src/i18n/locales/en-US/features/chat.json dashboard/src/i18n/locales/ja-JP/features/chat.json dashboard/src/i18n/locales/ru-RU/features/chat.json
git commit -m "feat(chatui): 支持展开消毒后的思考原文"
```

---

### Task 7: 全量验证

**Files:** 无新增；只运行检查。

- [ ] **Step 1: 后端相关测试**

Run:

```powershell
uv run pytest tests/agent/test_repetition_sanitizer.py tests/agent/test_repetition_sanitizer_history.py tests/test_tool_loop_agent_runner.py tests/dashboard/test_chat_history_repetition.py tests/dashboard/test_chat_side_thread.py tests/test_chat_session_messages.py -v
```

Expected: PASS。

- [ ] **Step 2: 后端格式化与静态检查**

Run:

```powershell
uv run ruff format astrbot/core/agent/context/repetition.py astrbot/core/agent/runners/tool_loop_agent_runner.py astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py astrbot/dashboard/services/chat_service.py tests/agent/test_repetition_sanitizer.py tests/agent/test_repetition_sanitizer_history.py tests/dashboard/test_chat_history_repetition.py
uv run ruff check astrbot/core/agent/context/repetition.py astrbot/core/agent/runners/tool_loop_agent_runner.py astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py astrbot/dashboard/services/chat_service.py tests/agent/test_repetition_sanitizer.py tests/agent/test_repetition_sanitizer_history.py tests/dashboard/test_chat_history_repetition.py
```

Expected: `All checks passed!`。

- [ ] **Step 3: 前端相关测试与类型检查**

Run:

```powershell
cd dashboard
pnpm vitest run src/components/chat/message_list_comps/ReasoningTimeline.spec.ts src/components/chat/ReasoningSidebar.spec.ts
pnpm typecheck
```

Expected: PASS，无类型错误。

- [ ] **Step 4: 检查工作区**

Run:

```powershell
git status --short
```

Expected: 只包含本计划涉及的文件；不要提交无关改动。

---

## Self-Review

**Spec coverage:**

- 纯函数、配置、守卫、主导重复块、兜底、标记：Task 1。
- D40 真实数据基线：Task 2。
- A1/A2 写入点、encrypted 跳过、provider 开关：Task 3。
- C 落库点、惰性清洗：Task 4。
- D ChatUI 读取点、`think_original` / `think_sanitized`：Task 5。
- 前端默认消毒版、单条展开原文、i18n：Task 6。
- 全量验证：Task 7。

**Placeholder scan:** 无 TBD/TODO；每个代码步骤都有完整代码。

**Type consistency:** `RepetitionSanitizeConfig`、`SanitizeResult`、`sanitize_repetitive_text`、`sanitize_think_part`、`sanitize_message_think_parts`、`sanitize_history_think_parts` 在各任务中名称一致；前端字段 `think_original` / `think_sanitized` / `think_collapsed_lines` 与后端一致。

**Known risk:** `test_d40_2503_collapses_loop` 的 `collapsed_lines >= 1300` 与 `collapsed_lines >= 6800` 是基于 2026-11-09 的真实数据；如果数据被替换，先打印实际计数再调整阈值，但不要放宽“非填充行必须保留”。
