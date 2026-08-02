"""What the reader actually sees, and why it cannot break the message.

Telegram rejects a whole message over one stray tag, so escaping and balancing
are the load-bearing parts here. Everything else is layout.
"""
from dataclasses import dataclass, field

from jbcub_bot.core.kb_snapshot import Note, Snapshot, Source
from jbcub_bot.features.kb import render


@dataclass(frozen=True)
class FakeCall:
    name: str = "read_note"
    args: dict = field(default_factory=dict)
    result: str = ""


@dataclass(frozen=True)
class FakeStats:
    steps: int = 3
    tool_calls: int = 4
    notes_read: int = 2
    input_tokens: int = 1200
    output_tokens: int = 310
    calls: tuple = ()


def _snapshot() -> Snapshot:
    return Snapshot(sha="abc123", repo="xoposhiy/cub-kb", notes={
        "kb/p.md": Note(
            path="kb/p.md", text="body", title="Grading",
            source=Source(file="sources/policies/bachelor_policies_v8.pdf",
                          document="Policies for Bachelor Studies",
                          version="8", sections=("III.4 Grading",),
                          pdf_pages="18-20")),
        "kb/one.md": Note(
            path="kb/one.md", text="body", title="One pager",
            source=Source(file="sources/sdt-handbook/2026-SDT-BSc.pdf",
                          document="Program Handbook", version="V 1.0",
                          sections=("2.1 General",), pdf_pages="12")),
        "kb/c.md": Note(
            path="kb/c.md", text="body", title="Spring",
            source=Source(file="sources/academic-calendars/2025-2026.html",
                          document="Academic Calendar 2025/2026",
                          sections=("Spring Semester 2026",),
                          url="https://constructor.university/ac/2025-2026")),
        "kb/bare.md": Note(path="kb/bare.md", text="body"),
    })


# --- escaping and balancing ---------------------------------------------------

def test_markup_the_model_is_allowed_survives():
    assert render.escape_subset("<b>bold</b> and <i>it</i>") == \
        "<b>bold</b> and <i>it</i>"


def test_everything_else_is_inert():
    out = render.escape_subset("<script>alert(1)</script> a & b")

    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out


def test_a_tag_with_attributes_is_not_restored():
    out = render.escape_subset('<a href="http://x">x</a>')

    assert "<a href" not in out


def test_a_quote_full_of_markdown_punctuation_is_untouched():
    body = "the _rule_ is *45%* and #4 applies"

    assert render.escape_subset(body) == body


def test_an_unclosed_tag_is_closed():
    assert render.balance("<b>bold") == "<b>bold</b>"


def test_a_stray_closing_tag_is_dropped():
    assert render.balance("plain</i> text") == "plain text"


def test_crossed_tags_are_closed_in_order():
    assert render.balance("<b><i>x</b>") == "<b><i>x</i></b>"


def test_plain_strips_tags_and_entities():
    assert render.plain("<b>a</b> &amp; b") == "a & b"


# --- the sources line the model writes ----------------------------------------

def test_the_sources_line_is_taken_off_the_body():
    body, paths = render.split_sources(
        "Retakes are allowed once.\n\nSources: kb/p.md")

    assert paths == ["kb/p.md"]
    assert "Sources" not in body
    assert body == "Retakes are allowed once."


def test_several_paths_on_the_sources_line():
    _, paths = render.split_sources("x\nSources: kb/p.md, kb/c.md")

    assert paths == ["kb/p.md", "kb/c.md"]


def test_a_path_repeated_is_listed_once():
    _, paths = render.split_sources("x\nSources: kb/p.md, kb/p.md")

    assert paths == ["kb/p.md"]


def test_a_model_that_ignores_the_instruction_still_gets_cited():
    body, paths = render.split_sources("Retakes (kb/p.md) are allowed once.")

    assert paths == ["kb/p.md"]
    assert "kb/p.md" not in body
    assert body == "Retakes are allowed once."


def test_an_answer_with_no_paths_keeps_its_body():
    body, paths = render.split_sources("The base does not cover this.")

    assert paths == []
    assert body == "The base does not cover this."


# --- the sources block --------------------------------------------------------

def _block(*paths, snapshot=None) -> str:
    text, _ = render.sources_block(list(paths), snapshot or _snapshot())
    return text


def test_a_pdf_note_renders_document_version_section_and_pages():
    block, pdfs = render.sources_block(["kb/p.md"], _snapshot())

    assert "📄 Policies for Bachelor Studies v8" in block
    assert "§III.4 Grading — pp. 18–20" in block
    assert pdfs[0].file == "sources/policies/bachelor_policies_v8.pdf"


def test_a_single_page_is_not_plural():
    block = _block("kb/one.md")

    assert "p. 12" in block
    assert "pp." not in block


def test_a_web_note_links_and_attaches_nothing():
    block, pdfs = render.sources_block(["kb/c.md"], _snapshot())

    assert "🌐 Academic Calendar 2025/2026" in block
    assert "§Spring Semester 2026" in block
    assert "https://constructor.university/ac/2025-2026" in block
    assert pdfs == ()


def test_a_note_with_no_source_is_dropped_rather_than_shown_as_a_path():
    """The reader has never heard of the knowledge base. A citation the bot
    cannot resolve to a real document is worth less than the confusion of
    printing a repository path."""
    block = _block("kb/bare.md")

    assert block == ""


def test_a_path_that_is_not_in_the_snapshot_leaks_nothing():
    block = _block("kb/ghost.md")

    assert block == ""
    assert "ghost" not in block


# --- grouping by document -----------------------------------------------------

def _with_sections(snapshot, *specs):
    """Add notes off one PDF, each `(path, section, pages)`."""
    base = snapshot.notes["kb/p.md"].source
    for path, section, pages in specs:
        snapshot.notes[path] = Note(
            path=path, text="b",
            source=Source(file=base.file, document=base.document,
                          version=base.version, sections=(section,),
                          pdf_pages=pages))
    return snapshot


def test_one_document_is_named_once_with_its_sections_under_it():
    snapshot = _with_sections(_snapshot(),
                              ("kb/a.md", "III.4 Grading", "18-20"),
                              ("kb/b.md", "V.2 Thesis", "31"))

    block = _block("kb/a.md", "kb/b.md", snapshot=snapshot)

    assert block.count("Policies for Bachelor Studies") == 1
    assert block.splitlines() == [
        "📄 Policies for Bachelor Studies v8",
        "   §III.4 Grading — pp. 18–20",
        "   §V.2 Thesis — p. 31",
    ]


def test_two_notes_off_one_section_merge_their_pages():
    snapshot = _with_sections(_snapshot(),
                              ("kb/a.md", "III.4 Grading", "18-20"),
                              ("kb/b.md", "III.4 Grading", "31"))

    block = _block("kb/a.md", "kb/b.md", snapshot=snapshot)

    assert "§III.4 Grading — pp. 18–20, 31" in block
    assert len(block.splitlines()) == 2


def test_ten_notes_off_one_section_print_it_once():
    snapshot = _with_sections(_snapshot(), *[
        (f"kb/s{i}.md", "III.4 Grading", "18-20") for i in range(10)])

    block = _block(*(f"kb/s{i}.md" for i in range(10)), snapshot=snapshot)

    assert block.count("III.4 Grading") == 1


def test_two_documents_each_keep_their_own_heading():
    block = _block("kb/p.md", "kb/one.md", "kb/c.md")

    assert block.count("📄") == 2
    assert block.count("🌐") == 1


def test_a_document_with_too_many_sections_says_how_many_it_hid():
    snapshot = _with_sections(_snapshot(), *[
        (f"kb/s{i}.md", f"Section {i}", str(i)) for i in range(7)])

    block = _block(*(f"kb/s{i}.md" for i in range(7)), snapshot=snapshot)

    assert block.count("   §") == render.MAX_SECTIONS
    assert "… and 3 more sections" in block


def test_one_hidden_section_is_counted_in_the_singular():
    snapshot = _with_sections(_snapshot(), *[
        (f"kb/s{i}.md", f"Section {i}", str(i)) for i in range(5)])

    block = _block(*(f"kb/s{i}.md" for i in range(5)), snapshot=snapshot)

    assert "… and 1 more section" in block
    assert "more sections" not in block


def test_a_note_whose_frontmatter_names_no_section_still_shows_its_pages():
    snapshot = _snapshot()
    snapshot.notes["kb/n.md"] = Note(
        path="kb/n.md", text="b",
        source=Source(file="sources/policies/bachelor_policies_v8.pdf",
                      document="Policies for Bachelor Studies", pdf_pages="9"))

    block = _block("kb/n.md", snapshot=snapshot)

    assert block.splitlines() == ["📄 Policies for Bachelor Studies",
                                  "   p. 9"]


# --- the metrics line ---------------------------------------------------------

def test_the_metrics_line_reports_all_five_numbers():
    line = render.metrics_line(FakeStats())

    assert "3 steps" in line
    assert "4 tool calls" in line
    assert "2 notes read" in line
    assert "1.2k in" in line
    assert "310 out" in line


def test_one_of_something_is_singular():
    line = render.metrics_line(FakeStats(steps=1, tool_calls=1, notes_read=1))

    assert "1 step ·" in line
    assert "1 tool call ·" in line
    assert "1 note read ·" in line


def test_reading_nothing_says_so_rather_than_looking_broken():
    """A bare "0 notes" reads as a fault; it is a legitimate outcome."""
    line = render.metrics_line(FakeStats(notes_read=0))

    assert "0 notes read" in line


# --- the whole message --------------------------------------------------------

def test_the_message_has_the_answer_then_its_sources_and_no_metrics():
    out = render.render("Retakes are allowed once.\nSources: kb/p.md",
                        _snapshot())

    assert out.html.index("Retakes are allowed once.") < \
        out.html.index("Policies for Bachelor Studies")
    assert "steps" not in out.html, "cost belongs in the admin trace"


def test_the_cited_pdf_comes_back_for_attaching():
    out = render.render("x\nSources: kb/p.md", _snapshot())

    assert [p.file for p in out.pdfs] == [
        "sources/policies/bachelor_policies_v8.pdf"]
    assert "Policies for Bachelor Studies" in out.pdfs[0].caption


def test_two_notes_from_one_pdf_attach_it_once():
    snapshot = _snapshot()
    same = snapshot.notes["kb/p.md"]
    snapshot.notes["kb/p2.md"] = Note(path="kb/p2.md", text="b",
                                      source=same.source)

    out = render.render("x\nSources: kb/p.md, kb/p2.md", snapshot)

    assert len(out.pdfs) == 1


def test_a_web_only_answer_attaches_nothing():
    out = render.render("x\nSources: kb/c.md", _snapshot())

    assert out.pdfs == ()


def test_an_answer_citing_nothing_gets_no_sources_block():
    out = render.render("The base does not cover this.", _snapshot())

    assert out.html == "The base does not cover this."


def test_a_broad_answer_does_not_drown_in_its_own_sources():
    snapshot = _snapshot()
    for i in range(12):
        snapshot.notes[f"kb/n{i}.md"] = Note(
            path=f"kb/n{i}.md", text="b",
            source=Source(file=f"sources/d{i}.pdf", document=f"Document {i}"))
    cited = ", ".join(f"kb/n{i}.md" for i in range(12))

    out = render.render(f"A list.\nSources: {cited}", snapshot)

    shown = [ln for ln in out.html.splitlines() if ln.startswith("📄")]
    assert len(shown) == render.MAX_DOCUMENTS
    assert "… and 8 more documents" in out.html


def test_one_document_over_the_cap_is_counted_in_the_singular():
    snapshot = _snapshot()
    for i in range(5):
        snapshot.notes[f"kb/n{i}.md"] = Note(
            path=f"kb/n{i}.md", text="b",
            source=Source(file=f"sources/d{i}.pdf", document=f"Document {i}"))
    cited = ", ".join(f"kb/n{i}.md" for i in range(5))

    out = render.render(f"A list.\nSources: {cited}", snapshot)

    assert "… and 1 more document" in out.html
    assert "more documents" not in out.html


def test_an_over_long_answer_is_clipped_to_what_telegram_accepts():
    out = render.render("x" * 6000, _snapshot())

    assert len(out.html) <= render.CLIP_LIMIT
    assert render.TRUNCATION_MARK in out.html


def test_the_rendered_message_is_balanced_html():
    out = render.render("<b>Retakes\nSources: kb/p.md", _snapshot())

    assert out.html.count("<b>") == out.html.count("</b>")


# --- the base never shows through ---------------------------------------------

def test_no_rendered_message_mentions_the_knowledge_base():
    """The whole point: for the reader only the source documents exist."""
    answer = ("Assembled from the notes in kb/policies/bachelor-studies-v8/ "
              "and kb/calendars/2025-2026/.\nSources: kb/p.md")

    out = render.render(answer, _snapshot())

    assert "kb/" not in out.html


def test_a_bare_folder_is_swept_from_the_prose_without_becoming_a_source():
    body, paths = render.split_sources("Gathered from kb/calendars/2025-2026/.")

    assert paths == []
    assert "kb/" not in body


def test_an_index_note_named_inline_is_still_a_citation():
    body, paths = render.split_sources("See kb/policies/_index.md for the list.")

    assert paths == ["kb/policies/_index.md"]
    assert "kb/" not in body


# --- the trace ----------------------------------------------------------------

def _stats(*calls) -> FakeStats:
    return FakeStats(calls=tuple(calls))


def test_the_trace_names_every_tool_and_what_it_returned():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "supervisor"}, "0 hits"),
        FakeCall("read_note", {"path": "kb/p.md"}, "8.1k chars"),
    ))

    assert '🔎 search_notes "supervisor" — 0 hits' in text
    assert "📖 read_note kb/p.md — 8.1k chars" in text


def test_the_trace_shows_a_search_prefix_and_defaults_a_listing_to_the_root():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "thesis",
                                  "path_prefix": "kb/handbooks/"}, "14 hits"),
        FakeCall("list_notes", {}, "31 hits"),
    ))

    assert '"thesis" in kb/handbooks/' in text
    assert "📑 list_notes kb/ — 31 hits" in text


def test_the_trace_ends_with_the_totals():
    text = render.trace_message(_stats(FakeCall("list_notes", {}, "3 hits")))

    assert text.splitlines()[-1] == render.metrics_line(FakeStats())
    assert render.RULE in text


def test_a_run_that_called_nothing_says_so_rather_than_showing_a_bare_rule():
    text = render.trace_message(FakeStats())

    assert "no tool calls" in text


def test_a_long_search_pattern_is_cut_from_the_right():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "supervisor " * 20}, "0 hits")))

    assert "…" in text
    assert text.startswith('🔎 search_notes "supervisor supervisor')


def test_a_long_note_path_keeps_its_filename():
    path = "kb/handbooks/" + "nested/" * 8 + "07-thesis.md"

    text = render.trace_message(_stats(
        FakeCall("read_note", {"path": path}, "2.0k chars")))

    assert "07-thesis.md" in text
    assert "…" in text


def test_a_call_whose_output_never_arrived_still_appears():
    """A cut-short run leaves its last call unpaired; the call itself is the
    thing worth seeing."""
    text = render.trace_message(_stats(FakeCall("read_note", {"path": "kb/p.md"})))

    assert "📖 read_note kb/p.md" in text
    assert "—" not in text.splitlines()[0]


def test_the_trace_survives_a_pattern_full_of_angle_brackets():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "<b>&x"}, "0 hits")))

    assert "<b>&x" in text, "plain text, so nothing needs escaping"


def test_a_runaway_trace_keeps_its_totals():
    calls = [FakeCall("read_note", {"path": f"kb/n{i}.md"}, "1.0k chars")
             for i in range(40)]

    text = render.trace_message(_stats(*calls))

    assert len(text.splitlines()) == render.MAX_TRACE_CALLS + 3
    assert "… and 20 more calls" in text
    assert text.endswith(render.metrics_line(FakeStats()))
