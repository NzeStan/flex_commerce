import threading
from unittest import mock

import pytest
from django.core.cache import cache
from django.db import transaction
from django.dispatch import Signal

from flexcommerce_core import events, hooks, jobs, tasks
from flexcommerce_core.signals import event as generic_event
from flexcommerce_core.testing import immediate_on_commit
from flexcommerce_core.utils.helpers import AsyncExecutor, StateMachine, atomic_lock

CALLS = []


def record(*args, **kwargs):
    CALLS.append((args, kwargs))
    return "ok"


def boom():
    raise RuntimeError("job failed")


def custom_executor(path, args, kwargs):
    CALLS.append(("custom", path, tuple(args)))


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()
    cache.clear()
    yield
    CALLS.clear()


class TestEmit:
    @pytest.mark.django_db(transaction=True)
    def test_emit_waits_for_commit_and_skips_rollback(self, fc_immediate_events):
        sig = Signal()
        got = []
        sig.connect(lambda sender, **kw: got.append(kw["value"]), weak=False)
        # Undo the immediate-commit test helper for this test.
        events.on_commit = lambda func, using=None: transaction.on_commit(func, using=using)
        try:
            with transaction.atomic():
                events.emit("x.happened", signal=sig, sender=None, value=1)
                assert got == []
            assert got == [1]
            with pytest.raises(RuntimeError):
                with transaction.atomic():
                    events.emit("x.happened", signal=sig, sender=None, value=2)
                    raise RuntimeError
            assert got == [1]
        finally:
            events.on_commit = lambda func, using=None: func()

    def test_generic_event_payload(self):
        got = []

        def receiver(sender, name, payload, **kw):
            got.append((name, payload))

        generic_event.connect(receiver, weak=False, dispatch_uid="t-generic")
        try:
            events.emit("order.created", payload={"id": 1})
        finally:
            generic_event.disconnect(dispatch_uid="t-generic")
        assert got == [("order.created", {"id": 1})]

    def test_failing_receiver_is_isolated(self, caplog):
        sig = Signal()
        got = []

        def bad(sender, **kw):
            raise ValueError("bad receiver")

        sig.connect(bad, weak=False)
        sig.connect(lambda sender, **kw: got.append(1), weak=False)
        events.emit("x", signal=sig)
        assert got == [1]
        assert "bad receiver" in caplog.text

    def test_immediate_on_commit_helper(self):
        with immediate_on_commit():
            ran = []
            events.on_commit(lambda: ran.append(1))
            assert ran == [1]


class TestHooks:
    def test_register_decorator_and_run(self):
        @hooks.register("t.hook")
        def a(x):
            return x + 1

        hooks.register("t.hook", a)  # duplicate ignored
        try:
            assert hooks.run("t.hook", 1) == [2]
        finally:
            hooks.unregister("t.hook", a)
        assert hooks.run("t.hook", 1) == []

    def test_order_and_pipeline(self):
        def double(x):
            return x * 2

        def inc(x):
            return x + 1

        hooks.register("t.pipe", double, order=10)
        hooks.register("t.pipe", inc, order=0)
        try:
            assert hooks.run_pipeline("t.pipe", 3) == 8  # (3 + 1) * 2
        finally:
            hooks.unregister("t.pipe", double)
            hooks.unregister("t.pipe", inc)

    def test_hooks_from_settings(self, fc):
        fc(HOOKS={"t.settings": ["tests.test_events_tasks_jobs.record"]})
        assert hooks.run("t.settings", 5) == ["ok"]
        assert CALLS == [((5,), {})]


class TestTasks:
    def test_sync(self, fc):
        fc(ASYNC_EXECUTOR="sync")
        assert tasks.enqueue(record, 1, a=2) == "ok"
        assert tasks.enqueue("tests.test_events_tasks_jobs.record", 3) == "ok"
        assert CALLS == [((1,), {"a": 2}), ((3,), {})]

    def test_threading(self, fc):
        fc(ASYNC_EXECUTOR="threading")
        done = threading.Event()
        with mock.patch.object(tasks, "_call", side_effect=lambda p, a, k: done.set()):
            tasks.enqueue(record, 1)
            assert done.wait(5)

    @pytest.mark.django_db
    def test_thread_runner_swallows_errors(self, caplog):
        tasks._run_in_thread("tests.test_events_tasks_jobs.boom", (), {})
        assert "failed" in caplog.text

    def test_custom_executor(self, fc):
        fc(ASYNC_EXECUTOR="tests.test_events_tasks_jobs.custom_executor")
        tasks.enqueue(record, 7)
        assert CALLS == [("custom", "tests.test_events_tasks_jobs.record", (7,))]

    def test_celery_missing_falls_back_inline(self, fc):
        fc(ASYNC_EXECUTOR="celery")
        with mock.patch.object(tasks, "run_task", None):
            assert tasks.enqueue(record, 1) == "ok"

    def test_celery_delay(self, fc):
        fc(ASYNC_EXECUTOR="celery")
        fake = mock.Mock()
        with mock.patch.object(tasks, "run_task", fake):
            tasks.enqueue(record, 1, k=2)
        fake.delay.assert_called_once_with("tests.test_events_tasks_jobs.record", [1], {"k": 2})

    def test_legacy_executor(self):
        assert AsyncExecutor("sync").run(record, 1) == "ok"
        delayed = mock.Mock()
        AsyncExecutor("celery").run(delayed, 2)
        delayed.delay.assert_called_once_with(2)
        done = threading.Event()
        AsyncExecutor("threading").run(done.set)
        assert done.wait(5)


class TestJobs:
    def setup_method(self):
        self._saved = dict(jobs._jobs)
        jobs._jobs.clear()

    def teardown_method(self):
        jobs._jobs.clear()
        jobs._jobs.update(self._saved)

    def test_runs_due_jobs_once_per_interval(self):
        jobs.register_job("t.record", "tests.test_events_tasks_jobs.record", 10, "desc")
        assert jobs.run_due_jobs() == {"t.record": "ok"}
        assert jobs.run_due_jobs() == {}
        assert jobs.run_due_jobs(force=True) == {"t.record": "ok"}
        assert "t.record" in jobs.get_jobs()

    def test_failing_job_does_not_stop_others(self):
        jobs.register_job("a.boom", "tests.test_events_tasks_jobs.boom", 1)
        jobs.register_job("b.ok", "tests.test_events_tasks_jobs.record", 1)
        results = jobs.run_due_jobs()
        assert isinstance(results["a.boom"], RuntimeError)
        assert results["b.ok"] == "ok"

    def test_only(self):
        jobs.register_job("a", "tests.test_events_tasks_jobs.record", 1)
        jobs.register_job("b", "tests.test_events_tasks_jobs.record", 1)
        assert list(jobs.run_due_jobs(only=["b"])) == ["b"]

    @pytest.mark.django_db
    def test_management_command(self):
        from io import StringIO

        from django.core.management import call_command

        jobs.register_job("c.ok", "tests.test_events_tasks_jobs.record", 1, "a job")
        jobs.register_job("c.bad", "tests.test_events_tasks_jobs.boom", 1)
        out = StringIO()
        call_command("flexcommerce_run_jobs", "--list", stdout=out)
        assert "c.ok" in out.getvalue() and "a job" in out.getvalue()
        out = StringIO()
        call_command("flexcommerce_run_jobs", stdout=out)
        assert "c.ok: ok" in out.getvalue() and "c.bad" in out.getvalue()
        out = StringIO()
        call_command("flexcommerce_run_jobs", stdout=out)
        assert "No jobs were due" in out.getvalue()


class Machine(StateMachine):
    TRANSITIONS = {"a": ["b"], "b": []}

    def on_transition(self, from_state, to_state, **kwargs):
        CALLS.append((from_state, to_state))


class TestStateMachine:
    def test_transitions(self):
        obj = type("Obj", (), {"status": "a"})()
        sm = Machine(obj)
        assert sm.allowed_transitions() == ["b"]
        sm.transition("b", save=False)
        assert obj.status == "b"
        assert CALLS == [("a", "b")]

    def test_invalid(self):
        from flexcommerce_core.exceptions import InvalidOrderTransitionError

        obj = type("Obj", (), {"status": "b"})()
        with pytest.raises(InvalidOrderTransitionError) as exc:
            Machine(obj).transition("a", save=False)
        assert exc.value.extra == {"from": "b", "to": "a", "allowed": []}

    @pytest.mark.django_db
    def test_atomic_lock(self):
        from tests.models import SoftItem

        item = SoftItem.objects.create()
        with atomic_lock(SoftItem.objects.filter(pk=item.pk)) as qs:
            assert qs.first() == item
