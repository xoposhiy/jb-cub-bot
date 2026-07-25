# Railway deployment — design

**Date:** 2026-07-25
**Status:** Approved for planning

## Goal

Run the bot continuously on [Railway](https://railway.com) instead of a
developer laptop, deployed from GitHub, with bot-owned data surviving
redeploys.

Non-goal: changing how the bot works. The only code changes are those Railway
forces (secrets cannot be files there).

## Constraints that shape the design

- **Long polling.** `dp.start_polling` opens outbound connections to
  `api.telegram.org`. The service needs no public domain, no listening port,
  and no healthcheck.
- **One poller per token.** Telegram serves `getUpdates` to a single consumer;
  a second one causes `409 Conflict` and updates split unpredictably between
  the two. So: exactly one replica, and a separate dev bot for local work.
- **Ephemeral container filesystem.** Every deploy and every restart starts
  from a fresh image. Anything written to the working directory is lost.
- **Bot-owned data is not reproducible.** `telegram_id`, `handle_observed`,
  `status_line`, and `visibility` are owned by the bot, not by the Google
  Sheet (see `AGENTS.md`). Losing them forces every student to re-link.
  Therefore the database must live on persistent storage.
- **Railway delivers secrets only as environment variables.** There is no
  mechanism to place a file into the container.

## Scope decisions (from brainstorming)

- **Volume + SQLite, not Postgres.** A Railway volume mounted at `/data` holds
  the SQLite file. Rejected Postgres: it needs a `psycopg` dependency, a URL
  scheme change, real Alembic migrations, and leaves dev on SQLite and prod on
  Postgres — two dialects for a bot whose whole dataset is one small table.
  The volume's "no replicas" caveat costs nothing, because a polling bot is
  restricted to one replica anyway.
- **Service-account credentials as inline JSON, read in code.** Rejected
  writing the file from a shell in the start command: it puts deployment logic
  in dashboard settings instead of the repo, spills the secret onto disk, and
  cannot be tested.
- **GitHub + Railpack, no Dockerfile.** Railpack already handles this project
  shape. Rejected a hand-written Dockerfile (manual work, no benefit) and
  `railway up` as the primary path (deploys untracked working-tree state).
- **A second dev bot for local development.** The production token lives only
  in Railway variables.
- **Start command in `railway.json`, not in the dashboard.** Keeps it in git,
  reviewable, and reproducible if the service is ever recreated.

## Railway topology

```
project  jbcub-bot
└── environment  production
    └── service  jbcub-bot   ← GitHub xoposhiy/jb-cub-bot, branch main
        ├── volume  mounted at /data
        └── variables (see below)
```

One service. No database service, no domain, no healthcheck. Replicas: 1.
Restart policy: default `ON_FAILURE`.

## Build and start

Railpack detects Python from `pyproject.toml` and uv from `uv.lock`, then runs:

```
uv sync --locked --no-dev --no-install-project   # dependencies, cached layer
uv sync --locked --no-dev --no-editable          # install the project itself
```

Python version comes from `.python-version` (3.12).

Railpack cannot infer the start command: it looks for a root `main.py`, a
Django `manage.py`, or a `Procfile`, and this project's entry point is
`src/jbcub_bot/__main__.py`. Without an explicit command the build fails with
`No start command detected`. Hence `railway.json`:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "deploy": {
    "startCommand": "python -m jbcub_bot"
  }
}
```

Railpack puts the project venv on `PATH`, so bare `python` resolves to it. If
that turns out not to hold, the fallback is `uv run --no-sync python -m
jbcub_bot`.

`main.py:_watch_for_quit` already returns early unless `sys.stdin.isatty()`, so
the interactive quit-watcher stays dormant in a container. No change needed.

## Code changes

### 1. `core/config.py` — accept inline credentials

Add `google_service_account_json: str = ""` and give
`google_service_account_file` a default of `""`, so exactly one of the two may
be supplied.

### 2. `core/sheets_client.py` — build credentials from either source

`fetch_rows` currently hardcodes `Credentials.from_service_account_file`.
Select the source instead:

```python
def _credentials(credentials_file: str, credentials_json: str) -> Credentials:
    if credentials_json:
        return Credentials.from_service_account_info(
            json.loads(credentials_json), scopes=_SCOPES
        )
    return Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
```

`fetch_rows` takes the JSON as an additional argument; the caller in
`core/sheets.py` passes both settings values. Inline JSON wins when present,
so local development keeps working through the file unchanged.

### 3. `.env.example` — document the new variable

Add `GOOGLE_SERVICE_ACCOUNT_JSON` with a note that Railway uses it while local
development uses the file, and that on a volume `DATABASE_URL` needs four
slashes.

Nothing else in the codebase is touched.

## Service variables

| Variable | Value |
|---|---|
| `BOT_TOKEN` | production bot token |
| `LINK_SECRET` | long random string, distinct from the local one |
| `RIGHTS_SHEET_ID` | same spreadsheet as local |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | full contents of the service-account key |
| `DATABASE_URL` | `sqlite:////data/jbcub_bot.db` |
| `BOOTSTRAP_ADMIN_IDS` | owner's Telegram id |

`GOOGLE_SERVICE_ACCOUNT_FILE` is deliberately left unset. Every other
`Settings` field keeps its default.

**Four slashes in `DATABASE_URL`.** `sqlite:///jbcub_bot.db` is a *relative*
path; `sqlite:////data/jbcub_bot.db` is absolute `/data/jbcub_bot.db`. With
three slashes the database is silently created in the working directory
instead of on the volume, and the data disappears on the next deploy with no
error message.

## Schema creation, and a known limitation

`main.run` calls `init_db()` → `Base.metadata.create_all`, which creates
missing tables. On a fresh volume that is sufficient and matches local
behaviour, so no migration step runs on deploy.

**Limitation, accepted for now:** `create_all` does not alter existing tables.
The first time a column is added to `User`, production will keep the old table
and queries will fail. When that happens, add a Railway **pre-deploy command**
`uv run alembic upgrade head`, which runs before traffic switches to the new
deployment. Out of scope here.

## Verification

- Tests: `fetch_rows` uses inline JSON when provided and falls back to the file
  path otherwise; both patched at the `Credentials` boundary, in the existing
  mock-based style.
- `uv run pytest` green before pushing.
- Deploy logs show both `uv sync` steps and then the bot's startup line.
- Live check in Telegram against the production bot: `/me` as the bootstrap
  admin, and a command that reads the sheet, to prove the inline credentials
  work.
- Persistence check: redeploy, then confirm a previously linked account is
  still linked.

## Out of scope

- Postgres, and Alembic migrations on deploy.
- A `staging` environment.
- Webhook mode instead of polling.
- Backups of the volume.
- Log aggregation or alerting beyond Railway's own log view.
