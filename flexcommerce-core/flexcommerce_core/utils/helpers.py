"""Concurrency helpers, plugin registry and state machine for FlexCommerce."""

import logging
import threading
from contextlib import contextmanager

from django.db import close_old_connections, transaction
from django.utils.module_loading import import_string

from .products import get_product_models, load_model  # noqa: F401  (re-exported)

logger = logging.getLogger("flexcommerce.core")

# ── Concurrency ───────────────────────────────────────────────────────────────


@contextmanager
def atomic_lock(qs, nowait=False):
    """
    ``select_for_update`` wrapper inside an atomic block::

        with atomic_lock(MyModel.objects.filter(pk=pk)) as locked_qs:
            obj = locked_qs.first()
    """
    with transaction.atomic():
        yield qs.select_for_update(nowait=nowait)


# ── Async Executor (compatibility wrapper) ───────────────────────────────────────


class AsyncExecutor:
    """
    Runs a callable with the configured backend. Prefer
    ``flexcommerce_core.tasks.enqueue`` (works with Celery for any function).
    """

    def __init__(self, backend=None):
        self._backend = backend

    @property
    def backend(self):
        from ..conf import fc_setting

        return self._backend or fc_setting("ASYNC_EXECUTOR", "sync")

    def run(self, func, *args, **kwargs):
        if self.backend == "celery" and hasattr(func, "delay"):
            return func.delay(*args, **kwargs)
        if self.backend in ("threading", "celery"):

            def _target():
                try:
                    func(*args, **kwargs)
                except Exception:
                    logger.exception("Background task %r failed", func)
                finally:
                    close_old_connections()

            threading.Thread(target=_target, daemon=True).start()
            return None
        return func(*args, **kwargs)


executor = AsyncExecutor()


# ── Plugin Registry ───────────────────────────────────────────────────────────


class HandlerRegistry:
    """
    Registry for swappable strategy handlers. Resolution order:

    1. ``registry.register(key, cls)`` (explicit, e.g. in tests or AppConfig.ready)
    2. ``FLEXCOMMERCE[key] = "dotted.path.Class"``
    3. ``registry.set_default(key, cls)`` (provided by FlexCommerce packages)
    """

    def __init__(self):
        self._registry = {}
        self._defaults = {}
        self._settings_cache = {}

    def register(self, key: str, handler_class):
        self._registry[key] = handler_class
        logger.debug("FlexCommerce: registered handler %s -> %s", key, handler_class)

    def unregister(self, key: str):
        self._registry.pop(key, None)

    def set_default(self, key: str, handler_class):
        self._defaults[key] = handler_class

    def get_class(self, key: str, default=None):
        from ..conf import fc_setting

        if key in self._registry:
            return self._registry[key]
        path = fc_setting(key)
        if path:
            if isinstance(path, str):
                if path not in self._settings_cache:
                    self._settings_cache[path] = import_string(path)
                return self._settings_cache[path]
            return path
        if key in self._defaults:
            return self._defaults[key]
        return default

    def get(self, key: str, default=None):
        """Return an *instance* of the handler registered for ``key``."""
        cls = self.get_class(key, default)
        if cls is None:
            raise KeyError(f"No handler registered for '{key}'")
        return cls()


registry = HandlerRegistry()


# ── State Machine Base ────────────────────────────────────────────────────────


class StateMachine:
    """
    Simple state machine. Subclasses define
    ``TRANSITIONS = {from_state: [allowed_to_states]}``.
    """

    TRANSITIONS = {}

    def __init__(self, instance, state_field="status"):
        self.instance = instance
        self.state_field = state_field

    @property
    def current_state(self):
        return getattr(self.instance, self.state_field)

    def allowed_transitions(self):
        return list(self.TRANSITIONS.get(self.current_state, []))

    def can_transition(self, to_state: str) -> bool:
        return to_state in self.TRANSITIONS.get(self.current_state, [])

    def transition(self, to_state: str, save=True, **kwargs):
        from ..exceptions import InvalidOrderTransitionError

        if not self.can_transition(to_state):
            raise InvalidOrderTransitionError(
                f"Cannot transition from '{self.current_state}' to '{to_state}'.",
                extra={
                    "from": self.current_state,
                    "to": to_state,
                    "allowed": self.allowed_transitions(),
                },
            )
        old_state = self.current_state
        setattr(self.instance, self.state_field, to_state)
        if save:
            self.instance.save(update_fields=[self.state_field, "updated_at"])
        self.on_transition(old_state, to_state, **kwargs)
        return self.instance

    def on_transition(self, from_state: str, to_state: str, **kwargs):
        """Override in subclasses to hook into transitions."""
