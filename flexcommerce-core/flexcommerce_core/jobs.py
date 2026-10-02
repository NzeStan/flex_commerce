"""
Periodic maintenance jobs.

Each installed package registers its jobs in ``AppConfig.ready()``. Run them all
with one cron line (every 5 minutes is a good default)::

    */5 * * * * python manage.py flexcommerce_run_jobs

or with Celery beat by scheduling the ``flexcommerce.run_jobs`` task.
A job only runs when its interval has elapsed. The slot is claimed atomically
with ``cache.add`` so concurrent runners never run the same job twice — use a
shared cache (Redis/Memcached) when you run more than one server.
``--force`` ignores the interval.
"""

import logging
from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone
from django.utils.module_loading import import_string

logger = logging.getLogger("flexcommerce.jobs")

_jobs = {}


@dataclass(frozen=True)
class Job:
    name: str
    path: str
    interval_minutes: int
    description: str = ""


def register_job(name, path, interval_minutes, description=""):
    _jobs[name] = Job(name, path, int(interval_minutes), description)


def get_jobs():
    return dict(_jobs)


def _cache_key(name):
    return f"flexcommerce:job:last_run:{name}"


def run_due_jobs(force=False, only=None, stdout=None):
    """Run every due job. Returns ``{name: result_or_error}``."""
    now = timezone.now()
    results = {}
    for name, job in sorted(_jobs.items()):
        if only and name not in only:
            continue
        timeout = max(job.interval_minutes * 60, 1)
        if force:
            cache.set(_cache_key(name), now, timeout=timeout)
        elif not cache.add(_cache_key(name), now, timeout=timeout):
            # Ran within the interval (or another runner claimed it just now).
            continue
        try:
            results[name] = import_string(job.path)()
            logger.info("FlexCommerce job %s finished: %s", name, results[name])
        except Exception as exc:  # keep running the other jobs
            logger.exception("FlexCommerce job %s failed", name)
            results[name] = exc
        if stdout is not None:
            stdout.write(f"{name}: {results[name]}")
    return results
