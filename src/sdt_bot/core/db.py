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
