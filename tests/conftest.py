import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jbcub_bot.core.db import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as s:
        yield s
