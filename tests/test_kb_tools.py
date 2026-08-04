"""Three functions over a dict. The point of the design is that a bad path is a
missing key rather than a filesystem call, so that is what these prove."""
from jbcub_bot.core.kb_snapshot import Note, Snapshot, Source
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


def _sourced() -> Snapshot:
    return Snapshot(sha="abc123", repo="r", notes={
        "kb/p.md": Note(
            path="kb/p.md", text="Retakes are allowed once.\n",
            title="Grading",
            source=Source(file="sources/policies/bachelor_policies_v8.pdf",
                          document="Policies for Bachelor Studies",
                          version="8", sections=("III.4 Grading",),
                          pdf_pages="18-20")),
    })


def test_read_note_tells_the_agent_which_document_it_is_reading():
    body = tools.read_note(_sourced(), "kb/p.md")

    assert "Policies for Bachelor Studies" in body
    assert "III.4 Grading" in body
    assert "18-20" in body
    assert "Retakes are allowed once." in body


def test_a_note_without_a_source_gets_no_hint():
    body = tools.read_note(_snapshot(), "kb/policies/exams.md")

    assert body.startswith("---")


def test_a_long_note_is_clipped_with_a_visible_mark():
    huge = "x" * (tools.MAX_CHARS + 500)
    snapshot = Snapshot(sha="abc123", repo="r",
                        notes={"kb/big.md": Note(path="kb/big.md", text=huge)})

    body = tools.read_note(snapshot, "kb/big.md")

    assert len(body) < tools.MAX_CHARS + len(tools.TRUNCATION_MARK) + 1
    assert body.endswith(tools.TRUNCATION_MARK)


def test_a_folder_listing_survives_past_a_notes_clip_limit():
    """The prompt maps folders, so a listing is the only way to learn a note's
    name. Clipping one at MAX_CHARS would hide its last notes for good."""
    notes = {f"kb/big/note-{i:03d}.md": Note(path=f"kb/big/note-{i:03d}.md",
                                             text="body",
                                             description="d" * 400)
             for i in range(60)}  # ~25k chars of listing, over MAX_CHARS
    snapshot = Snapshot(sha="abc123", repo="r", notes=notes)

    listing = tools.list_notes(snapshot, "kb/big/")

    assert len(listing) > tools.MAX_CHARS
    assert "kb/big/note-059.md" in listing, "the tail is still there"
    assert not listing.endswith(tools.TRUNCATION_MARK)


# --- what a call came back with -----------------------------------------------

def test_a_search_that_found_nothing_summarises_as_no_hits():
    snapshot = _snapshot()

    assert tools.summarize_result(
        "search_notes", tools.search_notes(snapshot, "zzzz")) == "0 hits"
    assert tools.summarize_result(
        "list_notes", tools.list_notes(snapshot, "kb/nowhere/")) == "0 hits"


def test_a_search_that_found_something_counts_its_lines():
    snapshot = _snapshot()

    summary = tools.summarize_result("search_notes",
                                     tools.search_notes(snapshot, "retake"))

    assert summary == "2 hits"


def test_one_hit_is_singular():
    snapshot = _snapshot()

    summary = tools.summarize_result("search_notes",
                                     tools.search_notes(snapshot, "12 May"))

    assert summary == "1 hit"


def test_a_capped_search_says_there_were_more():
    snapshot = Snapshot(sha="s", repo="r", notes={
        "kb/n.md": Note(path="kb/n.md", text="hit\n" * 60)})

    summary = tools.summarize_result("search_notes",
                                     tools.search_notes(snapshot, "hit"))

    assert summary == f"{tools.MAX_MATCHES} hits+"


def test_a_note_read_reports_its_size():
    snapshot = _snapshot()

    summary = tools.summarize_result(
        "read_note", tools.read_note(snapshot, "kb/policies/exams.md"))

    assert summary.endswith(" chars")


def test_a_big_note_reports_thousands_and_says_it_was_clipped():
    huge = "x" * (tools.MAX_CHARS + 500)
    snapshot = Snapshot(sha="s", repo="r",
                        notes={"kb/big.md": Note(path="kb/big.md", text=huge)})

    summary = tools.summarize_result("read_note",
                                     tools.read_note(snapshot, "kb/big.md"))

    assert summary == "20.0k chars (clipped)"


def test_a_missing_note_says_so_rather_than_reporting_its_error_text_size():
    snapshot = _snapshot()

    summary = tools.summarize_result("read_note",
                                     tools.read_note(snapshot, "kb/ghost.md"))

    assert summary == "no such note"
