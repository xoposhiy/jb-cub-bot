# Telegram log chat — design

**Date:** 2026-07-29
**Status:** Approved for planning

## Goal

One private staff chat that shows every user request the bot failed to serve —
both the crashes and the dead ends — with who asked, what they typed, and what
the bot answered.

## Constraints that drive the decisions

- **A crash report must survive a bad destination.** Today it DMs the bootstrap
  admins, which works on an empty DB. A log chat can be unset, or the bot can be
  removed from it, and neither may swallow the report.
- **A logged query is literal user text.** Any markup parsing would break on a
  query containing `_` or `<`, and the chat holds students' own words.

## Decisions

- **`core/oplog.py` owns the destination**, not the formatting: `OpsLog(bot,
  chat_id, admin_ids).send(text)` tries the chat, falls back to admin DMs when
  the chat is unset *or* the send fails, and never raises. `core/errors.py`
  keeps `summarize`/`format_traceback` and hands delivery to it. Splitting them
  this way means the fallback is written and tested once, for both kinds of
  entry.
- **`LOG_CHAT_ID: str = ""`** in `Settings` — a channel id is `-100…`, and empty
  means the feature is off. `build_dispatcher` takes it as an optional argument,
  so the existing tests keep working unchanged.
- **Three call sites, all closures in `build_dispatcher`**: the existing
  `dp.errors` handler, `nl_fallback` when it answers `NOTHING_MATCHED`, and the
  non-text branch of `nothing_understood`. Closing over one `OpsLog` avoids
  threading it through workflow data.
- **An unknown command and an access refusal are not logged.** Both mean the bot
  answered correctly; only a *miss* is a gap in what the bot can do.
- **Sent as plain text, no `parse_mode`**, query clipped to 500 characters. A
  miss entry names the person from `principal` (Telegram id and handle when
  there is no row), the query, and the answer; for non-text the query is
  `message.content_type`. When an admin is inside `/as`, one more line names the
  target — `impersonator` is already in the middleware data, so the handler
  takes it with a `None` default.
- **Time is the log message's own timestamp.** An explicit UTC line would
  duplicate what Telegram already shows.

## Out of scope

A log file on the volume — logs go to stdout and Railway keeps them, and an
unbounded file on `/data` shares a volume with SQLite, so filling it would take
the bot down on a DB write instead of on a log write. Adding it later means a
byte-capped `RotatingFileHandler` *and* a way to fetch the file; half of that
pairing is worse than nothing.

Throttling and aggregation: a spammed chat hits Telegram's rate limit, the send
fails, and the entry falls back to the host log. Storing misses in the database.

## Verification

- `tests/test_oplog.py` — destination choice (chat set → chat only; unset → DMs;
  chat send raises → DMs), and the miss entry's shape.
- `tests/test_fallback.py` — an unmatched text query and a photo each produce an
  entry; an unknown command produces none.
