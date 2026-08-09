from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jbcub_bot.core.config import get_settings

# Resolved from the working directory, like alembic.ini's own
# `prepend_sys_path = .`.
_ALEMBIC_INI = "alembic.ini"


class Base(DeclarativeBase):
    pass


# Built on first real use, so importing this module -- as tests do, with their
# own in-memory engine -- does not require a full .env.
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

    ``upgrade head`` does both, so a schema change needs no deployment change.

    The stamp is for databases predating migrations: they have the tables but
    no ``alembic_version``, and alembic would read that as empty and fail
    re-creating ``users``. They match one specific revision, which is what gets
    stamped -- ``head`` would skip everything since. Delete this branch once no
    such database is left.
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
