# Contributing

Thank you for helping make FlexCommerce better.

## Layout

```
flexcommerce-<app>/            one PyPI distribution per app
    flexcommerce_<app>/        the Django app
    tests/                     tests that run with only this app's dependencies
flexcommerce-meta/             the "flexcommerce" meta-package
integration_tests/             every app installed together (journeys, concurrency, platform checks)
scripts/                       dev tooling (test runner, packaging generator, smoke test)
docs/                          documentation
```

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install "Django>=5.2" djangorestframework pytest pytest-django pytest-timeout pytest-cov ruff build twine
```

## Tests

```bash
python scripts/run_suites.py                    # all 14 package suites + integration suite
python scripts/run_suites.py cart checkout      # selected packages
FC_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres python scripts/run_suites.py
```

Concurrency tests only run on PostgreSQL. CI runs Python 3.10–3.13 × Django 4.2 / 5.2 / 6.0 on PostgreSQL.

## Rules

- `ruff check .` and `ruff format --check .` must pass.
- New behaviour needs tests; bug fixes need a test that fails without the fix.
- Model changes need migrations: `python -m django makemigrations <app> --settings=tests.settings` from the
  package directory. Tests fail if migrations are missing.
- Package metadata comes from `scripts/gen_packaging.py` — edit it, then run it; never hand-edit `pyproject.toml`.
- Money is `Decimal`, never `float`. State changes go through services, not views.
- Cross-app side effects use post-commit signals or hooks — never direct imports of optional apps without an
  `is_installed()` check.

## Releasing

1. Bump `VERSION` in `scripts/gen_packaging.py` and every `__version__`, run the generator, update `CHANGELOG.md`.
2. Tag `vX.Y.Z` and push; the release workflow builds, checks and publishes every package to PyPI.
