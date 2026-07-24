from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jbcub_bot.core.config import get_settings


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
    """Create any missing tables from the models.

    Idempotent: ``create_all`` only creates tables that don't exist yet, so a
    fresh DB is built from scratch on first run and existing tables are left
    untouched on subsequent runs.
    """
    from jbcub_bot.core import models  # noqa: F401  (register models on Base)

    Base.metadata.create_all(get_engine())
