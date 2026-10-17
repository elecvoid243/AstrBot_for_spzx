"""Targeted diagnostics for the two findings from the stress run."""

from __future__ import annotations

import time

from astrbot.core.tools.computer_tools.edit_engine import decode_text_bytes
from astrbot.core.tools.computer_tools.patch_engine import parse_patch, plan_patch


def fix_tab_case() -> None:
    patch = "*** Update File: a.py\n-def f():\n-\treturn 1\n+def f():\n+\treturn 2\n"
    try:
        plan = plan_patch(parse_patch(patch), lambda p: b"def f():\n    return 1\n")
        out = (plan.files[0].new_bytes or b"").decode()
        print("M4 resulting content:", repr(out))
        print("M4 contains tab-indented line:", "\treturn 2" in out)
    except Exception as exc:  # noqa: BLE001 - expected after the F1 fix
        print(f"M4 now rejected: {type(exc).__name__}: {exc}")


def perf_scaling() -> None:
    for line_count in (1000, 2000, 5000, 10000):
        text = "".join(f"line_{i} = {i}\n" for i in range(line_count))
        patch = (
            "*** Update File: big.txt\n"
            f"-line_{line_count - 2} = {line_count - 2}\n"
            f"+line_{line_count - 2} = 0\n"
        )
        start = time.perf_counter()
        plan_patch(parse_patch(patch), lambda p: text.encode())
        elapsed = time.perf_counter() - start
        print(f"perf {line_count:>6} lines: {elapsed * 1000:8.1f} ms")


def bom_note() -> None:
    raw = b"\xef\xbb\xbfa\r\n"
    text, eol, has_bom = decode_text_bytes(raw)
    print("S6 decoded:", repr(text), repr(eol), has_bom)


def tab_variants() -> None:
    base = b"def f():\n    return 1\n"
    cases = {
        "no-context old with tab": (
            "*** Update File: a.py\n-\treturn 1\n+\treturn 2\n"
        ),
        "exact old, tab in new": (
            "*** Update File: a.py\n-    return 1\n+\treturn 2\n"
        ),
        "unindented context + tab in new": (
            "*** Update File: a.py\n-def f():\n-    return 1\n+def f():\n+\treturn 2\n"
        ),
    }
    for name, patch in cases.items():
        try:
            plan = plan_patch(parse_patch(patch), lambda p: base)
            out = (plan.files[0].new_bytes or b"").decode()
            print(f"{name}: OK -> {out!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: ERROR -> {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    fix_tab_case()
    tab_variants()
    bom_note()
    perf_scaling()
