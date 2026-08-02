# Knowledge base answers: shape, formatting, provenance and cost — design

**Status:** approved 2026-08-02.
**Amends:** `docs/superpowers/specs/2026-07-31-knowledge-base-agent-design.md`,
as built by `docs/superpowers/plans/2026-08-02-knowledge-base-agent.md`.

## Goal

An answer should be short, look like Telegram rather than like a raw Markdown
file, and carry the evidence for itself: a verbatim quote plus the means to find
that quote in the document it came from. It should also say what it cost to
produce.

## What is wrong today

1. Answers are long. Nothing in the prompt asks for brevity.
2. The agent writes Markdown; the message is sent with no `parse_mode`, so the
   reader sees `**bold**` and `##` as literal characters.
3. Citations point at GitHub blob URLs of the knowledge base. Staff do not want
   the notes — they want the policy document, at the page the rule is on.
4. Nothing reports what a question cost.

## The fact that makes this cheap

Every note in `cub-kb` already carries its provenance in frontmatter, written by
that repository's own tooling. Three shapes exist:

```yaml
# policy-note and handbook-note
source:
  file: sources/policies/bachelor_policies_v8.pdf
  document: "Policies for Bachelor Studies"
  version: "8"
  valid_from: 2025-09-01
  sections: ["III.4 Grading, Passing and Failing of Modules"]
  pdf_pages: "18-20"

# calendar-note — a web page, no PDF and no page numbers
source:
  file: sources/academic-calendars/2025-2026.html
  url: https://constructor.university/student-life/academic-calendars/2025-2026
  retrieved: 2026-07-31
  document: "Academic Calendar 2025/2026"
  sections: ["Academic Calendar – Degree Programs", "Spring Semester 2026"]
```

No PDF is parsed and no page number is computed. The knowledge base is the
index into the source documents, and the bot only has to read what is already
there.

There are exactly two PDFs — `sources/policies/bachelor_policies_v8.pdf`
(314 KB) and `sources/sdt-handbook/2026-SDT-BSc.pdf` (3.1 MB) — and two HTML
calendar sources.

**Page numbers and section numbers are never written by the model.** The model
names a note path; the bot looks the provenance up. A model that invents
"p. 47" is the failure this rule exists to prevent.

## Architecture

| File | Change |
| --- | --- |
| `core/kb_snapshot.py` | `Source` dataclass; `Note.source`; frontmatter parsed with PyYAML |
| `features/kb/render.py` (create) | Escaping, the HTML subset, the sources block, the metrics line, the plain-text fallback |
| `features/kb/agent.py` | Prompt rules; `AskStats`; `ask()` returns stats; `render_answer` and `_NOTE_REF` deleted |
| `features/kb/pdf.py` (create) | `file_id` cache, "already sent this session" bookkeeping |
| `features/kb/handlers.py` | Sends HTML with a fallback, attaches PDFs, threads `sent_pdfs` through the FSM |
| `features/kb/tools.py` | `read_note` reports the note's source line so the agent can see it |

### Frontmatter: PyYAML, reversing an earlier decision

The original module argued that "a two-line regex beats adding a YAML parser to
the image". That held for two flat string keys. It does not hold for a nested
map containing an inline list, and hand-rolling that is exactly the kind of
parser that works until a note quotes a colon. `uv add pyyaml`; parse the
frontmatter block properly.

`parse_frontmatter(text) -> tuple[dict, str]` returns the parsed mapping and the
body below it; `Source.from_mapping(meta.get("source"))` turns the `source` key
into a `Source`, returning `None` when the key is absent or is not a mapping. A
note whose frontmatter is absent or unparseable keeps working with `source=None`
— the knowledge base is edited by people, and one malformed note must not empty
the snapshot.

```python
@dataclass(frozen=True)
class Source:
    file: str = ""          # sources/policies/bachelor_policies_v8.pdf
    document: str = ""      # "Policies for Bachelor Studies"
    version: str = ""
    sections: tuple[str, ...] = ()
    pdf_pages: str = ""     # "18-20"; empty for a web source
    url: str = ""           # set for a web source, empty for a PDF

    @property
    def is_pdf(self) -> bool:
        return self.file.lower().endswith(".pdf")
```

`Note` gains `source: Source | None`.

## The prompt

Three rules are added to `SYSTEM_RULES`, and one existing rule changes.

- **Brevity.** "Answer in at most three sentences, then stop. Follow the answer
  with one short verbatim quote from the note that proves it. Do not write an
  overview, a preamble, or a list of everything you found."
- **Formatting.** "Write for Telegram, not Markdown. The only markup allowed is
  `<b>`, `<i>`, `<code>` and `<blockquote>`. Never write `#`, `*`, `_` or `-`
  as markup, and never write a link."
- **Citation.** The existing "every claim carries the path of the note" rule
  keeps its path requirement and gains: "Name the note path and nothing else —
  never a page number, a section number or a document title. Those are filled in
  for you from the note's own metadata."

`MAX_OUTPUT_TOKENS` stays at 1024: the cap is a runaway guard, and brevity is
the prompt's job. A three-sentence answer that gets truncated mid-word is worse
than one that is occasionally four sentences.

`read_note` prepends one line naming the note's document and section, so the
agent can tell a policy note from a calendar note without guessing from the path.

## Rendering

`features/kb/render.py` owns everything between the model's string and the
bytes Telegram receives.

### The HTML subset, safely

1. `html.escape(text, quote=False)` over the whole answer — `&`, `<`, `>` gone.
2. Restore only the allowed tags: `<b> </b> <i> </i> <code> </code>
   <blockquote> </blockquote>`, matched case-insensitively against the escaped
   forms. Attributes are not restored, so `<a href=…>` and
   `<blockquote expandable>` stay escaped and render as visible text rather than
   as markup.
3. Drop an unpaired closing tag and close an unclosed opening tag at the end.
   Telegram rejects the whole message over one stray `</b>`.

`<a>` is deliberately not in the allowlist: the only links in an answer are the
ones the bot builds itself in the sources block, from frontmatter.

### Layout

```
<answer, ≤3 sentences>

<blockquote>the verbatim quote</blockquote>

📄 Policies for Bachelor Studies v8 · §III.4 · pp. 18–20
🌐 Academic Calendar 2025/2026 · Spring Semester 2026
   https://constructor.university/student-life/academic-calendars/2025-2026
─────────────
3 steps · 4 tool calls · 2 notes · 1.2k in / 310 out
```

- One sources line per distinct cited note, in the order first cited.
- A PDF-backed note renders `📄 <document> v<version> · §<sections> · pp. <pages>`;
  a `pdf_pages` of `"18"` renders `p. 18`, `"18-20"` renders `pp. 18–20`.
- A web-backed note renders `🌐 <document> · <sections>` and the URL on its own
  line. **The URL is repeated on every answer that cites it** — it costs one
  line and saves a scroll.
- A cited note with `source=None` falls back to the note path.
- An answer citing nothing gets no sources block, exactly as today.
- The whole message is clipped to Telegram's 4096-character limit with a visible
  mark, counted after escaping.

### The metrics line

`3 steps · 4 tool calls · 2 notes · 1.2k in / 310 out`, in that order, after a
`─` rule. Token counts over 1000 render as `1.2k`. "notes" counts distinct paths
passed to `read_note`, so re-reading one note twice still reads one note.

### The fallback

`handlers.py` sends with `parse_mode="HTML"`. On `TelegramBadRequest` it sends
the same content again with every tag stripped and no `parse_mode`. A malformed
answer degrades to plain text; it never vanishes and never reaches `dp.errors`.
Any other exception is left alone to reach `dp.errors`, per the feature's
existing rule.

## Delivering the PDF

A PDF is attached **once per session**, on the first answer that cites a note
backed by it. Later answers in the same session cite pages only. The two PDFs
are independent: a session that touches both gets both, once each.

- Telegram fetches the file from
  `https://raw.githubusercontent.com/<repo>/<sha>/<source.file>`, pinned to the
  snapshot `sha`, so the attachment matches the pages cited. The cache is keyed
  by that whole URL, so a moved `sha` re-uploads rather than serving a stale
  file under a fresh page number.
- The `file_id` Telegram returns is cached process-wide in
  `dict[url, file_id]`, so the upload happens once per deploy rather than once
  per session. A redeploy loses the cache and re-uploads — not worth a database
  table.
- Which PDFs this session already received lives in the FSM data as
  `sent_pdfs: list[str]`, alongside `asked` and `history`.
- The document is sent **after** the answer, with the document title as its
  caption.
- A failed attachment is not a failed answer: the answer has already been sent,
  and the sources line names the document and pages regardless. The failure is
  logged and swallowed.

Web sources are never attached — only linked, every time.

## Metrics

`ask()` returns `(answer, history, stats)`.

```python
@dataclass(frozen=True)
class AskStats:
    steps: int = 0          # usage.requests — model turns
    tool_calls: int = 0
    notes_read: int = 0     # distinct read_note paths
    input_tokens: int = 0
    output_tokens: int = 0
```

Read from `result.context_wrapper.usage` and `result.new_items` filtered to
`ToolCallItem`. On the `MaxTurnsExceeded` path the same figures come from
`exc.run_data`, which carries `new_items` and `context_wrapper` — guarded,
since it is typed `| None`. A cut-short answer therefore still reports what it
burned, which is the case where the number matters most.

No money. Prices are a per-deployment fact the bot has no honest way to know,
and the token counts are one multiplication away from a cost whenever that is
wanted.

## Testing

- **Frontmatter** — all three source shapes parse; a note with no frontmatter,
  and a note with malformed YAML, both survive with `source=None`.
- **Escaping** — `<script>` renders inert; `&` survives; a quote containing `_`
  and `*` passes through untouched; an unclosed `<b>` is closed; a stray `</i>`
  is dropped.
- **Layout** — a PDF cite renders document, version, section and pages from
  frontmatter; a one-page `pdf_pages` renders `p.` not `pp.`; a web cite renders
  the URL; a `source=None` cite falls back to the path; no cite renders no
  block; an over-long message is clipped.
- **Fallback** — a `TelegramBadRequest` on the HTML send produces a second,
  tag-free send with the same words; any other exception propagates.
- **PDF delivery** — attached on first cite; not attached on the second cite in
  the same session; attached again in a fresh session; a second distinct PDF is
  attached alongside; a calendar cite attaches nothing; a failing attachment
  leaves the answer sent.
- **Metrics** — stats extracted from a stubbed run; distinct-path counting;
  stats still reported when the turn budget is exhausted.

Existing `test_kb_agent.py` tests for `render_answer` are rewritten against
`render.py`; the GitHub-blob-URL assertions are deleted.

## Out of scope

- Changing `MAX_TURNS`, `MAX_QUESTIONS` or the idle cut.
- The core-wide `render_help` wart that files `/ask` under "🔐 Admin".
- The core-wide `"Admins only."` copy shown to a teacher-gated command.
- Any cost or quota enforcement.
