import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jbcub_bot.core.db import Base


@pytest.fixture(autouse=True)
def _reset_kb_state():
    from jbcub_bot.features.kb import handlers as kb_handlers
    from jbcub_bot.features.kb import history as kb_history
    from jbcub_bot.features.kb import pdf as kb_pdf

    def clear():
        # The runtime is off by default rather than `reset_runtime()`'s lazy
        # rebuild: that would read the real settings, and a developer's own
        # .env carrying a real KB_LLM_API_KEY would silently turn the knowledge
        # base on for every test that never asked for it. A test that wants it
        # installs its own with `set_runtime`.
        kb_handlers.set_runtime(None)
        kb_handlers.set_registry(None)
        kb_handlers.reset_rate_limit()
        kb_handlers.reset_ratings()
        kb_history.reset()
        kb_pdf.reset_cache()

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _reset_impersonation():
    from jbcub_bot.core import impersonation
    impersonation.reset()
    yield
    impersonation.reset()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as s:
        yield s
