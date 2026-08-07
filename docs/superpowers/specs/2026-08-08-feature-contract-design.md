# Feature contract, message pipeline, and the agent — design

**Date:** 2026-08-08
**Status:** Approved for planning

## Goal

A feature declares what it handles and *where in the order* it handles it, and the
core can see all of it — so it can refuse, list, and validate uniformly. Today the
order comes from the alphabet of package names, half the UI (buttons) is outside
the contract entirely, and the declarative parts of the manifest that were built
for this are unused.

Second goal: `kb` becomes an agent that other features can extend with tools. The
mechanism for skills and tools is *not* built here; the seam it needs is.

## Constraints that drive the decisions

- **Features may import each other.** This reverses the original rule. It is what
  lets the core shrink: code shared by two features stays with its owner instead of
  being pushed into `core/`.
- **The send surface is open-ended.** `/sync` sends documents, the profile uses
  `entities`, `grades` edits markup, the agent uploads PDFs. Anything we wrap and
  fail to cover becomes a hole.
- **The registration surface is finite.** A command, a message handler in the
  chain, a keyed button, a dialog step. That is the whole list today and there is
  no pressure on it.
- **Order is a business rule for text and meaningless for buttons.** Several
  handlers want the same plain text; `callback_data` is a key exactly one handler
  wants.
- **A dialog must not silence other features.** `main.py`'s `StateFilter(None)` is
  global, so any feature's state disables the whole text chain.
- **`/help` filters by role.** Anything promoted into the contract becomes a
  visibility filter, whether or not that was intended.
- **The bot runs one process, one event loop, in-memory FSM.** State dies on
  redeploy, which is already accepted.
- **11 250 lines of tests over 6 772 of source.** Roughly half test pure logic and
  are indifferent to routing; the rest are wired through registration.
- **The name-first AI fallback spec (2026-08-07) lands first**, on the current
  architecture.

## Decisions

- **A feature is a package under `features/` exporting `register(bot)`.** No
  manifest and no exported `router`. A package without `register` crashes the
  loader; today it silently disappears (`core/loader.py:33`), which for a project
  taking student contributions is the worst possible failure mode.
- **The core owns registration and guards; the feature owns sending.** A handler
  still receives an aiogram `Message`/`CallbackQuery` and calls whatever it likes
  on it. Rejected: wrapping replies too — see the open-ended send surface above.
  Practically this means a feature imports aiogram *types* but never `Router`,
  `F`, or `StateFilter`.
- **The contract is `describe`, `command`, `message`, `button`, `dialog`, `note`.**
  `describe` carries the emoji, the title and the one-line summary of the heading in
  `/help`, and is required: without it a feature has no heading, so its absence is a
  startup error rather than a blank line. The feature's *name* comes from its package
  name, so there is nothing to keep in sync.
- **Position is an integer, with named landmarks in the core (`LOOKUP`, `AGENT`).**
  Two handlers claiming the same number is a startup error. Rejected: a stage enum,
  because order *within* a stage falls back to registration order and therefore to
  the alphabet — the bug being fixed; and `before=`/`after=` with a topological
  sort, which is machinery for machinery when there is one text handler in the
  chain, and which forces a bad answer when the named handler is removed.
- **Only `message` declares a position.** A command's position is the core's:
  commands beat dialogs, dialogs beat the chain. A button has a key, not a
  position. This is what removes `~F.text.startswith("/")` from features
  (`edit.py:226`, `kb/handlers.py:580`) and makes "commands work inside a dialog" a
  property rather than a habit.
- **`message` takes a predicate, not an aiogram filter.** A plain
  `(Message) -> bool`, with `TEXT`/`PHOTO`/`DOCUMENT`/`ANY` supplied by the core, so
  a feature's own predicate is testable by calling it.
- **Guards are exactly two: `public` and `role`.** No `public` means a principal is
  required. Nothing else exists, and nothing but these two affects `/help`
  visibility. Rejected: promoting the bootstrap-admin check (`screens.py:34`,
  `require_linked`) into the contract — a contract guard is a visibility filter, so
  it would silently shorten `/help` for exactly the person who needs to be told
  something is wrong. That check stays ordinary code inside `directory`, with
  wording that says what to do; without it the write is not an error but silence,
  because the synthetic principal from `identity.apply_bootstrap` is attached to no
  session while the screen redraws as if saved.
- **The core owns dialog identity, routing, and the exit.** State identity is
  derived from the dialog's name, so a feature writes no `StatesGroup`. The core
  routes text to the owner, leaves the chain alone when the owner is someone else,
  owns one `/cancel` for every dialog, and offers `end_for(user_id)` — which is
  what `/as` needs (`impersonate/handlers.py:37` works today only because there is
  one state in the whole bot). The one command-name collision that pushed `kb` off
  `/cancel` (`kb/handlers.py:16-18`) stops existing.
- **`on_cancel` is a per-dialog hook.** Cancelling `/edit` redraws its screen with a
  notice (`edit.py:177`), which is the feature's knowledge; the core owns the
  command and the default wording.
- **No step graph, no dialog timeout.** The whole bot has one dialog with one state,
  and `kb`'s state is being deleted by the 2026-08-07 spec. A multi-step dialog
  keeps its step in its own data.
- **The chain contract stays: `False` means "not mine", and obliges the handler to
  have answered nothing.** Anything else, including `None`, counts as taken, so a
  handler that forgets to return cannot go silently unhandled. What changes is that
  `dispatch` returns the handler that took the message rather than a bool — which is
  how `ai` learns a profile was shown between two questions without knowing that
  `directory` exists.
- **An exception aborts the chain.** The crashed handler may already have answered;
  trying the next one would answer twice. Today this holds by accident (no `try` in
  `intents.dispatch`); here it is a stated property.
- **The last word belongs to the core.** `NOTHING_MATCHED` and the ops-log miss move
  out of `main.py:56`, whose comment currently reasons about the name search — core
  knowing one feature's domain.
- **`features/ai` owns the agent runtime and its Telegram face; `features/kb` owns
  the knowledge and extends `ai`.** `kb` hands over tools, a prompt fragment, and a
  note→document resolver in one call. Rejected: `core/agent/`, whose only real
  argument was that a feature could not be imported by another feature; and leaving
  the runtime in `kb`, which points the dependency against the direction of
  extension.
- **The source-picking turn stays in `ai`; what a source *is* comes from the
  contributor.** "Which of the things you read does this rest on" is general;
  "a note path resolves to this PDF or this URL" is `kb`'s. The alternative leaves
  `ai` importing `kb`, which the second contributor of tools would immediately have
  to undo. Cost of the seam: one parameter.
- **The agent is built lazily on first use**, as it already is
  (`kb/handlers.py:190`), so feature load order never becomes significant.
- **`/help` groups by feature, always, and marks role on the line.** A feature that
  registered anything visible has a heading. Today the heading renders only if
  student-visible command lines survive (`help/render.py:51`) and admin lines pool
  into one block, which is why `impersonate`'s heading renders never.
- **A note may be a callable of the principal, not only a string.** `kb`'s note
  lists what is in the base, and that changes on `/kb_reload`; a static string
  would lie. A note carries a `role` like everything else, so a feature can say one
  thing to students and another to whoever runs the bot.
- **Collisions crash before polling starts** — two `/cancel`, two `dir:admin`, two
  handlers at the same position, a command with no description, a feature with no
  `register` or no `describe`. Same category as a bad migration in `init_db()`: loud
  at boot beats discovered in production. The resolved chain is logged at startup, so
  the order is readable without opening five files.
- **Migration is one branch, not incremental on main.** Coexistence would need an
  adapter holding both routing paths at once — throwaway code in the most dangerous
  part of the system. Inside the branch, a commit per feature: `help` and
  `impersonate` first as the cheapest test of the contract, then `directory`, then
  splitting `kb` into `ai` + `kb`. Merge when the suite is green; the pure-logic
  half stays green throughout, so breakage is always local.

## Moves

`core/middleware.py` → `core/principal.py`. `core/sheets.py` and
`core/gradebook.py` → `features/directory/`. `core/kb_snapshot.py` →
`features/kb/snapshot.py`. The agent runtime, conversation window, budget, stats and
render → `features/ai/`. `oplog`'s `format_kb_*` → `features/ai/`; `OpsLog` and
`format_miss` stay in the core.

## What is deleted

`core/registry.py` and its test — it existed only because `help` could not import
its siblings. `core/intents.py` with `Intent`, `Intent.pattern`,
`IntentRouter.matches` and `Manifest`. `core/commands.py` with `CommandRegistrar`
and `_guard`. `Manifest.min_role`, which was never read in `src/`.
`middleware.HasRole`. `require_linked` as a decorator. `EditProfile(StatesGroup)`.
`main.py`'s `StateFilter(None)` handler, `NOTHING_MATCHED`, and the module-global
`_intent_router` — which is reset nowhere while `registry` beside it is
(`main.py:74-79`), so every `build_dispatcher` call appends the chain again.
`conftest.py`'s `_reset_feature_routers`. `/cancel` as a `directory` command.
`~F.text.startswith("/")` in every feature.

## Out of scope

The skills-and-tools framework for the agent. Splitting `directory` into
`roster`/`grades`/`cohort` — a separate spec, now cheap because shared code no
longer has to move to the core. Ordering among buttons (there is none: the key is
unique). Persistent FSM. Webhooks. Any change to the name search, visibility, the
ETL, or how the agent reads the base.

## Verification

- **New:** the loader crashes on a package without `register`, on two commands of
  one name, on two buttons of one key, on two message handlers at one position, on
  a command with no description; the resolved chain order is asserted in one place;
  `False` declines and obliges silence while `None` takes; text in a feature's own
  dialog reaches it, text in another feature's dialog still runs the chain, and a
  command beats both; `/cancel` ends any dialog and calls `on_cancel` when given;
  `public` and `role` behave identically across command, message, button and
  dialog; `/help` assembles commands, chain lines and notes, filters by role, and
  evaluates a callable note.
- **Rewritten:** `test_loader`, `test_commands`, `test_intents`. Deleted:
  `test_registry`.
- **Rewired, assertions unchanged:** `test_edit_handlers`, `test_privacy_handlers`,
  `test_kb_handlers`, `test_directory_*`, `test_impersonate*`, `test_fallback`,
  `test_departed_access`, `test_start_link`, `test_grades_screen`,
  `test_me_keyboard_integration`.
- **Untouched but for imports:** `test_visibility`, `test_sheets_*`,
  `test_gradebook_*`, `test_kb_tools`, `test_kb_snapshot`, `test_kb_render`,
  `test_sync_diagnostics`, `test_directory_render`, `test_cohort_export`,
  `test_oplog`, `test_config`, `test_init_db`.

## Follow-up in the same merge

`AGENTS.md`: a feature is `register(bot)`; features may import each other; the
chain contract with `dispatch`'s new return; `/cancel` and "commands beat dialogs"
are core rules. The 2026-08-07 spec's line that features cannot import each other
no longer holds.
