# Railway deployment — design

**Date:** 2026-07-25
**Status:** Approved for planning

## Goal

Run the bot continuously on Railway, deployed from GitHub on push to `main`,
with bot-owned data surviving redeploys.

## Constraints that drive the decisions

- **Long polling** → no port, no domain, no healthcheck; and only one replica,
  since Telegram serves `getUpdates` to a single consumer (`409` otherwise).
- **Ephemeral container filesystem** → the DB must live on a mounted volume.
  Bot-owned fields (`telegram_id`, `handle_observed`, `status_line`,
  `visibility`) are not reproducible from the sheet; losing them forces every
  student to re-link.
- **Railway delivers secrets only as env vars** → the service-account key
  cannot be a file there.

## Decisions

- **Volume + SQLite at `/data`.** Postgres would add a driver, a URL scheme
  change, and a dev/prod dialect split for one small table. The volume's
  "no replicas" caveat is free, since polling already caps us at one.
- **Inline credentials JSON, read in code** (`GOOGLE_SERVICE_ACCOUNT_JSON`),
  falling back to the existing file path when unset, so local dev is unchanged.
- **Alembic on startup replaces `create_all`.** `init_db` runs
  `upgrade head`, having first stamped legacy `create_all` databases (tables
  present, `alembic_version` absent) so they don't collide. Fresh volume →
  full schema; future schema changes apply automatically.
- **GitHub + Railpack**, no Dockerfile — it already handles uv projects.
- **Start command in `railway.json`**, not the dashboard, so it lives in git.
  Railpack can't infer it: the entry point is `src/jbcub_bot/__main__.py`, and
  it only looks for a root `main.py`, `manage.py`, or `Procfile`.
- **A separate dev bot** for local work; the production token exists only in
  Railway.

## Topology

One project, one `production` environment, one service from
`xoposhiy/jb-cub-bot`, one volume at `/data`. One replica, default
`ON_FAILURE` restart policy. No database service, no domain.

## Service variables

`BOT_TOKEN`, `LINK_SECRET` (new, distinct from local), `RIGHTS_SHEET_ID`,
`GOOGLE_SERVICE_ACCOUNT_JSON`, `BOOTSTRAP_ADMIN_IDS`, and
`DATABASE_URL=sqlite:////data/jbcub_bot.db`.

`GOOGLE_SERVICE_ACCOUNT_FILE` stays unset; every other `Settings` field keeps
its default.

**Four slashes** in `DATABASE_URL`: three means a *relative* path, so the DB is
silently created off-volume and vanishes on the next deploy, with no error.

## Verification

- Tests: credentials come from inline JSON when set, from the file otherwise;
  `init_db` upgrades a fresh DB, stamps-then-upgrades a legacy one, and is
  idempotent on an already-migrated one.
- `uv run pytest` green before pushing.
- In Telegram against the production bot: `/me` as bootstrap admin, plus a
  sheet-reading command to prove the inline credentials work.
- Redeploy, then confirm a previously linked account is still linked.

## Out of scope

Postgres. A `staging` environment. Webhooks. Volume backups. Log aggregation.
