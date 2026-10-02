"""
Test helpers for projects that use FlexCommerce.

``pytest`` users can enable the fixtures below by adding to ``conftest.py``::

    pytest_plugins = ["flexcommerce_core.testing"]

* ``fc_immediate_events`` (autouse) – run post-commit events immediately.
  Tests run inside a transaction that never commits, so without this no
  signal emitted through ``flexcommerce_core.events.emit`` would ever fire.
* ``fc`` – update ``FLEXCOMMERCE`` settings for one test: ``fc(VAT_RATE=0.05)``.
* ``fc_clear_cache`` (autouse) – clear the default cache around each test so
  throttling counters, cached flash sales etc. never leak between tests.
"""

from contextlib import contextmanager

try:
    import pytest
except ImportError:  # pragma: no cover
    pytest = None


@contextmanager
def immediate_on_commit():
    from . import events

    original = events.on_commit
    events.on_commit = lambda func, using=None: func()
    try:
        yield
    finally:
        events.on_commit = original


if pytest is not None:

    @pytest.fixture(autouse=True)
    def fc_immediate_events():
        with immediate_on_commit():
            yield

    @pytest.fixture(autouse=True)
    def fc_clear_cache():
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    @pytest.fixture
    def fc(settings):
        base = dict(getattr(settings, "FLEXCOMMERCE", None) or {})

        def _apply(**overrides):
            settings.FLEXCOMMERCE = {**base, **overrides}
            return settings.FLEXCOMMERCE

        return _apply
