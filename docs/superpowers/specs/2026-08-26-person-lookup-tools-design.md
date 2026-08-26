# Agent tools for finding a person — design

**Date:** 2026-08-26
**Status:** Implemented

## Goal

A question that names a specific person -- "who is Ivan Petrov", "does Anna
Weber teach this course" -- should get that person's profile, not a knowledge
base answer. Today it does not: the deterministic roster search at `LOOKUP`
only catches a bare name (`matching.score` returns 0 once a query has more
words than the name has tokens), so a name inside a real question reaches the
agent, which has no directory in it and answers "the program documents say
nothing about that."

## Constraints that drive the decisions

- **The base is not a directory.** Answering a question about a person from
  the agent's own knowledge is exactly the invention `SYSTEM_RULES` already
  forbids for program facts.
- **Privacy rules live in one place.** `render_profile`/`profile_keyboard`/
  `profile_entities` already apply `visibility.py` and the departed-profile
  rule; a second path to a profile must go through the same three functions,
  not a copy of what they do.
- **A synchronous `Session` is not thread-safe.** The `agents` SDK runs a
  sync tool function through `asyncio.to_thread`, and a `Session` opened on
  the request's own thread breaks -- or worse, silently misbehaves -- when
  touched from a worker thread. Every other feature already reads this
  database with a handful of synchronous queries directly on the event loop,
  never through `to_thread`; a tool that reads the same roster has to follow
  the same rule, which means the tool function itself must be `async def` so
  the SDK does not offload it.
- **The tool call carries no Telegram object.** Rendering and sending the
  profile needs `bot`, the target `Message` and a `Session` outside the
  agent's run context, so a tool can only *pick* a person, not show them.

## Decisions

- **Two tools: `search_people(query)` and `show_profile(person_id)`.**
  `search_people` runs `directory.search.rank_users` and the same
  `classify()` the deterministic search uses, so a name is judged identically
  whether it arrived bare or inside a question. It reports either one clear
  leader or a shortlist, each with the roster row's `id` -- not the
  matriculation, which staff rows may lack. `show_profile` only records the
  chosen id on the run context and ends the turn; `kb/handlers.py` resolves
  the id, renders through `directory`'s own three functions, sends it, and
  drops the conversation, the same way a `looks_like_a_person_name` verdict
  does.
- **`Ask` gains `session` and `include_departed`.** Both reach `search_people`
  through the run context, the same trick `check_names` already uses. A
  caller with no session (most of `test_kb_agent.py`) never exercises the
  tool, so nothing else needed to change.
- **The run context also gains `profile_id: int | None`.** `_stop_on_a_verdict`
  (renamed from `_stop_on_a_name`) ends the run on either that or
  `person_name`. `ask()` short-circuits the same way a verdict does: no
  `validate` pass, no source-picking question.
- **The prompt tells the agent to try both places, plainly, rather than
  routing by how the question is phrased.** Two earlier versions both split
  by pattern -- "person question → roster only, program question → base
  only," then "'who is X' → roster, 'does X teach this' → base" -- and both
  broke on a real question: "Кто такой Александр Омельченко?" ("Who is
  Alexander Omelchenko?") is exactly the "who is X" shape, but he is only in
  the handbook, not the roster, and the agent told the reader the registry
  had nobody by that name without ever searching the base. A person can be on
  the roster, in the base, in both, or in neither, and no phrasing reliably
  says which -- so the rule is just: try `search_people` and the base before
  answering about somebody or saying they don't exist. `show_profile` is the
  answer when a profile is what was actually asked for; when the base already
  named their role, what they teach or who to contact, that answer goes out
  instead.
- **`looks_like_a_person_name` is unchanged and still comes first for a bare
  name.** A name the roster does not carry declines exactly as before;
  `search_people` for the same name would report nobody, too, so nothing
  needed to be shared between the two paths besides the shortlist logic.
- **`/ask` is no longer the only way to ask about a person by name** -- a
  question with a name inside it now reaches `search_people` either way. It
  still switches the name check off, so a bare, roster-known name reaches
  `search_people` instead of being declined outright -- the one gap it still
  closes.
- **The admin ops log gets a new entry, `format_kb_person_found`.** A profile
  shown by the agent is a hit, not a miss, but costs a model turn the same way
  a question does, so it goes in the same feed with the same trace.

## Out of scope

Changing what the deterministic `LOOKUP` search does for a bare name.
Teaching the agent anything about grades or cohorts beyond what
`render_profile` already shows. A confidence threshold different from the
chain's own `LEAD`/`SPREAD`.

## Verification

- `tests/test_directory_search.py` — `classify` names a clear leader, lists a
  shortlist, or neither.
- `tests/test_kb_tools.py` — `search_people` reports no match, one clear
  match with its id, or a shortlist; respects `include_departed`;
  `summarize_result` reads both new tools for the admin trace.
- `tests/test_kb_agent.py` — `search_people` then `show_profile` ends the run
  before checking or source-picking; an unknown id is refused and the run
  continues; a session-less run declines the tool politely; the prompt names
  both tools and tells the agent to leave a merely-mentioned name alone.
- `tests/test_kb_handlers.py` — a profile pick resolves the placeholder,
  sends the profile with its keyboard, drops the conversation, and is logged
  with who was shown; a picked id that no longer exists falls back to
  "No one found."
