"""
Background execution for FlexCommerce.

``enqueue("pkg.module.function", *args)`` runs a module-level function using
the executor configured in ``FLEXCOMMERCE["ASYNC_EXECUTOR"]``:

* ``"sync"``      – call inline (default; simplest, fine for small shops)
* ``"threading"`` – run in a daemon thread (DB connection closed afterwards)
* ``"celery"``    – send the ``flexcommerce.run`` Celery task (recommended at scale)
* dotted path     – your own callable ``f(path, args, kwargs)`` (RQ, Dramatiq, Huey...)

Arguments must be JSON-serialisable (pass primary keys, not model instances).
Deliveries that need retries (notifications, webhooks) keep their own retry
state in the database, so a lost task is recovered by ``flexcommerce_run_jobs``.

With Celery, make sure ``app.autodiscover_tasks()`` is called so this module is
imported by the worker.
"""

import logging
import threading

from django.db import close_old_connections
from django.utils.module_loading import import_string

logger = logging.getLogger("flexcommerce.tasks")


def _call(path, args, kwargs):
    return import_string(path)(*args, **(kwargs or {}))


def _run_in_thread(path, args, kwargs):
    try:
        _call(path, args, kwargs)
    except Exception:
        logger.exception("FlexCommerce background task %s failed", path)
    finally:
        close_old_connections()


def _path_of(func_or_path):
    if isinstance(func_or_path, str):
        return func_or_path
    return f"{func_or_path.__module__}.{func_or_path.__qualname__}"


def enqueue(func_or_path, *args, **kwargs):
    from .conf import fc_setting

    path = _path_of(func_or_path)
    backend = fc_setting("ASYNC_EXECUTOR", "sync") or "sync"

    if backend == "sync":
        return _call(path, args, kwargs)
    if backend == "threading":
        threading.Thread(target=_run_in_thread, args=(path, args, kwargs), daemon=True).start()
        return None
    if backend == "celery":
        if run_task is None:
            logger.error("ASYNC_EXECUTOR='celery' but Celery is not installed; running %s inline.", path)
            return _call(path, args, kwargs)
        run_task.delay(path, list(args), kwargs)
        return None
    # Custom executor: dotted path to callable(path, args, kwargs)
    import_string(backend)(path, list(args), kwargs)
    return None


try:  # pragma: no cover - exercised only when Celery is installed
    from celery import shared_task

    @shared_task(name="flexcommerce.run", ignore_result=True)
    def run_task(path, args=None, kwargs=None):
        return _call(path, args or [], kwargs or {})

    @shared_task(name="flexcommerce.run_jobs", ignore_result=True)
    def run_jobs_task(force=False):
        from .jobs import run_due_jobs

        return run_due_jobs(force=force)

except ImportError:  # Celery is optional
    run_task = None
    run_jobs_task = None
