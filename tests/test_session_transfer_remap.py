"""ChatUI session export/import: ID and reference remapping.

Author: elecvoid243
Date: 2026-09-17
Spec: docs/superpowers/specs/2026-09-13-chatui-session-export-import-design.md
"""

import pytest

from astrbot.dashboard.services.session_transfer_service import (
    build_attachment_id_map,
    is_safe_attachment_ext,
    is_safe_attachment_zip_path,
    new_umo,
    resolve_parent_message_id,
    rewrite_attachment_refs,
)


class _FixedUuid:
    """Deterministic uuid4 stand-in: returns uuid-0001, uuid-0002, ..."""

    def __init__(self) -> None:
        self.counter = 0

    def __call__(self):
        self.counter += 1
        return f"uuid-{self.counter:04d}"


def test_new_umo_friend_message():
    assert (
        new_umo("webchat", 0, "bob", "sess-1")
        == "webchat:FriendMessage:webchat!bob!sess-1"
    )


def test_new_umo_group_message():
    assert (
        new_umo("webchat", 1, "bob", "sess-1")
        == "webchat:GroupMessage:webchat!bob!sess-1"
    )


def test_attachment_map_keeps_free_ids_and_reissues_taken_ones():
    mapping = build_attachment_id_map(
        ["a1", "a2"],
        existing_ids={"a2"},
        uuid_factory=_FixedUuid(),
    )
    assert mapping == {"a1": "a1", "a2": "uuid-0001"}


def test_attachment_map_ignores_blank_and_duplicate_ids():
    mapping = build_attachment_id_map(
        ["", None, "a1", "a1"],
        existing_ids=set(),
        uuid_factory=_FixedUuid(),
    )
    assert mapping == {"a1": "a1"}


def test_rewrite_attachment_refs_replaces_only_mapped_parts():
    content = {
        "type": "user",
        "message": [
            {"type": "plain", "text": "hi"},
            {"type": "image", "attachment_id": "a1", "filename": "x.png"},
            {"type": "file", "attachment_id": "keep", "filename": "y.zip"},
        ],
    }
    rewritten = rewrite_attachment_refs(content, {"a1": "new1"})
    assert rewritten["message"][0] == {"type": "plain", "text": "hi"}
    assert rewritten["message"][1]["attachment_id"] == "new1"
    assert rewritten["message"][1]["filename"] == "x.png"
    assert rewritten["message"][2]["attachment_id"] == "keep"
    # The caller's payload must not be mutated in place.
    assert content["message"][1]["attachment_id"] == "a1"


def test_rewrite_attachment_refs_tolerates_missing_parts():
    assert rewrite_attachment_refs({}, {"a1": "new1"}) == {}
    assert rewrite_attachment_refs({"message": "bad"}, {}) == {"message": "bad"}
    assert rewrite_attachment_refs(None, {}) is None


def test_resolve_parent_message_id():
    assert resolve_parent_message_id(7, {7: 99}) == 99
    assert resolve_parent_message_id(7, {}) is None


@pytest.mark.parametrize(
    "zip_path",
    [
        "files/attachments/a1.png",
        "files/attachments/nested/a1.png",
    ],
)
def test_safe_attachment_zip_path_accepts_expected_prefix(zip_path):
    assert is_safe_attachment_zip_path(zip_path) is True


@pytest.mark.parametrize(
    "zip_path",
    [
        "",
        "export.json",
        "../files/attachments/a1.png",
        "files/attachments/../../etc/passwd",
        "files\\attachments\\a1.png",
        "/files/attachments/a1.png",
        "files/attachments/./a1.png",
    ],
)
def test_safe_attachment_zip_path_rejects_traversal(zip_path):
    assert is_safe_attachment_zip_path(zip_path) is False


@pytest.mark.parametrize("ext", [".png", ".jpeg", ".mp3", ".bin"])
def test_safe_attachment_ext_accepts(ext):
    assert is_safe_attachment_ext(ext) is True


@pytest.mark.parametrize("ext", ["", "png", ".", ".p/g", ".png.exe/" + "x" * 20])
def test_safe_attachment_ext_rejects(ext):
    assert is_safe_attachment_ext(ext) is False
