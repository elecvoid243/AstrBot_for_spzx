"""Check what location information reaches the model via tool errors."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.tools.computer_tools import fs as fs_tools
from astrbot.core.tools.computer_tools import util as computer_util
from astrbot.core.tools.computer_tools.patch_tool import ApplyPatchTool


def context(umo="qq:friend:user-1"):
    holder = SimpleNamespace(
        get_config=lambda umo=None: {
            "provider_settings": {
                "computer_use_require_admin": False,
                "computer_use_runtime": "local",
                "file_access_default_mode": "full",
            }
        }
    )
    event = SimpleNamespace(
        role="admin", unified_msg_origin=umo, get_sender_id=lambda: "u"
    )
    return ContextWrapper(context=SimpleNamespace(context=holder, event=event))


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="patch_err_"))
    computer_util.get_astrbot_workspaces_path = lambda: str(tmp / "workspaces")
    (tmp / "workspaces").mkdir(parents=True)
    ws = (
        tmp
        / "workspaces"
        / computer_util.normalize_umo_for_workspace("qq:friend:user-1")
    )
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "a.txt").write_text("old\n", encoding="utf-8")
    fs_tools.fs_access.sp = SimpleNamespace(
        session_get=AsyncMock(return_value=None),
        session_put=AsyncMock(),
    )

    cases = {
        "parse error (line 2)": "*** Update File: a.txt\n?bad\n",
        "hunk not found (hunk 2)": ("*** Update File: a.txt\n-old\n+new\n-never\n+x\n"),
        "missing file": "*** Update File: missing.txt\n-a\n+b\n",
    }
    for name, patch in cases.items():
        result = await ApplyPatchTool().call(context(), patch=patch)
        print(f"{name}:\n  {result}\n")


if __name__ == "__main__":
    asyncio.run(main())
