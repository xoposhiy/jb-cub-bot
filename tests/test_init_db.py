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
        # Assert the stamped revision is explicit, not the moving head alias
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "c72c6d99f0c1"


def test_init_db_is_idempotent(db_path):
    db.init_db()
    db.init_db()  # a second run on an up-to-date DB is a no-op

    assert inspect(db.get_engine()).has_table("users")
