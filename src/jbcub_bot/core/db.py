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
    re-create ``users``, so they are stamped at head first.
    """
    inspector = inspect(get_engine())
    config = Config(str(Path(_ALEMBIC_INI).resolve()))
    if inspector.has_table("users") and not inspector.has_table("alembic_version"):
        command.stamp(config, "head")
    command.upgrade(config, "head")
