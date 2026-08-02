"""Three functions over a dict. The point of the design is that a bad path is a
missing key rather than a filesystem call, so that is what these prove."""
from jbcub_bot.core.kb_snapshot import Note, Snapshot
from jbcub_bot.features.kb import tools


def _snapshot() -> Snapshot:
    notes = {
        "kb/policies/exams.md": Note(
            path="kb/policies/exams.md",
            text="---\ntitle: Exam rules\n---\n\nRetakes are allowed once.\n",
            title="Exam rules", description="How retakes work."),
        "kb/calendars/2026/spring.md": Note(
            path="kb/calendars/2026/spring.md",
            text="Session starts on 12 May.\nRetakes on 20 May.\n",
            title="Spring 2026"),
    }
    return Snapshot(sha="abc123", repo="xoposhiy/cub-kb", notes=notes)


def test_list_notes_on_an_empty_prefix_lists_the_whole_base():
    listing = tools.list_notes(_snapshot())

    assert "kb/policies/exams.md" in listing
    assert "kb/calendars/2026/spring.md" in listing
    assert "How retakes work." in listing


def test_list_notes_respects_the_prefix():
    listing = tools.list_notes(_snapshot(), "kb/calendars/")

    assert "kb/calendars/2026/spring.md" in listing
    assert "kb/policies/exams.md" not in listing


def test_read_note_returns_the_whole_note():
    assert "Retakes are allowed once." in tools.read_note(
        _snapshot(), "kb/policies/exams.md")


def test_read_note_on_an_unknown_path_answers_instead_of_raising():
    answer = tools.read_note(_snapshot(), "kb/nope.md")

    assert "kb/nope.md" in answer
    assert "list_notes" in answer


def test_a_traversal_path_is_merely_unknown():
    for path in ("../../.env", "/etc/passwd", "kb/../../secrets.md"):
        assert "list_notes" in tools.read_note(_snapshot(), path)


def test_search_notes_reports_path_and_line():
    hits = tools.search_notes(_snapshot(), "Retakes")

    assert "kb/policies/exams.md:5" in hits
    assert "kb/calendars/2026/spring.md:2" in hits


def test_search_notes_respects_the_prefix():
    hits = tools.search_notes(_snapshot(), "Retakes", "kb/calendars/")

    assert "kb/policies/exams.md" not in hits


def test_search_notes_answers_an_invalid_regex():
    answer = tools.search_notes(_snapshot(), "exam(")

    assert "not a valid" in answer.lower()


def test_search_notes_caps_its_matches_and_says_so():
    many = "match\n" * (tools.MAX_MATCHES + 20)
    snapshot = Snapshot(sha="abc123", repo="r",
                        notes={"kb/big.md": Note(path="kb/big.md", text=many)})

    hits = tools.search_notes(snapshot, "match")

    assert hits.count("kb/big.md:") == tools.MAX_MATCHES
    assert tools.TRUNCATION_MARK in hits


def test_a_long_note_is_clipped_with_a_visible_mark():
    huge = "x" * (tools.MAX_CHARS + 500)
    snapshot = Snapshot(sha="abc123", repo="r",
                        notes={"kb/big.md": Note(path="kb/big.md", text=huge)})

    body = tools.read_note(snapshot, "kb/big.md")

    assert len(body) < tools.MAX_CHARS + len(tools.TRUNCATION_MARK) + 1
    assert body.endswith(tools.TRUNCATION_MARK)
