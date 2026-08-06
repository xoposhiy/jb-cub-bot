# Sticky admin impersonation — design

**Date:** 2026-08-06
**Status:** Approved by request
**Supersedes** the mechanism in `2026-07-25-admin-impersonation-design.md` and
`2026-07-29-interactive-admin-impersonation-design.md`. Their goal carries over:
an admin drives the student's real interactive screens, edits included.

## Goal

Let an admin see the whole bot as a student sees it, across many messages, until
they leave. `/as <ref>` enters that mode; `/unas` returns. The one-shot
`/as <ref> <query>` form goes away.

The one-shot form fakes a `Message` and re-enters through
`dispatcher.propagate_event`, which skips the outer middlewares — `FSMContext`
among them. So today no multi-turn screen works under `/as`: a KB session, a
field edit awaiting its value. A sticky mode carries no update of its own, so
the real update takes the normal path and those screens work for the first time.

## Constraints and decisions

- The active target lives in a module-level dict in `core/impersonation.py`,
  keyed by the admin's telegram id, with a `reset()` for tests — same shape as
  `kb_handlers.reset_pending()`. One process, one event loop, so no locking.
  It is deliberately **not** FSM data: `state.clear()` inside a KB session or an
  edit cancel would otherwise end the mode as a side effect.
- The mode does not survive a restart. Accepted over a `User` column and a
  migration: a deploy dropping you back to your own view is the safe direction,
  and the missing banner says so.
- `PrincipalMiddleware` reads that one source instead of today's three (passed
  data, a `callback_data` marker, FSM data). Everything else there is unchanged,
  including the separate departed check on the target — what a departed student
  sees is the refusal.
- `/unas` is never impersonated: the middleware skips the swap for it. Without
  that, a departed target — refused before any handler runs — would trap the
  admin in the refusal until a restart.
- A separate `message`-only middleware prints `👤 Viewing as <name> · /unas to
  return`, sent before the handler runs. That lands before most answers, but
  not `edit.on_value` or `_reprompt`: both go through `_redraw`, which edits a
  message sent earlier, so the banner — sent after that message already
  existed — lands below it instead of above. It needs no exceptions: `/unas`
  arrives with no impersonation to announce, and a `/as` refused inside the
  mode is refused *because* of the mode, so saying so is right. Separate from
  `PrincipalMiddleware` because that file is about who is refused, and says so
  in its docstring. Not on callbacks: a button usually edits its own message in
  place, and a banner per tap would scroll the screen away.
- `/unas` is registered straight on the router, not through `CommandRegistrar`,
  so it appears in nobody's `/help` — the student's view stays the student's
  view, and the way out is printed in every banner instead.
- Commands inside the mode run with the target's role, because `principal`
  *is* the target — a student target refuses `/as` same as any other admin
  command, so switching away from a student is `/unas` then `/as`. A staff
  target does not refuse it, so retargeting there works without `/unas` too.
  Rejected: authorizing `/as` unconditionally on the real caller — one admin
  command working inside the mode while the rest follow the target's role
  costs more to explain than the two-command path costs to type.
- Both commands clear the FSM, so an unfinished dialog does not leak from your
  view into the student's, or back.

## What this removes

`callback_data()`, `split_callback()`, the `|as:` marker, and the
`impersonate_ref` threading through `directory/render.py`, `edit.py`,
`privacy.py` and `handlers.py`; the `propagate_event` hack; and the
`state is None` branches in `kb/handlers.py` and `directory/edit.py`, including
`_answer_one_shot`. All of it exists only to serve the one-shot form.
