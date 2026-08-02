"""What the reader actually sees, and why it cannot break the message.

Telegram rejects a whole message over one stray tag, so escaping and balancing
are the load-bearing parts here. Everything else is layout.
"""
from dataclasses import dataclass

from jbcub_bot.core.kb_snapshot import Note, Snapshot, Source
from jbcub_bot.features.kb import render


@dataclass(frozen=True)
class FakeStats:
    steps: int = 3
    tool_calls: int = 4
    notes_read: int = 2
    input_tokens: int = 1200
    output_tokens: int = 310


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

def test_a_pdf_note_renders_document_version_section_and_pages():
    line, pdf = render.source_line("kb/p.md", _snapshot().notes["kb/p.md"])

    assert "Policies for Bachelor Studies v8" in line
    assert "§III.4 Grading" in line
    assert "pp. 18–20" in line
    assert pdf.file == "sources/policies/bachelor_policies_v8.pdf"


def test_a_single_page_is_not_plural():
    line, _ = render.source_line("kb/one.md", _snapshot().notes["kb/one.md"])

    assert "p. 12" in line
    assert "pp." not in line


def test_a_web_note_links_and_attaches_nothing():
    line, pdf = render.source_line("kb/c.md", _snapshot().notes["kb/c.md"])

    assert "https://constructor.university/ac/2025-2026" in line
    assert pdf is None


def test_a_note_with_no_source_falls_back_to_its_path():
    line, pdf = render.source_line("kb/bare.md", _snapshot().notes["kb/bare.md"])

    assert "kb/bare.md" in line
    assert pdf is None


def test_a_path_that_is_not_in_the_snapshot_still_renders():
    line, pdf = render.source_line("kb/ghost.md", None)

    assert "kb/ghost.md" in line
    assert pdf is None


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

def test_the_message_has_answer_then_sources_then_metrics():
    out = render.render("Retakes are allowed once.\nSources: kb/p.md",
                        _snapshot(), FakeStats())

    answer_at = out.html.index("Retakes are allowed once.")
    source_at = out.html.index("Policies for Bachelor Studies")
    metrics_at = out.html.index("3 steps")
    assert answer_at < source_at < metrics_at


def test_the_cited_pdf_comes_back_for_attaching():
    out = render.render("x\nSources: kb/p.md", _snapshot(), FakeStats())

    assert [p.file for p in out.pdfs] == [
        "sources/policies/bachelor_policies_v8.pdf"]
    assert "Policies for Bachelor Studies" in out.pdfs[0].caption


def test_two_notes_from_one_pdf_attach_it_once():
    snapshot = _snapshot()
    same = snapshot.notes["kb/p.md"]
    snapshot.notes["kb/p2.md"] = Note(path="kb/p2.md", text="b",
                                      source=same.source)

    out = render.render("x\nSources: kb/p.md, kb/p2.md", snapshot, FakeStats())

    assert len(out.pdfs) == 1


def test_a_web_only_answer_attaches_nothing():
    out = render.render("x\nSources: kb/c.md", _snapshot(), FakeStats())

    assert out.pdfs == ()


def test_an_answer_citing_nothing_gets_no_sources_block_but_keeps_metrics():
    out = render.render("The base does not cover this.", _snapshot(),
                        FakeStats())

    assert "📄" not in out.html
    assert "🌐" not in out.html
    assert "3 steps" in out.html


def test_a_broad_answer_does_not_drown_in_its_own_sources():
    snapshot = _snapshot()
    for i in range(12):
        snapshot.notes[f"kb/n{i}.md"] = Note(path=f"kb/n{i}.md", text="b")
    cited = ", ".join(f"kb/n{i}.md" for i in range(12))

    out = render.render(f"A list.\nSources: {cited}", snapshot, FakeStats())

    shown = [ln for ln in out.html.splitlines() if ln.startswith("📄")]
    assert len(shown) == render.MAX_SOURCE_LINES
    assert "… and 7 more sources" in out.html


def test_one_source_over_the_cap_is_counted_in_the_singular():
    snapshot = _snapshot()
    for i in range(6):
        snapshot.notes[f"kb/n{i}.md"] = Note(path=f"kb/n{i}.md", text="b")
    cited = ", ".join(f"kb/n{i}.md" for i in range(6))

    out = render.render(f"A list.\nSources: {cited}", snapshot, FakeStats())

    assert "… and 1 more source" in out.html
    assert "more sources" not in out.html


def test_notes_cut_from_one_chapter_cite_that_chapter_once():
    """Ten notes off one section share a page range; printing it ten times is
    noise, not provenance."""
    snapshot = _snapshot()
    shared = snapshot.notes["kb/p.md"].source
    for i in range(9):
        snapshot.notes[f"kb/s{i}.md"] = Note(path=f"kb/s{i}.md", text="b",
                                             source=shared)
    cited = ", ".join(f"kb/s{i}.md" for i in range(9))

    out = render.render(f"A list.\nSources: {cited}", snapshot, FakeStats())

    assert out.html.count("Policies for Bachelor Studies") == 1
    assert "more source" not in out.html, "one line is under the cap"


def test_an_over_long_answer_is_clipped_to_what_telegram_accepts():
    out = render.render("x" * 6000, _snapshot(), FakeStats())

    assert len(out.html) <= render.CLIP_LIMIT
    assert render.TRUNCATION_MARK in out.html


def test_the_rendered_message_is_balanced_html():
    out = render.render("<b>Retakes\nSources: kb/p.md", _snapshot(),
                        FakeStats())

    assert out.html.count("<b>") == out.html.count("</b>")
