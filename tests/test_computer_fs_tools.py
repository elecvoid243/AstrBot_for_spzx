from __future__ import annotations

import base64
import io
import locale
import os
import sys
import zipfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, ImageContent
from PIL import Image

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.computer import file_read_utils
from astrbot.core.computer.booters.local import LocalBooter
from astrbot.core.tools.computer_tools import fs as fs_tools
from astrbot.core.tools.computer_tools import util as computer_util


def _make_context(
    *,
    require_admin: bool = True,
    role: str = "admin",
    runtime: str = "local",
    umo: str = "qq:friend:user-1",
    file_access_default_mode: str = "full",
) -> ContextWrapper:
    config_holder = SimpleNamespace(
        get_config=lambda umo=None: {
            "provider_settings": {
                "computer_use_require_admin": require_admin,
                "computer_use_runtime": runtime,
                "file_access_default_mode": file_access_default_mode,
            }
        }
    )
    event = SimpleNamespace(
        role=role,
        unified_msg_origin=umo,
        get_sender_id=lambda: "user-1",
    )
    astr_ctx = SimpleNamespace(context=config_holder, event=event)
    return ContextWrapper(context=astr_ctx)


def _make_sandbox_context(
    *,
    role: str = "admin",
    umo: str = "qq:friend:user-1",
):
    config_holder = SimpleNamespace(
        get_config=lambda umo=None: {
            "provider_settings": {
                "computer_use_require_admin": True,
                "computer_use_runtime": "sandbox",
            }
        }
    )
    event = SimpleNamespace(
        role=role,
        unified_msg_origin=umo,
        send=AsyncMock(),
    )
    astr_ctx = SimpleNamespace(context=config_holder, event=event)
    return ContextWrapper(context=astr_ctx)


@pytest.fixture(autouse=True)
def _fake_fs_access_sp(monkeypatch: pytest.MonkeyPatch):
    """Isolate fs_access custom-root persistence from the real store."""

    class _FakeSp:
        async def session_get(self, umo, key, default=None):
            return default

        async def session_put(self, umo, key, value):
            return None

    monkeypatch.setattr(fs_tools.fs_access, "sp", _FakeSp())


@pytest.mark.asyncio
async def test_sandbox_file_download_handles_windows_remote_filename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fs_tools,
        "get_astrbot_temp_path",
        lambda: str(temp_root),
    )

    async def _download_file(_remote_path, local_path):
        assert local_path.endswith("report.txt")
        assert "\\" not in local_path

    booter = SimpleNamespace(download_file=AsyncMock(side_effect=_download_file))

    async def _fake_get_booter(_ctx, _umo):
        return booter

    monkeypatch.setattr(fs_tools, "get_booter", _fake_get_booter)

    context = _make_sandbox_context()
    result = await fs_tools.FileDownloadTool().call(
        context,
        remote_path=r"C:\Users\AstrBot\report.txt",
        also_send_to_user=True,
    )

    assert "report.txt" in result
    sent_chain = context.context.event.send.await_args.args[0]
    sent_file = sent_chain.chain[0]
    assert sent_file.name == "report.txt"


@pytest.mark.asyncio
async def test_sandbox_file_download_strips_trailing_remote_slash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fs_tools,
        "get_astrbot_temp_path",
        lambda: str(temp_root),
    )

    booter = SimpleNamespace(download_file=AsyncMock())

    async def _fake_get_booter(_ctx, _umo):
        return booter

    monkeypatch.setattr(fs_tools, "get_booter", _fake_get_booter)

    context = _make_sandbox_context()
    result = await fs_tools.FileDownloadTool().call(
        context,
        remote_path="reports/export/",
        also_send_to_user=True,
    )

    assert "export" in result
    sent_chain = context.context.event.send.await_args.args[0]
    sent_file = sent_chain.chain[0]
    assert sent_file.name == "export"


def _setup_local_fs_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    umo: str = "qq:friend:user-1",
) -> Any:
    workspaces_root = tmp_path / "workspaces"
    skills_root = tmp_path / "skills"
    plugins_root = tmp_path / "plugins"
    builtin_plugins_root = tmp_path / "builtin_plugins"
    temp_root = tmp_path / "temp"
    workspaces_root.mkdir()
    skills_root.mkdir()
    plugins_root.mkdir()
    builtin_plugins_root.mkdir()
    temp_root.mkdir()

    monkeypatch.setattr(
        computer_util,
        "get_astrbot_workspaces_path",
        lambda: str(workspaces_root),
    )
    monkeypatch.setattr(
        fs_tools,
        "get_astrbot_skills_path",
        lambda: str(skills_root),
    )
    monkeypatch.setattr(
        fs_tools,
        "get_astrbot_plugin_path",
        lambda: str(plugins_root),
    )
    monkeypatch.setattr(
        fs_tools,
        "get_astrbot_builtin_plugin_path",
        lambda: str(builtin_plugins_root),
    )
    monkeypatch.setattr(
        fs_tools,
        "get_astrbot_temp_path",
        lambda: str(temp_root),
    )
    monkeypatch.setattr(
        file_read_utils,
        "get_astrbot_temp_path",
        lambda: str(temp_root),
    )

    booter = LocalBooter()

    async def _fake_get_booter(_ctx, _umo):
        return booter

    monkeypatch.setattr(fs_tools, "get_booter", _fake_get_booter)

    normalized_umo = computer_util.normalize_umo_for_workspace(umo)
    workspace = workspaces_root / normalized_umo
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _make_large_text() -> str:
    return "".join(f"line-{index:05d}-{'x' * 48}\n" for index in range(6000))


def _make_hardlink_or_skip(source, link) -> None:
    try:
        os.link(source, link)
    except (AttributeError, OSError) as exc:
        pytest.skip(f"hard links are unavailable on this filesystem: {exc}")


def _make_epub_bytes(*, chapter_count: int = 1) -> bytes:
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    ]
    spine_items = ['<itemref idref="nav"/>']
    nav_links = []

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )

        for index in range(1, chapter_count + 1):
            manifest_items.append(
                f'<item id="chapter{index}" href="chapter{index}.xhtml" '
                'media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="chapter{index}"/>')
            nav_links.append(
                f'<li><a href="chapter{index}.xhtml">Chapter {index}</a></li>'
            )
            archive.writestr(
                f"OEBPS/chapter{index}.xhtml",
                f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Chapter {index}</title>
  </head>
  <body>
    <h1>Chapter {index}</h1>
    <p>Paragraph {index}</p>
  </body>
</html>
""",
            )

        archive.writestr(
            "OEBPS/nav.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Navigation</title>
  </head>
  <body>
    <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
      <ol>
        {links}
      </ol>
    </nav>
  </body>
</html>
""".format(links="".join(nav_links)),
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">test-book</dc:identifier>
    <dc:title>Test Book</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    {manifest}
  </manifest>
  <spine>
    {spine}
  </spine>
</package>
""".format(
                manifest="".join(manifest_items),
                spine="".join(spine_items),
            ),
        )

    return buffer.getvalue()


@pytest.mark.asyncio
async def test_restricted_local_member_can_read_plugin_provided_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    _setup_local_fs_tools(monkeypatch, tmp_path)
    plugin_skill = (
        tmp_path
        / "plugins"
        / "astrbot_plugin_demo"
        / "skills"
        / "demo-skill"
        / "SKILL.md"
    )
    plugin_skill.parent.mkdir(parents=True)
    plugin_skill.write_text("# Demo Skill\n\nRead plugin docs.", encoding="utf-8")

    result = await fs_tools.FileReadTool().call(
        _make_context(role="member"),
        path=str(plugin_skill),
    )

    assert result == "# Demo Skill\n\nRead plugin docs."


@pytest.mark.asyncio
async def test_restricted_local_member_can_read_builtin_plugin_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    _setup_local_fs_tools(monkeypatch, tmp_path)
    builtin_skill = (
        tmp_path
        / "builtin_plugins"
        / "astrbot"
        / "skills"
        / "skill-creator"
        / "SKILL.md"
    )
    builtin_skill.parent.mkdir(parents=True)
    builtin_skill.write_text("# Skill Creator\n", encoding="utf-8")

    result = await fs_tools.FileReadTool().call(
        _make_context(role="member"),
        path=str(builtin_skill),
    )

    assert result == "# Skill Creator\n"


@pytest.mark.asyncio
async def test_restricted_local_member_can_read_plugin_skill_inventory_even_if_plugin_inactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    _setup_local_fs_tools(monkeypatch, tmp_path)
    plugin_skill = (
        tmp_path
        / "plugins"
        / "astrbot_plugin_demo"
        / "skills"
        / "demo-skill"
        / "SKILL.md"
    )
    plugin_skill.parent.mkdir(parents=True)
    plugin_skill.write_text("# Demo Skill\n", encoding="utf-8")

    result = await fs_tools.FileReadTool().call(
        _make_context(role="member"),
        path=str(plugin_skill),
    )

    assert result == "# Demo Skill\n"


@pytest.mark.asyncio
async def test_restricted_local_member_cannot_write_plugin_provided_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    _setup_local_fs_tools(monkeypatch, tmp_path)
    plugin_skill = (
        tmp_path
        / "plugins"
        / "astrbot_plugin_demo"
        / "skills"
        / "demo-skill"
        / "SKILL.md"
    )
    plugin_skill.parent.mkdir(parents=True)
    plugin_skill.write_text("# Demo Skill\n", encoding="utf-8")

    result = await fs_tools.FileWriteTool().call(
        _make_context(role="member"),
        path=str(plugin_skill),
        content="# Changed\n",
    )

    assert "Write access is restricted for this user." in result
    assert "data/plugins/*/skills" not in result
    assert plugin_skill.read_text(encoding="utf-8") == "# Demo Skill\n"


@pytest.mark.asyncio
async def test_restricted_local_member_cannot_modify_locally_installed_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    installed_skill = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    installed_skill.parent.mkdir(parents=True)
    installed_skill.write_text("# Demo Skill\n", encoding="utf-8")
    workspace_skill = workspace / "skills" / "demo-skill" / "SKILL.md"
    workspace_skill.parent.mkdir(parents=True)

    context = _make_context(role="member")
    read_result = await fs_tools.FileReadTool().call(
        context,
        path=str(installed_skill),
    )
    write_result = await fs_tools.FileWriteTool().call(
        context,
        path=str(installed_skill),
        content="# Changed\n",
    )
    edit_result = await fs_tools.FileEditTool().call(
        context,
        path=str(installed_skill),
        old="Demo",
        new="Changed",
    )
    workspace_result = await fs_tools.FileWriteTool().call(
        context,
        path=str(workspace_skill),
        content="# Workspace Skill\n",
    )

    assert read_result == "# Demo Skill\n"
    assert "Write access is restricted for this user." in write_result
    assert "Write access is restricted for this user." in edit_result
    assert "data/skills" not in write_result
    assert installed_skill.read_text(encoding="utf-8") == "# Demo Skill\n"
    assert workspace_result == f"File written successfully: {workspace_skill}"
    assert workspace_skill.read_text(encoding="utf-8") == "# Workspace Skill\n"


@pytest.mark.asyncio
async def test_local_admin_can_modify_locally_installed_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    _setup_local_fs_tools(monkeypatch, tmp_path)
    installed_skill = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    installed_skill.parent.mkdir(parents=True)

    result = await fs_tools.FileWriteTool().call(
        _make_context(role="admin"),
        path=str(installed_skill),
        content="# Demo Skill\n",
    )

    assert result == f"File written successfully: {installed_skill} (encoding: utf-8)"
    assert installed_skill.read_text(encoding="utf-8") == "# Demo Skill\n"


@pytest.mark.asyncio
async def test_restricted_local_member_rejects_workspace_hardlink_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("outside-secret\n", encoding="utf-8")
    hardlink_path = workspace / "linked.txt"
    _make_hardlink_or_skip(outside_file, hardlink_path)

    read_result = await fs_tools.FileReadTool().call(
        _make_context(role="member"),
        path="linked.txt",
    )
    write_result = await fs_tools.FileWriteTool().call(
        _make_context(role="member"),
        path="linked.txt",
        content="changed\n",
    )

    assert "multiple hard links" in read_result
    assert "may alias content outside allowed directories" in read_result
    assert "multiple hard links" in write_result
    assert "may alias content outside allowed directories" in write_result
    assert outside_file.read_text(encoding="utf-8") == "outside-secret\n"


def test_detect_text_encoding_allows_utf8_probe_cut_mid_character():
    sample = '{"results": ["中文内容"]}'.encode()[:-1]

    assert file_read_utils.detect_text_encoding(sample) in {"utf-8", "utf-8-sig"}


@pytest.mark.asyncio
async def test_file_read_tool_rejects_large_full_text_read_before_local_stream_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    large_file = workspace / "large.txt"
    large_file.write_text(_make_large_text(), encoding="utf-8")

    async def _unexpected_read(*args, **kwargs):
        raise AssertionError("full file read should be rejected before streaming")

    monkeypatch.setattr(file_read_utils, "read_local_text_range", _unexpected_read)

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="large.txt",
    )

    assert "text file exceeds 262144 bytes" in result
    assert "Use `offset` and `limit`" in result


@pytest.mark.asyncio
async def test_file_read_tool_allows_partial_read_for_large_text_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    large_file = workspace / "large.txt"
    lines = [f"line-{index:05d}\n" for index in range(50000)]
    large_file.write_text("".join(lines), encoding="utf-8")

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="large.txt",
        offset=1000,
        limit=3,
    )

    assert result == "".join(lines[1000:1003])


@pytest.mark.asyncio
async def test_file_read_tool_returns_image_call_tool_result_for_images(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    image_path = workspace / "sample.png"
    Image.new("RGB", (32, 16), color=(255, 0, 0)).save(image_path, format="PNG")

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="sample.png",
    )

    assert isinstance(result, CallToolResult)
    assert len(result.content) == 1
    assert isinstance(result.content[0], ImageContent)
    assert result.content[0].mimeType == "image/jpeg"
    assert base64.b64decode(result.content[0].data).startswith(b"\xff\xd8\xff")


@pytest.mark.asyncio
async def test_file_read_tool_treats_svg_as_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    svg_path = workspace / "shape.svg"
    svg_text = (
        "<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>"
    )
    svg_path.write_text(svg_text, encoding="utf-8")

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="shape.svg",
    )

    assert result == svg_text


@pytest.mark.asyncio
async def test_file_read_tool_reads_pdf_via_parser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    pdf_path = workspace / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n")

    async def _fake_parse_pdf(_file_bytes: bytes, _file_name: str) -> str:
        return "page-1\npage-2\n"

    monkeypatch.setattr(file_read_utils, "_parse_local_pdf_text", _fake_parse_pdf)

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="doc.pdf",
    )

    assert result == "page-1\npage-2\n"


@pytest.mark.asyncio
async def test_file_read_tool_reads_docx_via_parser_and_magic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    docx_path = workspace / "report.bin"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    docx_path.write_bytes(buffer.getvalue())

    async def _fake_parse_docx(_file_bytes: bytes, _file_name: str) -> str:
        return "doc-line-1\ndoc-line-2\n"

    monkeypatch.setattr(file_read_utils, "_parse_local_docx_text", _fake_parse_docx)

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="report.bin",
    )

    assert result == "doc-line-1\ndoc-line-2\n"


def test_is_epub_bytes_rejects_plain_zip_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr("README.txt", "hello")

    assert file_read_utils._is_epub_bytes(buffer.getvalue()) is False


@pytest.mark.asyncio
async def test_file_read_tool_reads_epub_via_parser_and_magic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    epub_path = workspace / "novel.bin"
    epub_path.write_bytes(_make_epub_bytes(chapter_count=2))

    async def _fake_parse_epub(_file_bytes: bytes, _file_name: str) -> str:
        return "# Chapter 1\n\nParagraph 1\n"

    monkeypatch.setattr(file_read_utils, "_parse_local_epub_text", _fake_parse_epub)

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="novel.bin",
    )

    assert result == "# Chapter 1\n\nParagraph 1\n"


@pytest.mark.asyncio
async def test_file_read_tool_stores_long_converted_document_in_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    pdf_path = workspace / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nfake\n")
    long_text = _make_large_text()

    async def _fake_parse_pdf(_file_bytes: bytes, _file_name: str) -> str:
        return long_text

    monkeypatch.setattr(file_read_utils, "_parse_local_pdf_text", _fake_parse_pdf)

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="manual.pdf",
    )

    converted_root = workspace / "converted_files"
    converted_files = list(converted_root.glob("manual.pdf_*/text.txt"))
    assert len(converted_files) == 1
    assert converted_files[0].read_text(encoding="utf-8") == long_text
    assert str(converted_files[0]) in result
    assert "Read or grep that file with a narrow window." in result


@pytest.mark.asyncio
async def test_grep_tool_applies_result_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    text_path = workspace / "grep.txt"
    text_path.write_text(
        "match-1\nmatch-2\nmatch-3\nmatch-4\n",
        encoding="utf-8",
    )

    result = await fs_tools.GrepTool().call(
        _make_context(),
        pattern="match",
        path="grep.txt",
        result_limit=2,
    )

    assert "match-1" in result
    assert "match-2" in result
    assert "match-3" not in result
    assert "[Truncated to first 2 result groups.]" in result


@pytest.mark.asyncio
async def test_file_read_tool_rejects_directory_with_clear_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """FileReadTool should return a helpful message when given a directory path."""
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    subdir = workspace / "my-directory"
    subdir.mkdir()

    result = await fs_tools.FileReadTool().call(
        _make_context(),
        path="my-directory",
    )

    assert "is a directory, not a file" in result
    assert "my-directory" in result
    assert "'astrbot_execute_shell'" in result


@pytest.mark.asyncio
async def test_file_write_tool_blocked_in_readonly_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.READONLY)
    try:
        context = _make_context(umo=umo)
        booter = SimpleNamespace(
            fs=SimpleNamespace(write_file=AsyncMock(return_value={"success": True}))
        )
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock(return_value=booter))
        result = await fs_tools.FileWriteTool().call(
            context, path=str(tmp_path / "a.txt"), content="x"
        )
        assert result.startswith("Error:")
        assert "readonly" in result
        booter.fs.write_file.assert_not_awaited()
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_file_edit_tool_blocked_in_readonly_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.READONLY)
    try:
        context = _make_context(umo=umo)
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock())
        result = await fs_tools.FileEditTool().call(
            context, path=str(tmp_path / "a.txt"), old="a", new="b"
        )
        assert result.startswith("Error:")
        assert "readonly" in result
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_file_write_tool_workspace_mode_restricts_admin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.WORKSPACE)
    try:
        ws = tmp_path / "ws"
        ws.mkdir()

        async def _fake_root(_ctx):
            return ws

        monkeypatch.setattr(fs_tools, "workspace_root_for_context", _fake_root)
        booter = SimpleNamespace(
            fs=SimpleNamespace(write_file=AsyncMock(return_value={"success": True}))
        )
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock(return_value=booter))
        context = _make_context(umo=umo, role="admin")

        ok_result = await fs_tools.FileWriteTool().call(
            context, path=str(ws / "inside.txt"), content="x"
        )
        assert ok_result.startswith("File written successfully")

        blocked = await fs_tools.FileWriteTool().call(
            context, path=str(tmp_path / "outside.txt"), content="x"
        )
        assert blocked.startswith("Error:")
        assert "Allowed directories" in blocked
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_file_write_tool_workspace_mode_honors_dynamic_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.WORKSPACE)
    wt = tmp_path / "worktree"
    wt.mkdir()
    fs_access.set_dynamic_roots(umo, [wt])
    try:

        async def _fake_root(_ctx):
            return tmp_path / "ws"

        monkeypatch.setattr(fs_tools, "workspace_root_for_context", _fake_root)
        booter = SimpleNamespace(
            fs=SimpleNamespace(write_file=AsyncMock(return_value={"success": True}))
        )
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock(return_value=booter))
        context = _make_context(umo=umo, role="admin")
        result = await fs_tools.FileWriteTool().call(
            context, path=str(wt / "main.py"), content="x"
        )
        assert result.startswith("File written successfully")
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_file_write_tool_encoding_param_selects_codec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    booter = SimpleNamespace(
        fs=SimpleNamespace(write_file=AsyncMock(return_value={"success": True}))
    )
    monkeypatch.setattr(fs_tools, "get_booter", AsyncMock(return_value=booter))
    context = _make_context()

    result = await fs_tools.FileWriteTool().call(
        context, path=str(tmp_path / "a.txt"), content="x"
    )
    assert result.startswith("File written successfully")
    assert booter.fs.write_file.await_args.kwargs["encoding"] == "utf-8"

    for alias in ("utf-8 bom", "utf-8-sig", "UTF-8 BOM"):
        await fs_tools.FileWriteTool().call(
            context, path=str(tmp_path / "b.txt"), content="x", encoding=alias
        )
        assert booter.fs.write_file.await_args.kwargs["encoding"] == "utf-8-sig"

    await fs_tools.FileWriteTool().call(
        context, path=str(tmp_path / "c.txt"), content="x", encoding="gbk"
    )
    assert booter.fs.write_file.await_args.kwargs["encoding"] == "gbk"

    await fs_tools.FileWriteTool().call(
        context, path=str(tmp_path / "d.txt"), content="x", encoding="ansi"
    )
    ansi_encoding = booter.fs.write_file.await_args.kwargs["encoding"]
    if sys.platform == "win32":
        assert ansi_encoding.startswith("cp")
    else:
        assert ansi_encoding == (locale.getpreferredencoding(False) or "utf-8").lower()


@pytest.mark.asyncio
async def test_local_file_write_with_bom_emits_utf8_bom(tmp_path):
    from astrbot.core.computer.booters.local import LocalFileSystemComponent

    fs = LocalFileSystemComponent()
    target = tmp_path / "bom.txt"

    result = await fs.write_file(
        path=str(target), content="hello", mode="w", encoding="utf-8-sig"
    )

    assert result["success"] is True
    assert target.read_bytes() == b"\xef\xbb\xbfhello"


@pytest.mark.asyncio
async def test_local_file_write_tool_gbk_encoding_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
    target = workspace / "gbk.txt"

    result = await fs_tools.FileWriteTool().call(
        _make_context(),
        path=str(target),
        content="中文内容",
        encoding="gbk",
    )

    assert result.startswith("File written successfully")
    assert "gbk" in result
    assert target.read_bytes() == "中文内容".encode("gbk")


@pytest.mark.asyncio
async def test_file_read_tool_unaffected_by_readonly_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.READONLY)
    try:
        target = tmp_path / "readable.txt"
        target.write_text("hello", encoding="utf-8")

        async def _fake_root(_ctx):
            return tmp_path

        monkeypatch.setattr(fs_tools, "workspace_root_for_context", _fake_root)
        monkeypatch.setattr(
            fs_tools,
            "read_file_tool_result",
            AsyncMock(return_value="hello"),
        )
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock())
        context = _make_context(umo=umo)
        result = await fs_tools.FileReadTool().call(context, path=str(target))
        assert result == "hello"
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_file_read_tool_workspace_mode_includes_dynamic_roots_for_restricted_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """Non-admin + workspace mode: reads cover the dynamic root, stay blocked outside."""
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.WORKSPACE)
    wt = tmp_path / "worktree"
    wt.mkdir()
    fs_access.set_dynamic_roots(umo, [wt])
    try:
        target = wt / "main.py"
        target.write_text("print('hi')", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_file = outside / "secret.txt"
        outside_file.write_text("secret", encoding="utf-8")

        async def _fake_root(_ctx):
            return tmp_path / "ws"

        monkeypatch.setattr(fs_tools, "workspace_root_for_context", _fake_root)
        monkeypatch.setattr(
            fs_tools,
            "read_file_tool_result",
            AsyncMock(return_value="print('hi')"),
        )
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock())
        context = _make_context(umo=umo, role="member")

        allowed = await fs_tools.FileReadTool().call(context, path=str(target))
        assert allowed == "print('hi')"

        blocked = await fs_tools.FileReadTool().call(context, path=str(outside_file))
        assert blocked.startswith("Error:")
        assert "Read access is restricted" in blocked
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_file_read_tool_workspace_mode_admin_arbitrary_path_still_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """Admin + workspace mode: reads are never restricted, arbitrary paths work."""
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.WORKSPACE)
    try:
        target = tmp_path / "outside" / "anywhere.txt"
        target.parent.mkdir()
        target.write_text("free", encoding="utf-8")

        async def _fake_root(_ctx):
            return tmp_path / "ws"

        monkeypatch.setattr(fs_tools, "workspace_root_for_context", _fake_root)
        monkeypatch.setattr(
            fs_tools,
            "read_file_tool_result",
            AsyncMock(return_value="free"),
        )
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock())
        context = _make_context(umo=umo, role="admin")
        result = await fs_tools.FileReadTool().call(context, path=str(target))
        assert result == "free"
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_grep_tool_workspace_mode_includes_dynamic_roots_for_restricted_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """Non-admin + workspace mode: grep may search inside the dynamic root only."""
    from astrbot.core.tools import fs_access

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.WORKSPACE)
    wt = tmp_path / "worktree"
    wt.mkdir()
    fs_access.set_dynamic_roots(umo, [wt])
    try:
        (wt / "main.py").write_text("match-here\n", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("match-there\n", encoding="utf-8")

        async def _fake_root(_ctx):
            return tmp_path / "ws"

        booter = SimpleNamespace(
            fs=SimpleNamespace(
                search_files=AsyncMock(
                    return_value={"success": True, "content": "match-here\n"}
                )
            )
        )
        monkeypatch.setattr(fs_tools, "workspace_root_for_context", _fake_root)
        monkeypatch.setattr(fs_tools, "get_booter", AsyncMock(return_value=booter))
        context = _make_context(umo=umo, role="member")

        allowed = await fs_tools.GrepTool().call(
            context, pattern="match", path=str(wt / "main.py")
        )
        assert "match-here" in allowed
        booter.fs.search_files.assert_awaited_once()

        blocked = await fs_tools.GrepTool().call(
            context, pattern="match", path=str(outside / "secret.txt")
        )
        assert blocked.startswith("Error:")
        assert "Read access is restricted" in blocked
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_execute_shell_blocked_in_readonly_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    from astrbot.core.tools import fs_access
    from astrbot.core.tools.computer_tools import shell as shell_tools

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.READONLY)
    try:
        context = _make_context(umo=umo)
        monkeypatch.setattr(shell_tools, "get_booter", AsyncMock())
        result = await shell_tools.ExecuteShellTool().call(context, command="echo hi")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "readonly" in result
    finally:
        fs_access.reset()


@pytest.mark.asyncio
async def test_shell_session_blocked_in_readonly_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    from astrbot.core.tools import fs_access
    from astrbot.core.tools.computer_tools import shell as shell_tools

    umo = "webchat:FriendMessage:webchat!tester!s1"
    fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.READONLY)
    try:
        context = _make_context(umo=umo)
        monkeypatch.setattr(shell_tools, "get_booter", AsyncMock())
        result = await shell_tools.ShellSessionTool().call(context, action="list")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "readonly" in result
    finally:
        fs_access.reset()


class TestChangedFilesRecording:
    """File tools must record touches into AstrAgentContext.extra.

    Backs the ChatUI end-of-turn file change summary: the agent runner
    aggregates these entries into the ``file_changes`` event.
    """

    def _isolate_history(self, monkeypatch, tmp_path):
        from astrbot.core.tools.computer_tools.edit_history import (
            EditHistoryManager,
        )

        manager = EditHistoryManager(base_dir=tmp_path / "history")
        monkeypatch.setattr(fs_tools, "get_history_manager", lambda: manager)
        return manager

    @pytest.mark.asyncio
    async def test_write_new_file_records_created(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
        self._isolate_history(monkeypatch, tmp_path)
        target = workspace / "brand_new.txt"
        ctx = _make_context()
        ctx.context.extra = {}

        result = await fs_tools.FileWriteTool().call(
            context=ctx, path=str(target), content="hello\nworld\n"
        )

        assert "File written successfully" in result
        entries = ctx.context.extra["changed_files"]
        assert len(entries) == 1
        assert entries[0]["kind"] == "created"
        assert entries[0]["backup_id"] == ""
        assert entries[0]["path"] == str(target)

    @pytest.mark.asyncio
    async def test_write_existing_file_saves_backup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
        manager = self._isolate_history(monkeypatch, tmp_path)
        target = workspace / "exists.txt"
        # Binary write keeps bytes platform-stable (no CRLF translation).
        target.write_bytes(b"old\n")
        ctx = _make_context()
        ctx.context.extra = {}

        await fs_tools.FileWriteTool().call(
            context=ctx, path=str(target), content="new\n"
        )

        entries = ctx.context.extra["changed_files"]
        assert entries[0]["kind"] == "write"
        assert entries[0]["backup_id"] != ""
        _, baseline = manager.read_backup(str(target), entries[0]["backup_id"])
        assert baseline == b"old\n"

    @pytest.mark.asyncio
    async def test_failed_write_records_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        _setup_local_fs_tools(monkeypatch, tmp_path)
        self._isolate_history(monkeypatch, tmp_path)
        tool = fs_tools.FileWriteTool()
        # Unique umo so global fs_access overrides from other tests
        # cannot leak READONLY/WORKSPACE state into this context.
        readonly_ctx = _make_context(
            umo="qq:friend:fc-readonly",
            file_access_default_mode="readonly",
        )
        readonly_ctx.context.extra = {}

        await tool.call(
            context=readonly_ctx,
            path=str(tmp_path / "nope.txt"),
            content="x",
        )

        assert readonly_ctx.context.extra.get("changed_files", []) == []

    @pytest.mark.asyncio
    async def test_edit_records_touch(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        workspace = _setup_local_fs_tools(monkeypatch, tmp_path)
        manager = self._isolate_history(monkeypatch, tmp_path)
        target = workspace / "edit_me.txt"
        # Binary write keeps bytes platform-stable (no CRLF translation).
        target.write_bytes(b"alpha\n")
        ctx = _make_context()
        ctx.context.extra = {}

        result = await fs_tools.FileEditTool().call(
            context=ctx, path=str(target), old="alpha", new="beta"
        )

        assert "Edited" in result
        entries = ctx.context.extra["changed_files"]
        assert entries[0]["kind"] == "edit"
        assert entries[0]["backup_id"] != ""
        _, baseline = manager.read_backup(str(target), entries[0]["backup_id"])
        assert baseline == b"alpha\n"
