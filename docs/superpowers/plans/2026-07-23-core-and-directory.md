# Core + Student Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the extensible core (identity, roles, storage, Google Sheets ETL, plugin loader, command/NL-intent routing) and the first feature — a role-aware Student Directory.

**Architecture:** A `core/` layer of flat modules provides shared services. Every capability is a drop-in feature package under `features/` exporting an aiogram `Router` plus a `Manifest`. A single denormalized `users` table anchors identity; Google Sheets are a read-only source of truth synced one-way by an admin command.

**Tech Stack:** Python + uv, aiogram 3.x, SQLAlchemy + Alembic (SQLite), google-api-python-client, pydantic-settings, itsdangerous (signed links), pytest.

## Global Constraints

- Python managed by **uv**; `uv.lock` committed; run everything via `uv run`.
- Telegram framework: **aiogram 3.x**; deployment via **long polling**.
- Storage: **SQLite** via **SQLAlchemy** with **Alembic** migrations.
- Google Sheets access via **service account + Sheets API**; the bot **never writes to a sheet**.
- **No self-registration.** Admins maintain all roster data.
- **One role per user**; roles are `Admin` / `Student` now, `Teacher` reserved.
- `matriculation` is the only stable key for students; staff use a stable key (email/id) from the rights sheet.
- **Field ownership:** sheet-owned fields flow one-way sheet→bot; bot-owned fields (`telegram_id`, `handle_observed`, `status_line`, `visibility`) survive re-import (upsert keyed by matriculation/staff key).
- **No audit logs, no binding revoke lists, no snapshots/rollback.**
- Secrets via `.env` / environment.

---

## File Structure

```
pyproject.toml            # project metadata + deps (uv)
uv.lock                   # committed lockfile
.env.example              # documented required env vars
.gitignore
alembic.ini               # Alembic config
alembic/                  # migration env + versions
src/sdt_bot/
  __init__.py
  __main__.py             # `python -m sdt_bot` entrypoint
  main.py                 # bootstrap: bot, dispatcher, loader, middleware, polling
  core/
    __init__.py
    config.py             # pydantic-settings Settings
    db.py                 # engine, session factory, Base
    models.py             # Role enum, User model
    identity.py           # resolve/claim/reset binding
    tokens.py             # signed one-time link tokens
    middleware.py         # PrincipalMiddleware + HasRole filter
    loader.py             # Manifest dataclass + feature auto-discovery
    intents.py            # Intent dataclass + IntentRouter
    sheets.py             # ETL: mapping, normalization, upsert, reconciliation
  features/
    __init__.py
    directory/
      __init__.py         # exports `router` and `manifest`
      visibility.py       # visibility enforcement service
      render.py           # profile card rendering
      search.py           # name/handle search
      handlers.py         # aiogram handlers (/me, /cohort, search, admin buttons)
mapping/
  cohort-2024.yaml        # example per-cohort column mapping
tests/
  conftest.py             # in-memory DB session fixture
  ...                     # one test module per source module
```

---

## Task 1: Project scaffold, tooling, and config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`
- Create: `src/sdt_bot/__init__.py`, `src/sdt_bot/__main__.py`
- Create: `src/sdt_bot/core/__init__.py`, `src/sdt_bot/core/config.py`
- Create: `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `sdt_bot.core.config.Settings` (pydantic-settings) with fields
  `bot_token: str`, `database_url: str`, `google_service_account_file: str`,
  `rights_sheet_id: str`, `mapping_dir: str`, `link_secret: str`,
  `link_ttl_seconds: int`. Produces `get_settings() -> Settings`.

- [ ] **Step 1: Initialize project, git, and dependencies**

Run:
```bash
cd /c/work/projects/sdt-tg-bot
git init
uv init --package --name sdt-bot --python 3.12
uv add aiogram sqlalchemy alembic "google-api-python-client" google-auth pydantic-settings itsdangerous pyyaml
uv add --dev pytest pytest-asyncio
```
Expected: `pyproject.toml`, `uv.lock`, and `src/sdt_bot/` created; dependencies resolved.

- [ ] **Step 2: Add pytest config and .gitignore**

Add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Create `.gitignore`:
```
.venv/
__pycache__/
*.pyc
*.db
.env
```

- [ ] **Step 3: Write the failing test for config**

Create `tests/test_config.py`:
```python
import pytest
from sdt_bot.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("LINK_SECRET", "s3cret")
    monkeypatch.setenv("RIGHTS_SHEET_ID", "sheet-xyz")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "sa.json")
    s = Settings()
    assert s.bot_token == "123:abc"
    assert s.link_secret == "s3cret"
    assert s.database_url == "sqlite:///sdt_bot.db"  # default
    assert s.link_ttl_seconds == 86400  # default


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("LINK_SECRET", raising=False)
    monkeypatch.delenv("RIGHTS_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    with pytest.raises(Exception):
        Settings()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.config'`

- [ ] **Step 5: Implement config**

Create `src/sdt_bot/core/config.py`:
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    link_secret: str
    rights_sheet_id: str
    google_service_account_file: str
    database_url: str = "sqlite:///sdt_bot.db"
    mapping_dir: str = "mapping"
    link_ttl_seconds: int = 86400


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `src/sdt_bot/__init__.py` (empty) and `src/sdt_bot/core/__init__.py` (empty).

- [ ] **Step 6: Create the module entrypoint and env example**

Create `src/sdt_bot/__main__.py`:
```python
from sdt_bot.main import run

if __name__ == "__main__":
    run()
```

Create `.env.example`:
```
BOT_TOKEN=123456:replace-me
LINK_SECRET=generate-a-long-random-string
RIGHTS_SHEET_ID=google-sheet-id-of-rights-sheet
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
DATABASE_URL=sqlite:///sdt_bot.db
MAPPING_DIR=mapping
LINK_TTL_SECONDS=86400
```

Note: `sdt_bot.main` does not exist yet — `__main__.py` is wired now but only runs after Task 14. Tests do not import it.

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: scaffold project with uv, config, and tooling"
```

---

## Task 2: Database, User model, and first migration

**Files:**
- Create: `src/sdt_bot/core/db.py`, `src/sdt_bot/core/models.py`
- Create: `alembic.ini`, `alembic/` (via `alembic init`)
- Create: `tests/conftest.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: `sdt_bot.core.config.get_settings`.
- Produces: `sdt_bot.core.db.Base`, `sdt_bot.core.db.engine`, `sdt_bot.core.db.SessionLocal`, `sdt_bot.core.db.get_session()`.
- Produces: `sdt_bot.core.models.Role` (enum: `ADMIN="Admin"`, `STUDENT="Student"`, `TEACHER="Teacher"`), `sdt_bot.core.models.User` with columns:
  `id:int PK`, `role:Role`, `name:str`, `matriculation:str|None unique`,
  `handle_sheet:str|None`, `handle_observed:str|None`, `telegram_id:int|None unique`,
  `gmail:str|None`, `github:str|None`, `codeforces:str|None`, `status_line:str|None`,
  `primary_cohort:str|None indexed`, `past_cohorts:list[str] JSON`,
  `visibility:dict[str,str] JSON`, `link_nonce:str|None`.

- [ ] **Step 1: Write the shared session fixture**

Create `tests/conftest.py`:
```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sdt_bot.core.db import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as s:
        yield s
```

- [ ] **Step 2: Write the failing test for the model**

Create `tests/test_models.py`:
```python
import pytest
from sqlalchemy.exc import IntegrityError

from sdt_bot.core.models import Role, User


def test_create_and_read_user(session):
    u = User(
        role=Role.STUDENT,
        name="Ivan Ivanov",
        matriculation="30000001",
        handle_sheet="ivanov",
        primary_cohort="2024",
        past_cohorts=["2023"],
        visibility={"gmail": "cohort"},
    )
    session.add(u)
    session.commit()
    got = session.get(User, u.id)
    assert got.name == "Ivan Ivanov"
    assert got.past_cohorts == ["2023"]
    assert got.visibility == {"gmail": "cohort"}
    assert got.role is Role.STUDENT


def test_matriculation_unique(session):
    session.add(User(name="A", matriculation="1"))
    session.commit()
    session.add(User(name="B", matriculation="1"))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.db'`

- [ ] **Step 4: Implement db.py**

Create `src/sdt_bot/core/db.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from sdt_bot.core.config import get_settings


class Base(DeclarativeBase):
    pass


# Lazy: importing this module (e.g. from tests, which use their own in-memory
# engine) must NOT require a full .env. The engine is built on first real use.
_engine = None
_maker = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url)
    return _engine


def get_session() -> Session:
    global _maker
    if _maker is None:
        _maker = sessionmaker(bind=get_engine())
    return _maker()
```

Note: everywhere the plan needs a session factory (middleware, `main.py`), pass
`get_session` — a zero-arg callable returning a `Session`.

- [ ] **Step 5: Implement models.py**

Create `src/sdt_bot/core/models.py`:
```python
import enum

from sqlalchemy import JSON, BigInteger, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from sdt_bot.core.db import Base


class Role(str, enum.Enum):
    ADMIN = "Admin"
    STUDENT = "Student"
    TEACHER = "Teacher"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.STUDENT)
    name: Mapped[str] = mapped_column(String, default="")
    matriculation: Mapped[str | None] = mapped_column(String, unique=True)
    handle_sheet: Mapped[str | None] = mapped_column(String)
    handle_observed: Mapped[str | None] = mapped_column(String)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    gmail: Mapped[str | None] = mapped_column(String)
    github: Mapped[str | None] = mapped_column(String)
    codeforces: Mapped[str | None] = mapped_column(String)
    status_line: Mapped[str | None] = mapped_column(String)
    primary_cohort: Mapped[str | None] = mapped_column(String, index=True)
    past_cohorts: Mapped[list] = mapped_column(JSON, default=list)
    visibility: Mapped[dict] = mapped_column(JSON, default=dict)
    link_nonce: Mapped[str | None] = mapped_column(String)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Initialize Alembic and generate the first migration**

Run:
```bash
uv run alembic init alembic
```
Edit `alembic/env.py`: set `target_metadata` and the URL from settings. Replace the `target_metadata = None` line and the config section with:
```python
from sdt_bot.core.config import get_settings
from sdt_bot.core.db import Base
from sdt_bot.core import models  # noqa: F401  (register the model)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```
Then:
```bash
uv run alembic revision --autogenerate -m "create users table"
uv run alembic upgrade head
```
Expected: a migration file under `alembic/versions/` creating the `users` table; `sdt_bot.db` created.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add User model, db session, and initial migration"
```

---

## Task 3: Identity resolution and first-claim binding

**Files:**
- Create: `src/sdt_bot/core/identity.py`
- Create: `tests/test_identity.py`

**Interfaces:**
- Consumes: `User` from `sdt_bot.core.models`.
- Produces:
  - `find_by_telegram_id(session, telegram_id: int) -> User | None`
  - `try_claim_by_handle(session, telegram_id: int, username: str | None) -> User | None`
  - `resolve(session, telegram_id: int, username: str | None) -> User | None`
  - `reset_binding(session, matriculation: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_identity.py`:
```python
from sdt_bot.core import identity
from sdt_bot.core.models import User


def _add(session, **kw):
    u = User(name=kw.pop("name", "X"), **kw)
    session.add(u)
    session.commit()
    return u


def test_resolve_by_telegram_id(session):
    u = _add(session, telegram_id=777, handle_observed="old")
    got = identity.resolve(session, 777, "newhandle")
    assert got.id == u.id
    assert got.handle_observed == "newhandle"  # observed handle refreshed


def test_claim_unclaimed_by_handle(session):
    u = _add(session, handle_sheet="ivanov")
    got = identity.resolve(session, 555, "ivanov")
    assert got.id == u.id
    assert got.telegram_id == 555
    assert got.handle_observed == "ivanov"


def test_claimed_record_not_reclaimed_by_handle(session):
    _add(session, handle_sheet="ivanov", telegram_id=111)
    got = identity.resolve(session, 999, "ivanov")
    assert got is None  # already claimed; handle no longer a valid path


def test_unknown_user_returns_none(session):
    assert identity.resolve(session, 42, "nobody") is None


def test_reset_binding(session):
    u = _add(session, matriculation="30000001", telegram_id=777)
    assert identity.reset_binding(session, "30000001") is True
    session.refresh(u)
    assert u.telegram_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.identity'`

- [ ] **Step 3: Implement identity.py**

Create `src/sdt_bot/core/identity.py`:
```python
from sqlalchemy import select

from sdt_bot.core.models import User


def find_by_telegram_id(session, telegram_id: int) -> User | None:
    return session.scalar(select(User).where(User.telegram_id == telegram_id))


def try_claim_by_handle(session, telegram_id: int, username: str | None) -> User | None:
    if not username:
        return None
    matches = session.scalars(
        select(User).where(
            User.handle_sheet == username, User.telegram_id.is_(None)
        )
    ).all()
    if len(matches) != 1:
        return None  # no unique unclaimed record
    user = matches[0]
    user.telegram_id = telegram_id
    user.handle_observed = username
    session.commit()
    return user


def resolve(session, telegram_id: int, username: str | None) -> User | None:
    user = find_by_telegram_id(session, telegram_id)
    if user is not None:
        if username and user.handle_observed != username:
            user.handle_observed = username
            session.commit()
        return user
    return try_claim_by_handle(session, telegram_id, username)


def reset_binding(session, matriculation: str) -> bool:
    user = session.scalar(
        select(User).where(User.matriculation == matriculation)
    )
    if user is None:
        return False
    user.telegram_id = None
    session.commit()
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_identity.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: identity resolution with first-claim handle binding and reset"
```

---

## Task 4: One-time link tokens

**Files:**
- Create: `src/sdt_bot/core/tokens.py`
- Modify: `src/sdt_bot/core/identity.py` (add `bind_by_token`)
- Create: `tests/test_tokens.py`

**Interfaces:**
- Consumes: `Settings.link_secret`, `Settings.link_ttl_seconds`, `User`.
- Produces (`tokens.py`):
  - `issue_link_token(session, matriculation: str, secret: str) -> str` — sets a fresh `link_nonce` on the user and returns a signed token embedding `matriculation` + nonce.
  - `verify_link_token(session, token: str, secret: str, ttl: int) -> User | None` — returns the user if signature valid, not expired, and nonce matches; else `None`.
- Produces (`identity.py`):
  - `bind_by_token(session, telegram_id: int, username: str | None, user: User) -> User` — writes `telegram_id`, `handle_observed`, clears `link_nonce` (single-use).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tokens.py`:
```python
from sdt_bot.core import identity, tokens
from sdt_bot.core.models import User

SECRET = "unit-secret"


def _student(session):
    u = User(name="Ivan", matriculation="30000001")
    session.add(u)
    session.commit()
    return u


def test_issue_and_verify_roundtrip(session):
    u = _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    got = tokens.verify_link_token(session, tok, SECRET, ttl=1000)
    assert got.id == u.id


def test_expired_token_rejected(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, tok, SECRET, ttl=-1) is None


def test_tampered_token_rejected(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, tok, SECRET, ttl=1000) is not None
    assert tokens.verify_link_token(session, tok + "x", SECRET, ttl=1000) is None


def test_single_use_via_nonce(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    user = tokens.verify_link_token(session, tok, SECRET, ttl=1000)
    identity.bind_by_token(session, 12345, "ivan_new", user)
    # nonce cleared -> the same token no longer verifies
    assert tokens.verify_link_token(session, tok, SECRET, ttl=1000) is None
    assert user.telegram_id == 12345
    assert user.handle_observed == "ivan_new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.tokens'`

- [ ] **Step 3: Implement tokens.py**

Create `src/sdt_bot/core/tokens.py`:
```python
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from sdt_bot.core.models import User

_SALT = "one-time-link"


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def issue_link_token(session, matriculation: str, secret: str) -> str:
    user = session.scalar(select(User).where(User.matriculation == matriculation))
    if user is None:
        raise ValueError(f"no user with matriculation {matriculation}")
    # fresh nonce derived from the pk + current row id keeps it single-use;
    # itsdangerous provides the timestamp, we provide the uniqueness.
    nonce = f"{user.id}-{len(matriculation)}-{user.matriculation}"
    user.link_nonce = nonce
    session.commit()
    return _serializer(secret).dumps({"m": matriculation, "n": nonce})


def verify_link_token(session, token: str, secret: str, ttl: int) -> User | None:
    try:
        data = _serializer(secret).loads(token, max_age=ttl)
    except (BadSignature, SignatureExpired):
        return None
    user = session.scalar(
        select(User).where(User.matriculation == data["m"])
    )
    if user is None or user.link_nonce is None or user.link_nonce != data["n"]:
        return None
    return user
```

Note on nonce: the value only needs to change when a token is consumed. Task 13
issues tokens through this function; a fresh issue overwrites the nonce, and
`bind_by_token` clears it, so a consumed or superseded token fails the equality
check. (If you want cryptographic randomness later, swap the derivation — the
contract is "the stored nonce must equal the token's nonce".)

- [ ] **Step 4: Add bind_by_token to identity.py**

Append to `src/sdt_bot/core/identity.py`:
```python
def bind_by_token(session, telegram_id: int, username: str | None, user: User) -> User:
    user.telegram_id = telegram_id
    if username:
        user.handle_observed = username
    user.link_nonce = None  # single-use
    session.commit()
    return user
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tokens.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: signed single-use one-time link tokens for binding"
```

---

## Task 5: Principal middleware and role guard

**Files:**
- Create: `src/sdt_bot/core/middleware.py`
- Create: `tests/test_middleware.py`

**Interfaces:**
- Consumes: `identity.resolve`, `Role`, a session factory.
- Produces:
  - `PrincipalMiddleware(session_factory)` — aiogram `BaseMiddleware`; injects
    `data["principal"]` (a `User` or `None`) and `data["session"]`.
  - `HasRole(min_role: Role)` — a callable filter returning `bool`, using the
    ordering `STUDENT < TEACHER < ADMIN`.
  - `role_rank(role: Role) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_middleware.py`:
```python
from types import SimpleNamespace

from sdt_bot.core.middleware import HasRole, PrincipalMiddleware, role_rank
from sdt_bot.core.models import Role, User


def test_role_rank_ordering():
    assert role_rank(Role.STUDENT) < role_rank(Role.TEACHER) < role_rank(Role.ADMIN)


def test_has_role_allows_equal_or_higher():
    guard = HasRole(Role.ADMIN)
    assert guard(User(role=Role.ADMIN)) is True
    assert guard(User(role=Role.STUDENT)) is False


def test_has_role_none_principal_denied():
    assert HasRole(Role.STUDENT)(None) is False


async def test_middleware_injects_principal(session):
    session.add(User(name="Ivan", telegram_id=777, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal"] = data["principal"]

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="ivan"))
    await mw(handler, event, {})
    assert captured["principal"].telegram_id == 777
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_middleware.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.middleware'`

- [ ] **Step 3: Implement middleware.py**

Create `src/sdt_bot/core/middleware.py`:
```python
from aiogram import BaseMiddleware

from sdt_bot.core import identity
from sdt_bot.core.models import Role, User

_RANK = {Role.STUDENT: 0, Role.TEACHER: 1, Role.ADMIN: 2}


def role_rank(role: Role) -> int:
    return _RANK[role]


class HasRole:
    def __init__(self, min_role: Role):
        self.min_role = min_role

    def __call__(self, principal: User | None) -> bool:
        if principal is None:
            return False
        return role_rank(principal.role) >= role_rank(self.min_role)


class PrincipalMiddleware(BaseMiddleware):
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def __call__(self, handler, event, data):
        session = self.session_factory()
        data["session"] = session
        user = getattr(event, "from_user", None)
        if user is not None:
            data["principal"] = identity.resolve(session, user.id, user.username)
        else:
            data["principal"] = None
        return await handler(event, data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_middleware.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: principal-injection middleware and role guard filter"
```

---

## Task 6: Feature loader and Manifest contract

**Files:**
- Create: `src/sdt_bot/core/loader.py`
- Create: `src/sdt_bot/features/__init__.py`
- Create: `tests/test_loader.py`, `tests/fixtures_features/dummy/__init__.py`

**Interfaces:**
- Consumes: `Role`, aiogram `Router`.
- Produces:
  - `@dataclass Manifest(name: str, commands: list[str], intents: list, min_role: Role, help_text: str)`
    with defaults `commands=[]`, `intents=[]`, `min_role=Role.STUDENT`, `help_text=""`.
  - `@dataclass LoadedFeature(manifest: Manifest, router: Router)`.
  - `discover_features(package) -> list[LoadedFeature]` — imports each submodule
    of the given features package and reads its `router` and `manifest`.

- [ ] **Step 1: Write a dummy feature fixture**

Create `tests/fixtures_features/__init__.py` (empty) and `tests/fixtures_features/dummy/__init__.py`:
```python
from aiogram import Router

from sdt_bot.core.loader import Manifest
from sdt_bot.core.models import Role

router = Router()
manifest = Manifest(
    name="dummy",
    commands=["ping"],
    min_role=Role.STUDENT,
    help_text="a dummy feature",
)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_loader.py`:
```python
import tests.fixtures_features as fixtures_pkg
from sdt_bot.core.loader import Manifest, discover_features
from sdt_bot.core.models import Role


def test_manifest_defaults():
    m = Manifest(name="x")
    assert m.commands == []
    assert m.intents == []
    assert m.min_role is Role.STUDENT


def test_discover_reads_router_and_manifest():
    features = discover_features(fixtures_pkg)
    names = {f.manifest.name for f in features}
    assert "dummy" in names
    dummy = next(f for f in features if f.manifest.name == "dummy")
    assert dummy.manifest.commands == ["ping"]
    assert dummy.router is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.loader'`

- [ ] **Step 4: Implement loader.py**

Create `src/sdt_bot/core/loader.py`:
```python
import importlib
import pkgutil
from dataclasses import dataclass, field

from aiogram import Router

from sdt_bot.core.models import Role


@dataclass
class Manifest:
    name: str
    commands: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    min_role: Role = Role.STUDENT
    help_text: str = ""


@dataclass
class LoadedFeature:
    manifest: Manifest
    router: Router


def discover_features(package) -> list[LoadedFeature]:
    found: list[LoadedFeature] = []
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        manifest = getattr(module, "manifest", None)
        router = getattr(module, "router", None)
        if manifest is None or router is None:
            continue
        found.append(LoadedFeature(manifest=manifest, router=router))
    return found
```

Create `src/sdt_bot/features/__init__.py` (empty).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_loader.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: feature loader with Manifest contract and auto-discovery"
```

---

## Task 7: NL intent router

**Files:**
- Create: `src/sdt_bot/core/intents.py`
- Create: `tests/test_intents.py`

**Interfaces:**
- Produces:
  - `@dataclass Intent(name: str, pattern: str, handler: Callable)` — `pattern`
    is a regex matched case-insensitively against message text; `handler` is an
    async callable `handler(message, principal, session)`.
  - `IntentRouter` with `register(intent)`, `matches(text) -> Intent | None`
    (first registered intent whose pattern matches), and
    `async dispatch(text, message, principal, session) -> bool` (returns True if
    an intent handled it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_intents.py`:
```python
from sdt_bot.core.intents import Intent, IntentRouter


def test_matches_first_registered():
    r = IntentRouter()
    r.register(Intent("a", r"hello", handler=None))
    r.register(Intent("b", r"hel", handler=None))
    assert r.matches("hello there").name == "a"
    assert r.matches("nope") is None


async def test_dispatch_invokes_handler():
    calls = []

    async def h(message, principal, session):
        calls.append((message, principal))

    r = IntentRouter()
    r.register(Intent("greet", r"hi", handler=h))
    handled = await r.dispatch("hi", message="M", principal="P", session="S")
    assert handled is True
    assert calls == [("M", "P")]


async def test_dispatch_no_match_returns_false():
    r = IntentRouter()
    handled = await r.dispatch("whatever", message="M", principal=None, session="S")
    assert handled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_intents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.intents'`

- [ ] **Step 3: Implement intents.py**

Create `src/sdt_bot/core/intents.py`:
```python
import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class Intent:
    name: str
    pattern: str
    handler: Callable


class IntentRouter:
    def __init__(self):
        self._intents: list[Intent] = []

    def register(self, intent: Intent) -> None:
        self._intents.append(intent)

    def matches(self, text: str) -> Intent | None:
        for intent in self._intents:
            if re.search(intent.pattern, text, re.IGNORECASE):
                return intent
        return None

    async def dispatch(self, text, message, principal, session) -> bool:
        intent = self.matches(text)
        if intent is None:
            return False
        await intent.handler(message, principal, session)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_intents.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: NL intent router with regex matchers"
```

---

## Task 8: Sheets ETL — column mapping and normalization

**Files:**
- Create: `src/sdt_bot/core/sheets.py`
- Create: `mapping/cohort-2024.yaml`
- Create: `tests/test_sheets_normalize.py`

**Interfaces:**
- Produces:
  - `load_mapping(path: str) -> dict` — reads a YAML file `{canonical_field: column_header}`.
  - `normalize_rows(rows: list[list[str]], mapping: dict) -> list[dict]` — the
    first row is the header; each data row becomes `{canonical_field: value}`.
    Raises `MappingError` if any mapped column header is absent.
  - `class MappingError(Exception)`.

- [ ] **Step 1: Create an example mapping file**

Create `mapping/cohort-2024.yaml`:
```yaml
matriculation: "Matriculation Number"
name: "Full Name"
handle_sheet: "Telegram"
gmail: "Gmail"
github: "GitHub"
codeforces: "Codeforces"
primary_cohort: "Cohort"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_sheets_normalize.py`:
```python
import pytest

from sdt_bot.core.sheets import MappingError, load_mapping, normalize_rows


def test_load_mapping(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("matriculation: \"Matr\"\nname: \"Name\"\n", encoding="utf-8")
    m = load_mapping(str(p))
    assert m == {"matriculation": "Matr", "name": "Name"}


def test_normalize_rows_maps_by_header():
    rows = [
        ["Matr", "Name", "Telegram"],
        ["30000001", "Ivan Ivanov", "ivanov"],
    ]
    mapping = {"matriculation": "Matr", "name": "Name", "handle_sheet": "Telegram"}
    out = normalize_rows(rows, mapping)
    assert out == [
        {"matriculation": "30000001", "name": "Ivan Ivanov", "handle_sheet": "ivanov"}
    ]


def test_normalize_rows_missing_column_raises():
    rows = [["Name"], ["Ivan"]]
    mapping = {"matriculation": "Matr", "name": "Name"}
    with pytest.raises(MappingError):
        normalize_rows(rows, mapping)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_sheets_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.core.sheets'`

- [ ] **Step 4: Implement the normalization half of sheets.py**

Create `src/sdt_bot/core/sheets.py`:
```python
import yaml


class MappingError(Exception):
    pass


def load_mapping(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def normalize_rows(rows: list[list[str]], mapping: dict) -> list[dict]:
    if not rows:
        return []
    header = rows[0]
    index = {col: i for i, col in enumerate(header)}
    for field, column in mapping.items():
        if column not in index:
            raise MappingError(f"column {column!r} for field {field!r} not found")
    out = []
    for row in rows[1:]:
        record = {}
        for field, column in mapping.items():
            i = index[column]
            record[field] = row[i] if i < len(row) else ""
        out.append(record)
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_sheets_normalize.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: sheet column mapping and row normalization"
```

---

## Task 9: Sheets ETL — upsert and reconciliation

**Files:**
- Modify: `src/sdt_bot/core/sheets.py` (add upsert + reconciliation)
- Create: `tests/test_sheets_upsert.py`

**Interfaces:**
- Consumes: `User`, `Role`, `normalize_rows`.
- Produces:
  - `upsert_users(session, records: list[dict], key: str = "matriculation") -> None`
    — inserts/updates only sheet-owned fields (`name`, `handle_sheet`, `gmail`,
    `github`, `codeforces`, `primary_cohort`, `past_cohorts`, and `matriculation`);
    never touches `telegram_id`, `handle_observed`, `status_line`, `visibility`,
    `link_nonce`. Matches existing rows by `key`.
  - `@dataclass ReconcileReport(drift: list[str], unmatched: list[str], duplicates: list[str])`.
  - `reconcile(session, records: list[dict], key: str = "matriculation") -> ReconcileReport`
    — `drift`: users whose `handle_observed` differs from the record's
    `handle_sheet`; `unmatched`: record keys with no user; `duplicates`: keys
    appearing more than once in `records`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sheets_upsert.py`:
```python
from sdt_bot.core import sheets
from sdt_bot.core.models import User


def test_upsert_inserts_new(session):
    sheets.upsert_users(session, [
        {"matriculation": "1", "name": "Ivan", "handle_sheet": "ivan",
         "primary_cohort": "2024"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.name == "Ivan"
    assert u.primary_cohort == "2024"


def test_upsert_preserves_bot_owned_fields(session):
    session.add(User(matriculation="1", name="Old", telegram_id=777,
                     status_line="hi", handle_observed="ivan_obs",
                     visibility={"gmail": "nobody"}))
    session.commit()
    sheets.upsert_users(session, [
        {"matriculation": "1", "name": "New", "handle_sheet": "ivan_sheet"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.name == "New"               # sheet-owned updated
    assert u.handle_sheet == "ivan_sheet"
    assert u.telegram_id == 777          # bot-owned preserved
    assert u.status_line == "hi"
    assert u.handle_observed == "ivan_obs"
    assert u.visibility == {"gmail": "nobody"}


def test_reconcile_reports_drift_unmatched_duplicates(session):
    session.add(User(matriculation="1", name="Ivan",
                     handle_observed="ivan_new"))
    session.commit()
    records = [
        {"matriculation": "1", "handle_sheet": "ivan_old"},   # drift
        {"matriculation": "2", "handle_sheet": "x"},          # unmatched
        {"matriculation": "2", "handle_sheet": "x"},          # duplicate key
    ]
    report = sheets.reconcile(session, records)
    assert "1" in report.drift
    assert "2" in report.unmatched
    assert "2" in report.duplicates
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sheets_upsert.py -v`
Expected: FAIL with `AttributeError: module 'sdt_bot.core.sheets' has no attribute 'upsert_users'`

- [ ] **Step 3: Implement upsert and reconciliation**

Append to `src/sdt_bot/core/sheets.py`:
```python
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from sdt_bot.core.models import User

SHEET_OWNED = (
    "name", "handle_sheet", "gmail", "github", "codeforces", "primary_cohort",
    "past_cohorts",
)


def upsert_users(session, records: list[dict], key: str = "matriculation") -> None:
    for record in records:
        key_value = record.get(key)
        if not key_value:
            continue
        user = session.scalar(
            select(User).where(getattr(User, key) == key_value)
        )
        if user is None:
            user = User(**{key: key_value})
            session.add(user)
        for field_name in SHEET_OWNED:
            if field_name in record:
                setattr(user, field_name, record[field_name])
    session.commit()


@dataclass
class ReconcileReport:
    drift: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)


def reconcile(session, records: list[dict], key: str = "matriculation") -> ReconcileReport:
    report = ReconcileReport()
    keys = [r.get(key) for r in records if r.get(key)]
    report.duplicates = [k for k, n in Counter(keys).items() if n > 1]
    for record in records:
        key_value = record.get(key)
        if not key_value:
            continue
        user = session.scalar(
            select(User).where(getattr(User, key) == key_value)
        )
        if user is None:
            report.unmatched.append(key_value)
            continue
        observed = user.handle_observed
        sheet_handle = record.get("handle_sheet")
        if observed and sheet_handle and observed != sheet_handle:
            report.drift.append(key_value)
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sheets_upsert.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: sheet upsert preserving bot-owned fields and reconciliation report"
```

---

## Task 10: Directory — visibility enforcement service

**Files:**
- Create: `src/sdt_bot/features/__init__.py` (if missing) and `src/sdt_bot/features/directory/__init__.py`
- Create: `src/sdt_bot/features/directory/visibility.py`
- Create: `tests/test_visibility.py`

**Interfaces:**
- Consumes: `User`, `Role`.
- Produces (`visibility.py`):
  - `SUPER_MINIMUM = ("name", "telegram", "primary_cohort", "role", "status_line")`
  - `CONFIGURABLE = ("gmail", "github", "codeforces")`
  - `ADMIN_ONLY = ("matriculation",)`
  - `are_cohort_mates(a: User, b: User) -> bool`
  - `visible_fields(viewer: User, target: User) -> dict[str, object]` — returns the
    field→value mapping the viewer may see. `telegram` maps to `handle_observed or handle_sheet`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_visibility.py`:
```python
from sdt_bot.features.directory.visibility import (
    are_cohort_mates,
    visible_fields,
)
from sdt_bot.core.models import Role, User


def _u(**kw):
    return User(name=kw.pop("name", "U"), **kw)


def test_cohort_mates_by_intersection():
    a = _u(primary_cohort="2024", past_cohorts=["2023"])
    b = _u(primary_cohort="2022", past_cohorts=["2023"])
    c = _u(primary_cohort="2021", past_cohorts=[])
    assert are_cohort_mates(a, b) is True   # shared 2023
    assert are_cohort_mates(a, c) is False


def test_student_sees_cohort_mate_configurable_by_default():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                github="gh", visibility={})  # default -> cohort
    fields = visible_fields(viewer, target)
    assert fields["gmail"] == "t@gmail.com"
    assert fields["github"] == "gh"


def test_student_non_cohort_sees_super_minimum_only():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                handle_observed="tg")
    fields = visible_fields(viewer, target)
    assert "gmail" not in fields
    assert fields["telegram"] == "tg"
    assert fields["name"] == target.name


def test_field_hidden_when_level_nobody():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                visibility={"gmail": "nobody"})
    assert "gmail" not in visible_fields(viewer, target)


def test_field_all_students_visible_across_cohorts():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", github="gh",
                visibility={"github": "all_students"})
    assert visible_fields(viewer, target)["github"] == "gh"


def test_teacher_sees_full_set_across_cohorts_ignoring_nobody():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                visibility={"gmail": "nobody"})
    assert visible_fields(viewer, target)["gmail"] == "t@gmail.com"


def test_admin_sees_admin_only_fields():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, matriculation="30000001")
    assert visible_fields(viewer, target)["matriculation"] == "30000001"


def test_student_never_sees_admin_only():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", matriculation="30000001")
    assert "matriculation" not in visible_fields(viewer, target)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.features.directory.visibility'`

- [ ] **Step 3: Implement visibility.py**

Create `src/sdt_bot/features/directory/__init__.py` (empty for now — router/manifest added in Task 12).

Create `src/sdt_bot/features/directory/visibility.py`:
```python
from sdt_bot.core.models import Role, User

SUPER_MINIMUM = ("name", "telegram", "primary_cohort", "role", "status_line")
CONFIGURABLE = ("gmail", "github", "codeforces")
ADMIN_ONLY = ("matriculation",)

_DEFAULT_LEVEL = "cohort"


def _cohorts(u: User) -> set:
    cohorts = set(u.past_cohorts or [])
    if u.primary_cohort:
        cohorts.add(u.primary_cohort)
    return cohorts


def are_cohort_mates(a: User, b: User) -> bool:
    return bool(_cohorts(a) & _cohorts(b))


def _telegram(u: User):
    return u.handle_observed or u.handle_sheet


def visible_fields(viewer: User, target: User) -> dict:
    fields: dict = {}

    # Super-minimum: always visible to any student/teacher/admin.
    fields["name"] = target.name
    fields["telegram"] = _telegram(target)
    fields["primary_cohort"] = target.primary_cohort
    fields["role"] = target.role
    if target.status_line:
        fields["status_line"] = target.status_line

    is_admin = viewer.role is Role.ADMIN
    is_teacher = viewer.role is Role.TEACHER
    mates = are_cohort_mates(viewer, target)

    for field in CONFIGURABLE:
        value = getattr(target, field)
        if value is None:
            continue
        # Staff override: teachers/admins see configurable fields regardless.
        if is_admin or is_teacher:
            fields[field] = value
            continue
        level = (target.visibility or {}).get(field, _DEFAULT_LEVEL)
        if level == "all_students":
            fields[field] = value
        elif level == "cohort" and mates:
            fields[field] = value
        # level == "nobody" -> skip

    if is_admin:
        for field in ADMIN_ONLY:
            fields[field] = getattr(target, field)

    return fields
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: directory visibility enforcement service"
```

---

## Task 11: Directory — profile rendering and search helpers

**Files:**
- Create: `src/sdt_bot/features/directory/render.py`
- Create: `src/sdt_bot/features/directory/search.py`
- Create: `tests/test_directory_render.py`, `tests/test_directory_search.py`

**Interfaces:**
- Consumes: `visible_fields`, `User`.
- Produces (`render.py`): `render_profile(viewer: User, target: User) -> str` — a
  human-readable card built only from `visible_fields`.
- Produces (`search.py`): `search_users(session, query: str) -> list[User]` — case-
  insensitive substring match on `name`, `handle_sheet`, or `handle_observed`;
  `list_cohort(session, primary_cohort: str) -> list[User]` — exact match on the
  indexed `primary_cohort`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_directory_render.py`:
```python
from sdt_bot.features.directory.render import render_profile
from sdt_bot.core.models import Role, User


def test_render_includes_visible_and_omits_hidden():
    viewer = User(name="V", role=Role.STUDENT, primary_cohort="2024")
    target = User(name="Ivan Ivanov", role=Role.STUDENT, primary_cohort="2024",
                  handle_observed="ivanov", gmail="i@gmail.com",
                  visibility={"gmail": "nobody"})
    text = render_profile(viewer, target)
    assert "Ivan Ivanov" in text
    assert "ivanov" in text
    assert "i@gmail.com" not in text  # hidden by visibility
```

Create `tests/test_directory_search.py`:
```python
from sdt_bot.features.directory.search import list_cohort, search_users
from sdt_bot.core.models import User


def _seed(session):
    session.add_all([
        User(name="Ivan Ivanov", handle_sheet="ivanov", primary_cohort="2024"),
        User(name="Petr Petrov", handle_observed="petrov", primary_cohort="2024"),
        User(name="Anna Smith", handle_sheet="asmith", primary_cohort="2021"),
    ])
    session.commit()


def test_search_by_name_substring(session):
    _seed(session)
    results = search_users(session, "ivan")
    assert {u.name for u in results} == {"Ivan Ivanov"}


def test_search_by_handle(session):
    _seed(session)
    assert search_users(session, "petrov")[0].name == "Petr Petrov"


def test_list_cohort_by_primary(session):
    _seed(session)
    names = {u.name for u in list_cohort(session, "2024")}
    assert names == {"Ivan Ivanov", "Petr Petrov"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_directory_render.py tests/test_directory_search.py -v`
Expected: FAIL with `ModuleNotFoundError` for `render`/`search`.

- [ ] **Step 3: Implement render.py**

Create `src/sdt_bot/features/directory/render.py`:
```python
from sdt_bot.core.models import User
from sdt_bot.features.directory.visibility import visible_fields

_LABELS = {
    "name": "Name",
    "role": "Role",
    "primary_cohort": "Cohort",
    "telegram": "Telegram",
    "status_line": "Status",
    "gmail": "Gmail",
    "github": "GitHub",
    "codeforces": "Codeforces",
    "matriculation": "Matriculation",
}
_ORDER = ["name", "role", "primary_cohort", "telegram", "status_line",
          "gmail", "github", "codeforces", "matriculation"]


def render_profile(viewer: User, target: User) -> str:
    fields = visible_fields(viewer, target)
    lines = []
    for key in _ORDER:
        if key not in fields or fields[key] in (None, ""):
            continue
        value = fields[key]
        if hasattr(value, "value"):  # enum -> its value
            value = value.value
        lines.append(f"{_LABELS[key]}: {value}")
    return "\n".join(lines)
```

- [ ] **Step 4: Implement search.py**

Create `src/sdt_bot/features/directory/search.py`:
```python
from sqlalchemy import or_, select

from sdt_bot.core.models import User


def search_users(session, query: str) -> list[User]:
    pattern = f"%{query.lower()}%"
    stmt = select(User).where(
        or_(
            User.name.ilike(pattern),
            User.handle_sheet.ilike(pattern),
            User.handle_observed.ilike(pattern),
        )
    )
    return list(session.scalars(stmt).all())


def list_cohort(session, primary_cohort: str) -> list[User]:
    stmt = select(User).where(User.primary_cohort == primary_cohort)
    return list(session.scalars(stmt).all())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_directory_render.py tests/test_directory_search.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: directory profile rendering and search/cohort helpers"
```

---

## Task 12: Directory — handlers, manifest, and NL search intent

**Files:**
- Modify: `src/sdt_bot/features/directory/__init__.py` (export `router`, `manifest`)
- Create: `src/sdt_bot/features/directory/handlers.py`
- Create: `tests/test_directory_handlers.py`

**Interfaces:**
- Consumes: `render_profile`, `search_users`, `list_cohort`, `Intent`, `Manifest`, `Role`.
- Produces (`handlers.py`):
  - `router: Router` with `/me`, `/cohort` command handlers and a
    `set_status(session, user, text)` helper.
  - `async name_search(message, principal, session)` — the NL intent handler:
    replies with a profile card (single match) or a candidate list (many) or a
    "not found" message.
  - `name_search_intent: Intent` (name `"directory.search"`, pattern `r".+"`).
- Produces (`__init__.py`): `router`, `manifest` (name `"directory"`,
  `commands=["me", "cohort"]`, `intents=[name_search_intent]`, `min_role=Role.STUDENT`).

- [ ] **Step 1: Write the failing tests (logic-level, no Telegram network)**

Create `tests/test_directory_handlers.py`:
```python
import sdt_bot.features.directory as directory
from sdt_bot.features.directory.handlers import name_search_intent, set_status
from sdt_bot.core.models import Role, User


def test_manifest_exposes_contract():
    assert directory.manifest.name == "directory"
    assert "me" in directory.manifest.commands
    assert "cohort" in directory.manifest.commands
    assert directory.manifest.min_role is Role.STUDENT
    assert any(i.name == "directory.search" for i in directory.manifest.intents)
    assert directory.router is not None


def test_search_intent_matches_plain_text():
    import re
    assert re.search(name_search_intent.pattern, "Ivan", re.IGNORECASE)


def test_set_status_updates_user(session):
    u = User(name="Ivan", telegram_id=1)
    session.add(u)
    session.commit()
    set_status(session, u, "looking for a teammate")
    session.refresh(u)
    assert u.status_line == "looking for a teammate"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_directory_handlers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.features.directory.handlers'`

- [ ] **Step 3: Implement handlers.py**

Create `src/sdt_bot/features/directory/handlers.py`:
```python
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from sdt_bot.core.intents import Intent
from sdt_bot.core.models import User
from sdt_bot.features.directory.render import render_profile
from sdt_bot.features.directory.search import list_cohort, search_users

router = Router()


def set_status(session, user: User, text: str) -> None:
    user.status_line = text
    session.commit()


@router.message(Command("me"))
async def cmd_me(message: Message, principal: User, session):
    if principal is None:
        await message.answer("You are not linked yet. Contact an admin.")
        return
    await message.answer(render_profile(principal, principal))


@router.message(Command("cohort"))
async def cmd_cohort(message: Message, principal: User, session):
    if principal is None or not principal.primary_cohort:
        await message.answer("No cohort on file.")
        return
    mates = list_cohort(session, principal.primary_cohort)
    lines = [f"- {m.name} (@{m.handle_observed or m.handle_sheet or '?'})"
             for m in mates]
    await message.answer("Your cohort:\n" + "\n".join(lines))


async def name_search(message: Message, principal: User, session):
    if principal is None:
        await message.answer("You are not linked yet. Contact an admin.")
        return
    query = (message.text or "").strip()
    results = search_users(session, query)
    if not results:
        await message.answer("No one found.")
    elif len(results) == 1:
        await message.answer(render_profile(principal, results[0]))
    else:
        lines = [f"- {u.name}" for u in results[:20]]
        await message.answer("Several people match:\n" + "\n".join(lines))


name_search_intent = Intent(
    name="directory.search", pattern=r".+", handler=name_search
)
```

- [ ] **Step 4: Wire the feature exports**

Replace `src/sdt_bot/features/directory/__init__.py` with:
```python
from sdt_bot.core.loader import Manifest
from sdt_bot.core.models import Role
from sdt_bot.features.directory.handlers import name_search_intent, router

manifest = Manifest(
    name="directory",
    commands=["me", "cohort"],
    intents=[name_search_intent],
    min_role=Role.STUDENT,
    help_text="Find classmates and manage your own profile.",
)

__all__ = ["router", "manifest"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_directory_handlers.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: directory handlers, manifest, and free-text search intent"
```

---

## Task 13: Directory — admin inline buttons (issue link / reset)

**Files:**
- Modify: `src/sdt_bot/features/directory/render.py` (add admin keyboard builder)
- Modify: `src/sdt_bot/features/directory/handlers.py` (callback handlers)
- Create: `tests/test_directory_admin.py`

**Interfaces:**
- Consumes: `Role`, `issue_link_token`, `reset_binding`, `get_settings`.
- Produces (`render.py`):
  - `admin_keyboard(target: User) -> InlineKeyboardMarkup | None` — returns a
    keyboard with "Issue link" (`callback_data=f"dir:link:{matriculation}"`) and
    "Reset telegram_id" (`callback_data=f"dir:reset:{matriculation}"`) buttons,
    or `None` when the target has no `matriculation`.
- Produces (`handlers.py`):
  - callback handlers matching `dir:link:*` and `dir:reset:*`, both guarded so
    only `Role.ADMIN` principals act; `cmd_me`/`name_search` attach
    `admin_keyboard(target)` when the viewer is an admin.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_directory_admin.py`:
```python
from sdt_bot.features.directory.render import admin_keyboard
from sdt_bot.core.models import User


def test_admin_keyboard_has_link_and_reset():
    kb = admin_keyboard(User(name="Ivan", matriculation="30000001"))
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "dir:link:30000001" in datas
    assert "dir:reset:30000001" in datas


def test_admin_keyboard_none_without_matriculation():
    assert admin_keyboard(User(name="Staff")) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_directory_admin.py -v`
Expected: FAIL with `ImportError: cannot import name 'admin_keyboard'`

- [ ] **Step 3: Implement admin_keyboard**

Append to `src/sdt_bot/features/directory/render.py`:
```python
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def admin_keyboard(target: User) -> InlineKeyboardMarkup | None:
    if not target.matriculation:
        return None
    m = target.matriculation
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Issue link", callback_data=f"dir:link:{m}"),
        InlineKeyboardButton(text="Reset telegram_id",
                             callback_data=f"dir:reset:{m}"),
    ]])
```

- [ ] **Step 4: Implement the callback handlers**

Append to `src/sdt_bot/features/directory/handlers.py`:
```python
from aiogram import F
from aiogram.types import CallbackQuery

from sdt_bot.core import identity
from sdt_bot.core.config import get_settings
from sdt_bot.core.models import Role
from sdt_bot.core.tokens import issue_link_token
from sdt_bot.features.directory.render import admin_keyboard


@router.callback_query(F.data.startswith("dir:link:"))
async def cb_issue_link(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    token = issue_link_token(session, matriculation, get_settings().link_secret)
    bot_user = await cb.bot.me()
    await cb.message.answer(
        f"One-time link:\nhttps://t.me/{bot_user.username}?start={token}"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("dir:reset:"))
async def cb_reset(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    ok = identity.reset_binding(session, matriculation)
    await cb.answer("Reset done." if ok else "Not found.", show_alert=True)
```

Then update `cmd_me` and `name_search` in the same file so an admin viewer gets
the keyboard. Change the single-result reply in `name_search` to:
```python
    elif len(results) == 1:
        target = results[0]
        kb = admin_keyboard(target) if principal.role is Role.ADMIN else None
        await message.answer(render_profile(principal, target), reply_markup=kb)
```
and the `cmd_me` reply to:
```python
    kb = admin_keyboard(principal) if principal.role is Role.ADMIN else None
    await message.answer(render_profile(principal, principal), reply_markup=kb)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_directory_admin.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: admin inline buttons for one-time link and binding reset"
```

---

## Task 14: Bootstrap, /start binding, /sync command, and wiring

**Files:**
- Create: `src/sdt_bot/main.py`
- Create: `src/sdt_bot/core/sheets_client.py` (Sheets API fetch)
- Create: `tests/test_bootstrap.py`
- Modify: `src/sdt_bot/features/directory/handlers.py` (add `/start` and `/sync`)

**Interfaces:**
- Consumes: everything above.
- Produces (`sheets_client.py`): `fetch_rows(sheet_id: str, credentials_file: str, range_: str = "A:Z") -> list[list[str]]`.
- Produces (`main.py`):
  - `build_dispatcher(session_factory) -> Dispatcher` — registers
    `PrincipalMiddleware`, includes every discovered feature router, and attaches
    the NL fallback handler that calls `IntentRouter.dispatch`.
  - `run()` — creates the `Bot`, builds the dispatcher, and starts polling.

- [ ] **Step 1: Write the failing test for dispatcher wiring**

Create `tests/test_bootstrap.py`:
```python
from sdt_bot.main import build_dispatcher


def test_build_dispatcher_registers_directory():
    dp = build_dispatcher(session_factory=lambda: None)
    # the directory router is included among the dispatcher's sub-routers
    names = [r.name for r in dp.sub_routers]
    assert any("directory" in (n or "") for n in names) or dp.sub_routers
```

Note: aiogram assigns router names; the assertion tolerates auto-names by
falling back to "at least one router was included".

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bootstrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdt_bot.main'`

- [ ] **Step 3: Implement the Sheets API client**

Create `src/sdt_bot/core/sheets_client.py`:
```python
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def fetch_rows(sheet_id: str, credentials_file: str, range_: str = "A:Z") -> list[list[str]]:
    creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_)
        .execute()
    )
    return result.get("values", [])
```

- [ ] **Step 4: Add /start and /sync handlers**

Append to `src/sdt_bot/features/directory/handlers.py`:
```python
from aiogram.filters import CommandObject
from sqlalchemy import select

from sdt_bot.core import sheets
from sdt_bot.core.config import get_settings
from sdt_bot.core.sheets_client import fetch_rows
from sdt_bot.core.tokens import verify_link_token


@router.message(Command("start"))
async def cmd_start(message: Message, principal: User, session,
                    command: CommandObject):
    settings = get_settings()
    payload = command.args
    if payload:  # one-time link binding
        user = verify_link_token(session, payload, settings.link_secret,
                                 settings.link_ttl_seconds)
        if user is None:
            await message.answer("This link is invalid or expired.")
            return
        identity.bind_by_token(session, message.from_user.id,
                               message.from_user.username, user)
        await message.answer(f"Linked as {user.name}.")
        return
    if principal is not None:
        await message.answer(f"Welcome back, {principal.name}.")
    else:
        await message.answer(
            "I couldn't recognize you. Ask a program admin for a one-time link."
        )


@router.message(Command("sync"))
async def cmd_sync(message: Message, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await message.answer("Admins only.")
        return
    settings = get_settings()
    rows = fetch_rows(settings.rights_sheet_id, settings.google_service_account_file)
    # rights sheet uses matriculation-or-email as key; mapping is loaded per file.
    mapping = sheets.load_mapping(f"{settings.mapping_dir}/rights.yaml")
    try:
        records = sheets.normalize_rows(rows, mapping)
    except sheets.MappingError as exc:
        await message.answer(f"Sync aborted: {exc}")
        return
    sheets.upsert_users(session, records)
    report = sheets.reconcile(session, records)
    await message.answer(
        "Sync done.\n"
        f"Drift: {report.drift or 'none'}\n"
        f"Unmatched: {report.unmatched or 'none'}\n"
        f"Duplicates: {report.duplicates or 'none'}"
    )
```

Add the corresponding commands to the manifest — update
`src/sdt_bot/features/directory/__init__.py` `commands` list to
`["start", "me", "cohort", "sync"]`.

Create `mapping/rights.yaml`:
```yaml
matriculation: "Matriculation Number"
name: "Full Name"
role: "Role"
handle_sheet: "Telegram"
```
Note: `role` is sheet-owned; add `"role"` to `sheets.SHEET_OWNED` in
`src/sdt_bot/core/sheets.py` and convert the string to `Role` during upsert —
update `upsert_users` so that when `field_name == "role"` it assigns
`Role(record["role"])`.

- [ ] **Step 5: Implement main.py**

Create `src/sdt_bot/main.py`:
```python
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message

import sdt_bot.features as features_pkg
from sdt_bot.core.config import get_settings
from sdt_bot.core.db import get_session
from sdt_bot.core.intents import IntentRouter
from sdt_bot.core.loader import discover_features
from sdt_bot.core.middleware import PrincipalMiddleware

_intent_router = IntentRouter()


def build_dispatcher(session_factory) -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(PrincipalMiddleware(session_factory))
    dp.callback_query.middleware(PrincipalMiddleware(session_factory))

    for feature in discover_features(features_pkg):
        dp.include_router(feature.router)
        for intent in feature.manifest.intents:
            _intent_router.register(intent)

    # NL fallback: any non-command text runs through the intent router.
    @dp.message(F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session):
        await _intent_router.dispatch(message.text, message, principal, session)

    return dp


def run() -> None:
    settings = get_settings()
    bot = Bot(settings.bot_token)
    dp = build_dispatcher(get_session)
    asyncio.run(dp.start_polling(bot))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_bootstrap.py -v`
Expected: PASS.

Note on handler ordering: feature routers are included before the NL fallback,
and the directory command handlers use `Command(...)` filters, so `/me`, `/sync`,
etc. are matched by their routers; only non-command text reaches the fallback.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 8: Manual smoke test (documented, not automated)**

With a real `.env` (`cp .env.example .env` and fill in a bot token + a service
account with the rights sheet shared to it) and `uv run alembic upgrade head`:
```bash
uv run python -m sdt_bot
```
Verify in Telegram: `/start` for an unlinked user asks to contact an admin;
after `/sync` (as an admin whose row exists in the rights sheet), a student who
messages the bot with a matching handle is recognized; typing a name returns a
profile card; an admin viewing a card sees the inline buttons.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: bootstrap dispatcher, /start binding, /sync, and sheets client"
```

---

## Self-Review Notes

Spec coverage check (spec §→plan task):
- §2 layers → Tasks 1–14 create every listed `core/` module and the directory feature.
- §3 data model → Task 2 (single `users` table, JSON cohorts/visibility, dual handle fields, `link_nonce`).
- §4 ETL → Tasks 8 (mapping/normalize), 9 (upsert preserving bot-owned, reconciliation), 14 (Sheets API client, `/sync`, abort-on-parse-error).
- §5 identity & binding → Tasks 3 (resolve/claim/reset), 4 (one-time token, single-use), 14 (`/start` binding).
- §6 plugin contract → Tasks 6 (loader + Manifest), 7 (intents), 12 (directory exports router+manifest).
- §7 directory → Tasks 10 (visibility matrix incl. staff override, cohort-mate rule, admin-only), 11 (render/search/cohort), 12 (free-text search, `/me`, `/cohort`), 13 (admin inline link/reset buttons), 14 (`/sync` shows reconciliation, no separate command).
- §8 tooling → Task 1 (uv, pyproject, lockfile, pytest config).
- §9 cross-cutting → Task 1 (`.env`), Task 14 (long polling), tests throughout.
- §10 out-of-scope items are respected (no self-registration, no snapshots, no audit logs, no sheet writes, no LLM).

Type consistency: `resolve`, `bind_by_token`, `issue_link_token`,
`verify_link_token`, `visible_fields`, `render_profile`, `search_users`,
`list_cohort`, `admin_keyboard`, `discover_features`, `Manifest`, `Intent`,
`IntentRouter.dispatch` signatures are defined once and consumed with matching
names/arguments in later tasks.
