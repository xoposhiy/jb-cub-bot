# Railway Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot deployable on Railway: credentials from an env var, schema migrations on startup, and an explicit start command.

**Architecture:** Three independent changes. (1) Service-account credentials are built by a new `build_credentials()` helper that prefers an inline JSON env var and falls back to the existing file path; `fetch_rows` receives the resulting `Credentials` object instead of a path. (2) `init_db()` runs `alembic upgrade head` instead of `create_all`, stamping legacy `create_all` databases first so they don't collide. (3) A `railway.json` supplies the start command Railpack cannot infer.

**Tech Stack:** Python 3.12, uv, aiogram 3, SQLAlchemy 2, Alembic, pydantic-settings, google-auth, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-railway-deploy-design.md`.
- Local development must keep working unchanged: credentials from a file, DB at `sqlite:///jbcub_bot.db`.
- `fetch_rows` keeps arity 3 (`sheet_id`, credentials, `range_`) — existing test fakes in `tests/test_directory_sync.py` rely on it.
- Never add `psycopg`, Postgres, or a Dockerfile — explicitly out of scope.
- Tests follow the existing style: `monkeypatch`, `SimpleNamespace`, no network, no real files.
- `alembic.ini` is resolved relative to the current working directory, matching the existing CWD-relative `mapping_dir` setting and `prepend_sys_path = .`.
- Run tests with `uv run pytest`.

---

### Task 1: Credentials from inline JSON or file

**Files:**
- Modify: `src/jbcub_bot/core/config.py:12` (add field, default the existing one)
- Modify: `src/jbcub_bot/core/sheets_client.py` (whole file)
- Modify: `src/jbcub_bot/features/directory/handlers.py:21,157`
- Test: `tests/test_sheets_client.py` (create)
- Test: `tests/test_config.py` (add one test)
- Test: `tests/test_directory_sync.py:22-30` and 4 test bodies (update)

**Interfaces:**
- Consumes: `Settings` from `jbcub_bot.core.config`.
- Produces: `sheets_client.build_credentials(credentials_file: str, credentials_json: str) -> Credentials`; `sheets_client.fetch_rows(sheet_id: str, credentials: Credentials, range_: str = "A:Z") -> list[list[str]]`; `Settings.google_service_account_json: str`.

- [ ] **Step 1: Write the failing tests for `build_credentials`**

Create `tests/test_sheets_client.py`:

```python
import json

import pytest

from jbcub_bot.core import sheets_client

SA_INFO = {"type": "service_account", "project_id": "p"}


def test_build_credentials_prefers_inline_json(monkeypatch):
    captured = {}

    def fake_from_info(info, scopes=None):
        captured["info"] = info
        captured["scopes"] = scopes
        return "creds-from-json"

    monkeypatch.setattr(
        sheets_client.Credentials, "from_service_account_info", fake_from_info
    )
    monkeypatch.setattr(
        sheets_client.Credentials,
        "from_service_account_file",
        lambda *a, **k: pytest.fail("must not touch the filesystem when JSON is given"),
    )

    result = sheets_client.build_credentials("sa.json", json.dumps(SA_INFO))

    assert result == "creds-from-json"
    assert captured["info"] == SA_INFO
    assert captured["scopes"] == sheets_client._SCOPES


def test_build_credentials_falls_back_to_file(monkeypatch):
    captured = {}

    def fake_from_file(path, scopes=None):
        captured["path"] = path
        captured["scopes"] = scopes
        return "creds-from-file"

    monkeypatch.setattr(
        sheets_client.Credentials, "from_service_account_file", fake_from_file
    )

    result = sheets_client.build_credentials("sa.json", "")

    assert result == "creds-from-file"
    assert captured["path"] == "sa.json"
    assert captured["scopes"] == sheets_client._SCOPES
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sheets_client.py -v`
Expected: FAIL — `AttributeError: module 'jbcub_bot.core.sheets_client' has no attribute 'build_credentials'`

- [ ] **Step 3: Implement `build_credentials` and re-point `fetch_rows`**

Replace the whole of `src/jbcub_bot/core/sheets_client.py`:

```python
import json

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def build_credentials(credentials_file: str, credentials_json: str) -> Credentials:
    """Service-account credentials from an inline JSON blob or a key file.

    Inline JSON wins when present: hosts like Railway can only pass secrets as
    environment variables, while local development keeps using the file.
    """
    if credentials_json:
        return Credentials.from_service_account_info(
            json.loads(credentials_json), scopes=_SCOPES
        )
    return Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)


def fetch_rows(
    sheet_id: str, credentials: Credentials, range_: str = "A:Z"
) -> list[list[str]]:
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_)
        .execute()
    )
    return result.get("values", [])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sheets_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Write the failing config test**

Append to `tests/test_config.py`:

```python
def test_service_account_fields_default_to_empty(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    s = Settings(_env_file=None)  # ignore the developer's real .env
    assert s.google_service_account_file == ""
    assert s.google_service_account_json == ""
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_config.py::test_service_account_fields_default_to_empty -v`
Expected: FAIL — a `ValidationError` for the missing required `google_service_account_file`

- [ ] **Step 7: Add the settings fields**

In `src/jbcub_bot/core/config.py`, replace line 12:

```python
    google_service_account_file: str
```

with:

```python
    # Exactly one of these is supplied. Inline JSON is for hosts that can only
    # pass secrets as env vars (Railway); the file path is for local dev.
    google_service_account_file: str = ""
    google_service_account_json: str = ""
```

- [ ] **Step 8: Run the config tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (5 passed) — including `test_settings_missing_required_raises`, which still raises because `bot_token`, `link_secret`, and `rights_sheet_id` remain required.

- [ ] **Step 9: Update the sync tests for the new call**

In `tests/test_directory_sync.py`, add the new field to the `_settings()` helper (line 22-30):

```python
def _settings():
    return SimpleNamespace(
        google_service_account_file="sa.json",
        google_service_account_json="",
        rights_sheet_id="RIGHTS",
        cohorts_tab="Cohorts",
        rights_tab="Rights",
        rights_mapping="rights.yaml",
        mapping_dir="mapping",
    )
```

Then, in each of the four tests that patch `get_settings` (`test_sync_aborts_and_writes_nothing_on_cohort_parse_error`, `test_sync_happy_path`, `test_sync_creates_searchable_admin_only_in_rights`, `test_sync_aborts_and_writes_nothing_on_invalid_role`), add this line next to the existing `get_settings` patch:

```python
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
```

`test_sync_denied_for_non_admin` needs no change: the `@cmd.command(..., min_role=Role.ADMIN)` guard rejects the caller before the body runs, so credentials are never built.

- [ ] **Step 10: Run the sync tests to verify they fail**

Run: `uv run pytest tests/test_directory_sync.py -v`
Expected: FAIL — `AttributeError: <module 'jbcub_bot.features.directory.handlers'> has no attribute 'build_credentials'`

- [ ] **Step 11: Build credentials in `cmd_sync`**

In `src/jbcub_bot/features/directory/handlers.py`, change the import on line 21:

```python
from jbcub_bot.core.sheets_client import fetch_rows
```

to:

```python
from jbcub_bot.core.sheets_client import build_credentials, fetch_rows
```

and replace line 157:

```python
    sa = settings.google_service_account_file
```

with:

```python
    sa = build_credentials(settings.google_service_account_file,
                           settings.google_service_account_json)
```

The four `fetch_rows(..., sa, ...)` call sites below stay exactly as they are — `sa` is now a `Credentials` object rather than a path.

- [ ] **Step 12: Run the full suite to verify everything passes**

Run: `uv run pytest`
Expected: PASS — all tests, no failures.

- [ ] **Step 13: Commit**

```bash
git add src/jbcub_bot/core/config.py src/jbcub_bot/core/sheets_client.py src/jbcub_bot/features/directory/handlers.py tests/test_sheets_client.py tests/test_config.py tests/test_directory_sync.py
git commit -m "feat: accept Google service-account credentials as inline JSON"
```

---

### Task 2: Run migrations on startup instead of `create_all`

**Files:**
- Modify: `src/jbcub_bot/core/db.py:31-40` (replace `init_db`, extend imports)
- Test: `tests/test_init_db.py` (create)

**Interfaces:**
- Consumes: `get_engine()` and `get_settings().database_url` (both already exist); the single existing migration `alembic/versions/c72c6d99f0c1_create_users_table.py`, whose `down_revision` is `None` and which creates `users` with every column in `models.py`.
- Produces: `db.init_db() -> None`, still called from `main.run` — signature unchanged, so `main.py` needs no edit.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_init_db.py`:

```python
import pytest
from sqlalchemy import create_engine, inspect, text

from jbcub_bot.core import db
from jbcub_bot.core.config import get_settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point both the app engine and alembic at a throwaway SQLite file.

    Env vars beat the developer's .env in pydantic-settings, so this is
    hermetic. get_settings is lru_cached and db caches its engine, so both
    caches are reset around every test.
    """
    path = tmp_path / "test.db"
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    get_settings.cache_clear()
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_maker", None)
    yield path
    get_settings.cache_clear()


def test_init_db_builds_schema_on_a_fresh_database(db_path):
    db.init_db()

    inspector = inspect(db.get_engine())
    assert inspector.has_table("users")
    assert inspector.has_table("alembic_version")


def test_init_db_stamps_a_legacy_create_all_database(db_path):
    # The pre-migration world: tables exist, alembic_version does not.
    from jbcub_bot.core import models  # noqa: F401  (register models on Base)

    legacy = create_engine(f"sqlite:///{db_path.as_posix()}")
    db.Base.metadata.create_all(legacy)
    with legacy.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, role, last_name, first_name, past_cohorts,"
            " visibility) VALUES (1, 'Student', 'Ivanov', 'Ivan', '[]', '{}')"
        ))
    legacy.dispose()

    db.init_db()  # must not fail trying to re-create `users`

    inspector = inspect(db.get_engine())
    assert inspector.has_table("alembic_version")
    with db.get_engine().connect() as conn:
        assert conn.execute(text("SELECT last_name FROM users")).scalar() == "Ivanov"


def test_init_db_is_idempotent(db_path):
    db.init_db()
    db.init_db()  # a second run on an up-to-date DB is a no-op

    assert inspect(db.get_engine()).has_table("users")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_init_db.py -v`
Expected: 2 failed, 1 passed. Both `test_init_db_builds_schema_on_a_fresh_database` and `test_init_db_stamps_a_legacy_create_all_database` fail on the missing `alembic_version` table, because `create_all` never makes one. `test_init_db_is_idempotent` passes already — `create_all` is idempotent too — so it is a regression guard rather than a driver here.

- [ ] **Step 3: Replace `init_db` with a migration runner**

In `src/jbcub_bot/core/db.py`, change the imports at the top from:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jbcub_bot.core.config import get_settings
```

to:

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jbcub_bot.core.config import get_settings

# Resolved from the working directory, like the `mapping_dir` setting and
# alembic.ini's own `prepend_sys_path = .`.
_ALEMBIC_INI = "alembic.ini"
```

Then replace `init_db` (lines 31-40) with:

```python
def init_db() -> None:
    """Bring the schema up to date, creating it from scratch when absent.

    ``upgrade head`` builds a fresh database and applies anything added since
    the last deploy, so a schema change needs no deployment change. Databases
    created by the older ``create_all`` have the tables but no
    ``alembic_version``; alembic would read those as empty and fail trying to
    re-create ``users``, so they are stamped at head first.
    """
    inspector = inspect(get_engine())
    config = Config(str(Path(_ALEMBIC_INI).resolve()))
    if inspector.has_table("users") and not inspector.has_table("alembic_version"):
        command.stamp(config, "head")
    command.upgrade(config, "head")
```

`alembic/env.py` already sets `sqlalchemy.url` from `get_settings().database_url` and imports `models` for `target_metadata`, so nothing needs passing in and the local `models` import is no longer required here. Passing an absolute path to `Config` keeps `script_location = %(here)s/alembic` resolving correctly.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_init_db.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS — all tests. The `session` fixture in `tests/conftest.py` builds its own in-memory engine with `create_all` and is unaffected.

- [ ] **Step 6: Verify the real local database still opens**

Run: `uv run python -c "from jbcub_bot.core.db import init_db; init_db(); print('ok')"`
Expected: prints `ok`. This exercises the stamp path against the actual `jbcub_bot.db`, which was created by `create_all`.

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/core/db.py tests/test_init_db.py
git commit -m "feat: run alembic migrations on startup instead of create_all"
```

---

### Task 3: Railway build configuration

**Files:**
- Create: `railway.json`
- Modify: `.env.example`

**Interfaces:**
- Consumes: the entry point `src/jbcub_bot/__main__.py` (invoked as `python -m jbcub_bot`), and `Settings.google_service_account_json` from Task 1.
- Produces: nothing other tasks depend on. This is the last task.

- [ ] **Step 1: Create `railway.json`**

Railpack detects Python and uv on its own but cannot infer the start command, because it only looks for a root `main.py`, a Django `manage.py`, or a `Procfile`. Keeping the command here rather than in the dashboard puts it under review and survives recreating the service.

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "deploy": {
    "startCommand": "python -m jbcub_bot"
  }
}
```

- [ ] **Step 2: Verify the start command works locally**

Run: `uv run python -m jbcub_bot`
Expected: the bot starts and prints `Bot is running. Press 'q' + Enter to stop (Ctrl+C also works).` Stop it with `q` + Enter.

This only confirms the module path is right. Whether bare `python` resolves to the project venv inside Railway's image can only be seen in the deploy logs; the fallback is `uv run --no-sync python -m jbcub_bot`, applied in the deployment steps below.

- [ ] **Step 3: Document the new variables in `.env.example`**

Replace the `GOOGLE_SERVICE_ACCOUNT_FILE` and `DATABASE_URL` lines in `.env.example` with:

```
# Local dev reads the service-account key from a file. Railway can only pass
# secrets as env vars, so there set GOOGLE_SERVICE_ACCOUNT_JSON to the whole
# key instead and leave GOOGLE_SERVICE_ACCOUNT_FILE unset. Set exactly one.
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
GOOGLE_SERVICE_ACCOUNT_JSON=
# Four slashes means an absolute path, three means relative. On a mounted
# volume use: DATABASE_URL=sqlite:////data/jbcub_bot.db
DATABASE_URL=sqlite:///jbcub_bot.db
```

- [ ] **Step 4: Confirm `.env.example` covers every required setting**

Run: `uv run python -c "from jbcub_bot.core.config import Settings; print(sorted(Settings.model_fields))"`
Expected: every printed name is either present in `.env.example` or has a default in `Settings`.

- [ ] **Step 5: Commit**

```bash
git add railway.json .env.example
git commit -m "chore: add Railway start command config and document new env vars"
```

---

## Deployment (manual, after the tasks)

Not part of the TDD cycle — these steps need a browser and the owner's Railway account.

The separate dev bot from the spec already exists and the local `.env` already
points at it, so the production token is free to use here.

1. Push `main` to GitHub; Railway builds the new commit automatically.
2. In the service: attach a volume with mount path `/data`.
3. Set the variables from the spec, `DATABASE_URL=sqlite:////data/jbcub_bot.db` among them, and leave `GOOGLE_SERVICE_ACCOUNT_FILE` unset.
4. Read the deploy logs: both `uv sync` lines, then the bot's startup line. If `python` is not found, set `startCommand` to `uv run --no-sync python -m jbcub_bot` and push again.
5. In Telegram, against the production bot: `/me` as the bootstrap admin, then `/sync` to prove the inline credentials reach Google.
6. Redeploy and confirm a linked account is still linked — this is the check that the volume is really in use and `DATABASE_URL` has four slashes.
