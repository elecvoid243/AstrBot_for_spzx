"""Per-umo private notes stored as virtual paths over a flat file store."""

import pytest

from astrbot.core.agent import notes_store


@pytest.fixture
def notes_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes_store, "get_astrbot_agent_notes_path", lambda: str(tmp_path)
    )
    return tmp_path


def test_validate_path_rejects_escapes(notes_root):
    for raw in ("../x.md", "/abs.md", "C:/x.md", "CON.md", "a/../../b.md", "   "):
        with pytest.raises(ValueError):
            notes_store.validate_path("umo:a", raw)


def test_validate_path_adds_the_default_suffix(notes_root):
    path = notes_store.validate_path("umo:a", "tasks/plan")

    assert path.name == "plan.md"


def test_write_then_read_round_trips(notes_root):
    notes_store.write_note("umo:a", "plan", "line one\nline two\n")

    note = notes_store.read_note("umo:a", "plan.md")

    assert note["path"] == "plan.md"
    assert note["total_lines"] == 2
    assert note["content"] == "line one\nline two"


def test_append_keeps_existing_content(notes_root):
    notes_store.write_note("umo:a", "log.md", "a\n")

    result = notes_store.write_note("umo:a", "log.md", "b\n", mode="append")

    assert result["bytes"] == 4
    assert notes_store.read_note("umo:a", "log.md")["content"] == "a\nb"


def test_single_file_limit_rejects_without_writing(notes_root):
    with pytest.raises(ValueError):
        notes_store.write_note(
            "umo:a", "big.md", "x" * (notes_store.MAX_FILE_BYTES + 1)
        )

    assert notes_store.list_notes("umo:a") == []


def test_total_limit_rejects_without_writing(notes_root, monkeypatch):
    monkeypatch.setattr(notes_store, "MAX_TOTAL_BYTES", 100)
    notes_store.write_note("umo:a", "a.md", "x" * 80)

    with pytest.raises(ValueError):
        notes_store.write_note("umo:a", "b.md", "x" * 40)

    assert [entry["path"] for entry in notes_store.list_notes("umo:a")] == ["a.md"]


def test_list_filters_by_prefix(notes_root):
    notes_store.write_note("umo:a", "tasks/one.md", "1")
    notes_store.write_note("umo:a", "tasks/two.md", "2")
    notes_store.write_note("umo:a", "root.md", "3")

    paths = {
        entry["path"] for entry in notes_store.list_notes("umo:a", prefix="tasks/")
    }

    assert paths == {"tasks/one.md", "tasks/two.md"}


def test_search_matches_case_insensitively_and_limits(notes_root):
    notes_store.write_note("umo:a", "a.md", "Alpha beta\nBETA gamma\nbeta delta")

    hits = notes_store.search_notes("umo:a", "beta", max_matches_per_file=2)

    assert len(hits) == 2
    assert hits[0]["line"] == 1
    assert "beta" in hits[0]["snippet"].lower()


def test_read_supports_line_ranges_and_negative_indices(notes_root):
    notes_store.write_note("umo:a", "n.md", "1\n2\n3\n4\n5")

    middle = notes_store.read_note("umo:a", "n.md", start_line=2, stop_line=4)
    tail = notes_store.read_note("umo:a", "n.md", start_line=-2)

    assert middle["content"] == "2\n3\n4"
    assert tail["content"] == "4\n5"


def test_notes_are_isolated_per_umo(notes_root):
    notes_store.write_note("umo:a", "plan.md", "secret")

    assert notes_store.read_note("umo:b", "plan.md") is None
    assert notes_store.list_notes("umo:b") == []
    assert notes_store.search_notes("umo:b", "secret") == []


def test_unknown_note_reads_as_none(notes_root):
    assert notes_store.read_note("umo:a", "missing.md") is None
