import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import jbcub_bot.features as features_pkg
from jbcub_bot.core.db import Base
from jbcub_bot.core.loader import discover_features


@pytest.fixture(autouse=True)
def _reset_feature_routers():
    # Feature routers are module-level singletons; aiogram forbids re-attaching
    # an already-parented Router, so build_dispatcher() can only be called once
    # per process unless we detach them first. Reset before every test so any
    # number of build_dispatcher() calls succeed regardless of test order.
    for feature in discover_features(features_pkg):
        feature.router._parent_router = None
    yield


@pytest.fixture(autouse=True)
def _reset_kb_runtime():
    from jbcub_bot.features.kb import handlers as kb_handlers
    from jbcub_bot.features.kb import pdf as kb_pdf
    # Off by default rather than `reset_runtime()`'s lazy rebuild: that would
    # read the real settings, and a developer's own .env carrying a real
    # KB_LLM_API_KEY would silently turn the knowledge base on for every test
    # that never asked for it. A test that wants it installs its own with
    # `set_runtime`.
    kb_handlers.set_runtime(None)
    kb_handlers.reset_pending()
    kb_handlers.reset_rate_limit()
    kb_pdf.reset_cache()
    yield
    kb_handlers.set_runtime(None)
    kb_handlers.reset_pending()
    kb_handlers.reset_rate_limit()
    kb_pdf.reset_cache()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as s:
        yield s
