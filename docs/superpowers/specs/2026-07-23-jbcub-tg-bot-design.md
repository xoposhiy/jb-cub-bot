# JBCUB Telegram Bot — Design Spec

**Date:** 2026-07-23
**Status:** Approved design, ready for implementation planning

## 1. Overview & Vision

An extensible Telegram bot for a study program at Constructor University, serving
both students and program administration.

The bot is built as a **core + plugins** system. The core provides shared
services (identity, roles, storage, Google Sheets ETL, feature discovery,
command/NL-intent routing). Every capability — present and future — is a
**feature plugin** dropped into `features/`, so students can extend the bot with
minimal friction and no edits to central files.

Three subsystems, built in stages:

- **Core / Extensibility** — the foundation. *(built now)*
- **Student Directory** — contacts/profiles with role-based visibility. *(first
  feature, built now)*
- **Knowledge Q&A** — answers about the program handbook, university policies,
  and academic calendar via LLM/RAG. *(future feature; described here only at the
  vision level — it will land as another plugin)*

**Guiding principles:** simplest data model that works; one owner per data field;
plugins are drop-in; enforcement logic (visibility, roles) lives in reusable
services, not scattered inside handlers.

### Tech stack

- **Language:** Python
- **Telegram framework:** aiogram 3.x (routers map cleanly onto plugins;
  middleware and FSM come built in)
- **Storage:** SQLite via SQLAlchemy, with Alembic migrations (easy migration to
  Postgres later if ever needed)
- **Google Sheets:** service account + Sheets API
- **Deployment:** long polling; secrets via `.env` / environment variables

## 2. Layers & Structure

Flat modules in `core/` (one file each — no premature package nesting):

```
core/
  config.py       # .env, secrets, settings
  db.py           # engine, session, models
  identity.py     # Principal resolution, roles, binding, one-time links
  sheets.py       # ETL: pull, per-cohort column mapping, upsert, reconciliation
  loader.py       # feature auto-discovery in features/
  intents.py      # NL router: free text -> intent -> feature
  middleware.py   # Principal injection, role guard
main.py           # bootstrap: bot, dispatcher, loader
features/
  directory/      # first feature: router + manifest
  <future>/       # Q&A and others
```

## 3. Data Model (SQLite / SQLAlchemy)

A single denormalized `users` table anchors identity for everyone (students and
staff). No join tables — cohorts and visibility are stored inline.

**`users`**

| Column | Type | Owner | Notes |
|---|---|---|---|
| `id` | int PK | bot | surrogate key |
| `role` | enum | sheet (rights) | `Admin` / `Student` now; `Teacher` later. One role per user. |
| `name` | str | sheet | |
| `matriculation` | str, nullable, unique | sheet | stable key for students; null for staff |
| `handle_sheet` | str, nullable | sheet | handle as listed in the sheet (a *hint*) |
| `handle_observed` | str, nullable | bot | handle observed from the user's Telegram profile |
| `telegram_id` | int, nullable, unique | bot | stable Telegram id; written on binding |
| `gmail` | str, nullable | sheet | configurable-visibility field |
| `github` | str, nullable | sheet | configurable-visibility field |
| `codeforces` | str, nullable | sheet | configurable-visibility field |
| `status_line` | str, nullable | bot | self-authored by the student |
| `primary_cohort` | str, **indexed** | sheet | current cohort; used for all filtering |
| `past_cohorts` | JSON list | sheet | previous cohorts, e.g. `["2023"]` |
| `visibility` | JSON dict | bot | `{field: level}`, e.g. `{"gmail": "cohort", "github": "nobody"}` |
| *(admin-only fields)* | various | sheet | visible only to admins; not to peers, not to the student |

**Key properties**

- **Field ownership (System of Record vs Projection):** each field has exactly
  one owner. Sheet-owned fields flow one-way sheet → bot; the bot never writes
  the sheet. Bot-owned fields (`telegram_id`, `handle_observed`, `status_line`,
  `visibility`) survive re-import because upsert is keyed by
  `matriculation` (students) / stable key (staff).
- **Dual-field pattern:** a field that exists in the sheet but the bot also wants
  to maintain itself is stored twice — `_sheet` (import) and `_observed` (bot).
  Currently only the handle. The model is shaped so adding another such pair is
  trivial.
- **Cohorts:** `primary_cohort` is a single indexed column driving all filtering
  (e.g. `/cohort`). `past_cohorts` is a JSON list. Neither needs a join table.
- **Visibility (`cohort` level):** stored as JSON per user. No join table.

## 4. Google Sheets Sync (ETL)

- **Source of truth:** Google Sheets. One sheet per cohort (formats differ
  slightly) plus one separate **rights sheet** (identifier → role), which is the
  SoR for staff records.
- **Access:** service account; each sheet shared with the service-account email;
  bot pulls via Sheets API.
- **Per-cohort column mapping:** a YAML file per cohort maps
  `sheet column -> canonical field`, normalizing the differing formats to the
  single model.
- **Trigger:** admin command only. No scheduled sync in v1, no snapshots, no
  rollback.
- **Flow:** pull → normalize via mapping → upsert sheet-owned fields keyed by
  `matriculation` (students) / stable key such as email/id (staff). Bot-owned
  fields are preserved. **On any parse error, abort and write nothing.**
- **Reconciliation report:** produced as part of **every sync** (no separate
  command) and shown to the admin who ran it — drift such as
  `handle_observed != handle_sheet`, unmatched sheet rows, duplicates. The bot
  proposes; a human updates the sheet. The bot never edits the sheet.

## 5. Identity & Binding

- **Middleware** runs on every update: resolves the Principal
  (`telegram_id` → user, role, cohorts) and injects it into handler context. An
  unrecognized user can only reach the binding flow.
- **Binding (no admin approval in the main path):** `/start` →
  - if `telegram_id` is already known → recognized;
  - else match the sender's `@username` against `handle_sheet`; on a **unique
    match to an unclaimed record**, write `telegram_id` + `handle_observed`;
  - no match → tell the user to contact an admin / use a one-time link.
- **Anti-impersonation:** auto-match fires only for an unclaimed record
  (first-claim). Once `telegram_id` is set, the record is claimed and
  identification is by `telegram_id`, not handle. Admins are ordinary records in
  the rights sheet, resolved by the same logic — auto-match grants at most
  `Student`-level access, so impersonation exposure is bounded by design.
- **One-time deep-link:** `t.me/<bot>?start=<token>` — signed, TTL, single-use —
  writes `telegram_id` directly. Issued by an admin when the handle didn't match
  or the account was lost.
- **Reset:** on loss/compromise an admin clears `telegram_id`; the next match or
  one-time link writes a new one. No revoke lists, no audit logs.

## 6. Feature Plugin Contract

Each feature is a package under `features/<name>/` exporting:

- **`router`** — an aiogram `Router` with command handlers.
- **`manifest`** — name, list of commands, NL-intent matchers, **minimum role**,
  and help text.

The **loader** auto-discovers feature packages, includes their routers,
registers their intents, and collects help text. **No edits to central files** —
truly drop-in, so there are no merge conflicts when many students contribute.

Features receive core services (identity, roles, db, sheets) via DI / handler
context. A **role guard** enforces `manifest.min_role` before a handler runs.

**NL router:** non-command text is run through the registered intent matchers and
dispatched to the matching feature. v1 uses simple keyword/regex matchers
declared in manifests; LLM-based classification is a future upgrade (needed by
Q&A). Commands and NL intents coexist — a feature may register either or both.

## 7. First Feature: Student Directory

Interactions:

- **Free-text name search (no command)** — the primary interaction. Typing a
  name (or handle) returns the matching student's contact card, or a list of
  candidates to pick from when several match. This is registered as the directory
  feature's NL intent, so it works without any command.
- `/me` — view own profile; set per-field visibility and edit `status_line`.
- `/cohort` — list your cohort (people with the same `primary_cohort`).
- Admin: `/sync` — runs the ETL and shows the reconciliation report as part of
  its result (there is no separate reconcile command).

**Admin actions live in the profile view, not as commands.** When an admin views
a student's profile, inline buttons visible only to admins offer:

- **Issue one-time link** — generate the binding deep-link for that student.
- **Reset telegram_id** — clear the student's binding (loss/compromise).

**Visibility enforcement** is a single reusable service, not logic inside each
handler. Every profile read passes through it.

### Visibility matrix

| Viewer → target | Sees |
|---|---|
| Student → cohort-mate (cohort sets intersect: primary ∪ past) | name, telegram, gmail, github, codeforces, status_line, cohort/role — subject to the target's per-field `visibility` |
| Student → anyone else (no shared cohort) | super-minimum: name, cohort/role, telegram, status_line |
| Teacher → any student (all cohorts) | the full study/contact set, across all cohorts |
| Admin → anyone | everything, including sheet-only / admin-only fields |

### Field categories

1. **Super-minimum** — `name`, `cohort/role`, `telegram`, `status_line`: always
   visible to all students; not hideable. (`status_line` is optional content —
   empty until the student sets it.)
2. **Configurable** — `gmail`, `github`, `codeforces` (and future ones): the
   student picks a per-field level in `{nobody, cohort, all_students}`. **Default:
   `cohort`.**
3. **Admin-only** — visible only to admins; not to peers and not to the student
   themselves.

**Staff override:** the `nobody/cohort/all_students` levels govern
**student-to-student** visibility only. Teachers and admins are staff and are not
affected: a teacher always sees the full study/contact set across all cohorts; an
admin sees everything. So `nobody` means "no student sees it" — staff still do.

### Cohort-mate rule

Two users are cohort-mates for visibility if
`(viewer.primary_cohort ∪ viewer.past_cohorts) ∩ (target.primary_cohort ∪ target.past_cohorts) ≠ ∅`.
Filtering (e.g. `/cohort`) uses `primary_cohort` only (indexed).

## 8. Tooling & Dev Setup

- **Dependency & environment manager: `uv`** (Astral). One tool handles the
  virtualenv, dependencies, the Python version, and the lockfile — the lowest
  friction for student contributors, which is a central project goal.
- **`pyproject.toml`** — project metadata and dependencies (aiogram, SQLAlchemy,
  Alembic, google-api-python-client, pydantic-settings, etc.).
- **`uv.lock`** — pinned, reproducible versions; committed to the repo.
- **Workflow:**
  - `uv sync` — create/refresh an identical environment (incl. the right Python).
  - `uv run python -m jbcub_bot` — run the bot without manually activating a venv.
  - `uv run pytest` — run tests.
  - `uv add <pkg>` — add a dependency.

## 9. Cross-cutting Concerns

- **Config/secrets:** `.env` / environment — bot token, service-account key,
  sheet ids.
- **Deployment:** long polling. Webhook can be added later if scale demands.
- **Testing (TDD):** unit tests for the visibility enforcement service, identity
  resolution/binding, and ETL column mapping; a contract test for the feature
  loader.

## 10. Out of Scope (v1)

- No self-registration — admins maintain all roster data.
- No scheduled sync, snapshots, or rollback.
- No audit logs, no binding revoke lists.
- No LLM/RAG yet (Q&A is a future plugin).
- The bot never writes back to Google Sheets.
