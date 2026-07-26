from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jbcub_bot.core.config import get_settings

# Resolved from the working directory, like the `mapping_dir` setting and
# alembic.ini's own `prepend_sys_path = .`.
_ALEMBIC_INI = "alembic.ini"


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


def init_db() -> None:
    """Bring the schema up to date, creating it from scratch when absent.

    ``upgrade head`` builds a fresh database and applies anything added since
    the last deploy, so a schema change needs no deployment change. Databases
    created by the older ``create_all`` have the tables but no
    ``alembic_version``; alembic would read those as empty and fail trying to
    re-create ``users``, so they are stamped. Databases from ``create_all``
    have exactly the schema of revision c72c6d99f0c1, so that specific revision
    is stamped rather than the moving ``head`` alias; this ensures later
    migrations apply normally instead of being skipped.

    The legacy-stamp branch is one-shot: it exists only to migrate databases
    created before this project had migrations, and can be deleted once the
    remaining pre-migration databases have been stamped.
    """
    inspector = inspect(get_engine())
    ini_path = Path(_ALEMBIC_INI).resolve()
    if not ini_path.is_file():
        raise RuntimeError(
            f"alembic.ini not found at {ini_path}; the bot must be run from "
            "the repository root."
        )
    config = Config(str(ini_path))
    # The bot already configured logging (and aiogram's loggers exist by now);
    # alembic/env.py must not run fileConfig() and disable them.
    config.attributes["configure_logger"] = False
    if inspector.has_table("users") and not inspector.has_table("alembic_version"):
        command.stamp(config, "c72c6d99f0c1")
    command.upgrade(config, "head")
