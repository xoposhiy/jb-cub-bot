# Name first, AI second — design

**Date:** 2026-08-07
**Status:** Approved for planning

## Goal

Typing anything to the bot does the obvious thing: a name shows a person, a
question gets answered from the knowledge base. No button in between, and no
mode to leave — looking someone up works whether or not a conversation with the
agent is already going.

Today the two live apart. Unmatched text answers "I didn't find anyone by that
name. Ask AI instead?" with a button, and the answer to a tapped button opens an
FSM session in which the *next* name typed is read as a question instead of a
name. Both halves of that are the interface asking the reader to know which mode
they are in.

## Constraints that drive the decisions

- **A sentence can never be mistaken for a name search.** `matching.score`
  returns 0 when the query has more words than the person's name tokens, and the
  accept threshold is 0.80 measured against the real roster. So the roster search
  is safe to run on every message, including a follow-up in a conversation.
- **A name the roster does not know is indistinguishable from a question**, to
  code. "Егоров" and "пересдача" are both one unknown word. Only a model can
  tell them apart, so the agent has to be able to decline.
- **Features cannot import each other.** `directory` and `kb` meet in `core` or
  not at all.
- **The intent chain stops at the first handler that does not decline**, and a
  handler that declines must have answered nothing (`core/intents.py`).
- **An FSM state makes `nl_fallback` step aside**, so whatever owns a state owns
  its own routing. The KB no longer wants its own routing.
- **History is re-sent on every question.** One `read_note` returns up to 20 000
  characters and one `list_notes` up to 60 000, so carrying a run's input list
  forward is what forced the old 12-question cap.
- **In-memory state dies on redeploy**, which the knowledge base spec already
  accepts for conversations.

## Decisions

- **One rule, no modes: the roster search runs first on every free-text
  message, and the agent runs only where the search declines.** The chain stays
  `[directory.search, kb.ask]` exactly as it is; what changes is that nothing
  jumps ahead of it any more. Rejected: keeping the FSM session and letting the
  agent answer first inside it, which needs the agent's verdict to travel back
  out to a second dispatch pass — machinery in `core` bought for a case the
  scoring rule already handles.
- **The agent can refuse: `looks_like_a_person_name(name)`.** Called instead of
  answering, it ends the run, and the bot replies `No one found.` This exists
  for exactly one case — a name the roster does not carry — because without it
  a mistyped surname gets "The program documents say nothing about that."
  The prompt's rules: only for a message that is nothing but a person's name; a
  question with a name inside it is a question; a bare word naming a course, a
  document, a place or a programme is not a person; when unsure, answer, because
  a wrong verdict costs the reader their answer.
- **`ask()` short-circuits on the verdict** — no `validate` pass and no
  source-picking question. There is nothing to check and nothing to attach.
- **The KB FSM state goes.** With the search always first there is no ordering
  left for a state to express, and a state was the one thing making the KB
  intercept text before the chain. Conversations live in a module-level
  `dict[chat_id]` in the feature, like `_active` in `core/impersonation.py`, with
  the least-recently-used dropped past ~200 chats.
- **A conversation is question/answer pairs, not the run's input list.** Tool
  calls, tool outputs and reasoning items are not carried. The window is 40 000
  characters, cut at a pair boundary, with
  `[Earlier questions in this conversation are no longer shown.]` first when
  pairs fell out. Rejected: a window over the input list, which has to find
  boundaries that are not there — `validate`'s complaint also arrives in the
  `user` role — and carries note texts that dwarf everything else. Giving that
  complaint a role of its own would have supplied the missing boundary, and is
  dropped with the window that needed it: a change to a working check, for
  nothing.
  A follow-up therefore re-reads the note it needs. That costs a turn and buys
  three things: the citation line in the previous answer says where to look, the
  new answer rests on freshly read text rather than on a remembered read, and a
  `/kb_reload` mid-conversation stops leaving stale notes in view.
- **Every question carries the time it was asked**, formatted by
  `tools.current_datetime` so the agent sees one format for both. The rules say
  the bracketed first line is the bot's, not part of the message, and that the
  stamp on the question being answered is the current time — otherwise the agent
  spends a turn fetching a clock it is already holding. `current_datetime` stays
  for the questions where the answer turns on a date the stamp does not settle.
- **A conversation is dropped when the reader changed the subject** — the name
  verdict, `/ask`, and another intent having taken the previous free-text
  message — and never by a clock or a counter. Two people talking for an hour is
  not a problem to solve.
- **`dispatch` returns the intent that took the message, and `core/intents.py`
  remembers the last taker per chat.** That is how `kb` learns a profile was
  shown in between without knowing `directory` exists. Rejected: `directory`
  reporting the event through a core primitive of its own, which puts a rule
  about conversations inside the name search.
- **`/ask` keeps its place as "start clean".** It drops the conversation and
  bypasses the roster search, which makes it the only way to ask the agent about
  a person by name. The name check is switched off through the run context, so
  the tool answers "not applicable here" — the same trick `choose_sources` uses
  for being called too early — rather than through a second set of
  instructions, which would split the cached prompt prefix in two.
- **`🔎 Looking…` is the placeholder**, edited in place into the answer or into
  `No one found.` Neutral because it covers both outcomes: a reader who typed a
  surname should not be told an AI is thinking about it.
- **The rating pair rates and nothing else** — `✅ Good answer` / `❌ Bad
  answer`, still one pair in the chat walking forward under the newest message.
  There is no session for it to close. Its position is remembered outside the
  conversation, so dropping one does not leave a second live pair behind.
- **An exhausted hourly budget declines**, so the reader gets `No one found.`
  and the admins get the existing mention. Answering "the knowledge base is
  busy" to someone who mistyped a surname explains the wrong thing; `/ask`,
  being explicit, still gets the honest reason.
- **Nothing changes about who may use it.** The intent carries no `min_role`
  today and gains none, so a student's unmatched text now reaches the agent
  without a tap. The hourly `KB_RATE_LIMIT` is the only guard, and the ops chat
  is where we find out whether 100 is still the right number.
- **A stub keeps answering `kb:start`.** Buttons sent before the deploy stay in
  the chat, and a tap that spins is worse than one that says to just type the
  question.

## Out of scope

Summarising a conversation rather than windowing it. Surviving a restart.
Sharing a conversation between two people. Per-person quotas. Changing the
roster search, the snapshot store, `validate`, or how sources are attached.

## What is deleted

`kb_offer` and its `_OFFER` line, the `Ask AI` keyboard, `_PENDING` with
`remember_question`/`take_question`, `KbChat`, `on_question`, `_open`, `_greet`,
`_close`, `_clear_exit_button`, the thinking keyboard and its callback,
`_OPENED`, `_CLOSED`, `_EXHAUSTED`, `_IDLE`, `MAX_QUESTIONS`, `IDLE_SECONDS`.
`NOTHING_MATCHED` moves from `main.py` to `core/intents.py`, which now has two
callers. AGENTS.md's line on the intent contract changes with `dispatch`'s
return type.

## Verification

- `tests/test_intents.py` — `dispatch` returns the intent that took the message
  and `None` when every one declined; the last taker is recorded per chat and
  cleared when nobody took it.
- `tests/test_kb_agent.py` — a stub model calling `looks_like_a_person_name`
  yields the verdict, no source-picking turn and no history; the same call is
  refused when the run turns the check off, and the run goes on to answer; a
  question reaches the model with its stamp.
- `tests/test_kb_history.py` — pairs accumulate; the window drops the oldest
  pairs and only whole pairs; the elision note appears once something was
  dropped and not before; no tool output is ever stored.
- `tests/test_kb_handlers.py` — unmatched text is answered by the agent with no
  tap; a second question carries the first pair; a name verdict edits the
  placeholder into `No one found.`, logs the miss with the run's cost, and drops
  the conversation; a profile shown between two questions drops it too; an
  exhausted budget declines without calling the agent and pings the admins; an
  unconfigured runtime declines; `/ask` with a question bypasses the search and
  the name check; bare `/ask` says so and drops the conversation; a rating tap
  logs and strips the buttons and keeps the conversation; `kb:start` answers.
- `tests/test_search_integration.py`, `tests/test_fallback.py` — a found name
  still answers with a profile and never calls the agent; the offer button is
  gone from what unmatched text produces.
