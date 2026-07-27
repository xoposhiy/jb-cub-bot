import logging

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from jbcub_bot.core import db
from jbcub_bot.core.config import get_settings

# The revision whose schema a pre-migration `create_all` produced, and which
# init_db() therefore stamps onto such a database.
LEGACY_REVISION = "c72c6d99f0c1"


def _make_legacy_database() -> None:
    """Build a database exactly as the pre-migration `create_all` left it.

    Runs the one migration that describes that schema and then removes
    alembic_version, rather than calling `Base.metadata.create_all`: the model
    has moved on since, so create_all would produce a schema *newer* than the
    revision init_db stamps and the pending migrations would find nothing to
    migrate.
    """
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    command.upgrade(config, LEGACY_REVISION)
    with db.get_engine().begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))


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


def test_migrations_produce_exactly_the_columns_the_model_declares(db_path):
    # A model change without a migration takes the bot down at boot, where the
    # only symptom is a failing query far from the edit that caused it.
    from jbcub_bot.core.models import User

    db.init_db()

    columns = {c["name"] for c in inspect(db.get_engine()).get_columns("users")}
    assert columns == {c.name for c in User.__table__.columns}


def test_init_db_stamps_a_legacy_create_all_database(db_path):
    # The pre-migration world: tables exist, alembic_version does not.
    _make_legacy_database()
    with db.get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, role, last_name, first_name, past_cohorts,"
            " visibility) VALUES (1, 'Student', 'Ivanov', 'Ivan', '[]', '{}')"
        ))

    db.init_db()  # must not fail trying to re-create `users`

    inspector = inspect(db.get_engine())
    assert inspector.has_table("alembic_version")
    with db.get_engine().connect() as conn:
        assert conn.execute(text("SELECT last_name FROM users")).scalar() == "Ivanov"


def test_init_db_stamps_the_explicit_revision_not_head(db_path, monkeypatch):
    """init_db must stamp legacy databases at the explicit revision id.

    Stamping "head" instead would mark every later migration as already
    applied, so a legacy database would keep the schema it had. Capturing the
    literal argument passed to command.stamp says that directly, where
    inspecting the resulting schema would only say it indirectly.
    """
    _make_legacy_database()

    stamped_revisions = []
    real_stamp = db.command.stamp

    def recording_stamp(config, revision):
        stamped_revisions.append(revision)
        return real_stamp(config, revision)

    monkeypatch.setattr(db.command, "stamp", recording_stamp)

    db.init_db()

    assert stamped_revisions == [LEGACY_REVISION]


def test_init_db_is_idempotent(db_path):
    db.init_db()
    db.init_db()  # a second run on an up-to-date DB is a no-op

    assert inspect(db.get_engine()).has_table("users")


def test_init_db_does_not_disable_pre_existing_loggers(db_path):
    """command.upgrade runs alembic/env.py, which calls fileConfig() with its
    default disable_existing_loggers=True; that would silently disable every
    logger created before init_db() runs (e.g. aiogram's), unless env.py
    honors the configure_logger=False that init_db() sets on the Config.

    A plain named logger stands in for aiogram's so this doesn't depend on
    aiogram's internals.
    """
    logger = logging.getLogger("aiogram")
    logger.disabled = False

    db.init_db()

    assert logger.disabled is False


def test_init_db_raises_a_clear_error_when_alembic_ini_is_missing(db_path, monkeypatch):
    # init_db() resolves alembic.ini relative to the CWD by design; running
    # it from the wrong directory should say so, not surface alembic's
    # generic "no script_location" CommandError.
    monkeypatch.chdir(db_path.parent)

    with pytest.raises(RuntimeError, match="repository root"):
        db.init_db()
