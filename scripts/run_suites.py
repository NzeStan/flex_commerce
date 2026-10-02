"""
Run every package's test-suite plus the integration suite (portable: CI, Linux, macOS, Windows).

    python scripts/run_suites.py                 # all packages + integration
    python scripts/run_suites.py cart orders     # selected packages
    python scripts/run_suites.py --no-integration
    FC_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/postgres python scripts/run_suites.py

Each package runs in its own process with only its own test settings, which
proves every app works with just its declared dependencies installed.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = [
    "core",
    "catalog",
    "pricing",
    "inventory",
    "discounts",
    "shipping",
    "cart",
    "orders",
    "payments",
    "checkout",
    "engagement",
    "marketplace",
    "notifications",
    "analytics",
]


def pythonpath(extra=()):
    paths = [str(p) for p in extra] + [str(ROOT / f"flexcommerce-{name}") for name in PACKAGES]
    paths.append(str(ROOT / "flexcommerce-meta"))
    return os.pathsep.join(paths)


def run(cwd, extra_path, args):
    env = {**os.environ, "PYTHONPATH": pythonpath(extra_path)}
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args]
    return subprocess.call(cmd, cwd=cwd, env=env)


def main(argv):
    integration = "--no-integration" not in argv
    selected = [a for a in argv if not a.startswith("--")] or PACKAGES
    failures = []
    for name in selected:
        print(f"\n=== flexcommerce-{name}", flush=True)
        pkg = ROOT / f"flexcommerce-{name}"
        if run(pkg, [pkg], ["-q"]) != 0:
            failures.append(name)
    if integration and selected == PACKAGES:
        print("\n=== integration", flush=True)
        if run(ROOT / "integration_tests", [ROOT], ["-q", "-c", "pytest.ini", "--rootdir", "."]) != 0:
            failures.append("integration")
    if failures:
        print(f"\nFAILED: {', '.join(failures)}")
        return 1
    print("\nAll suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
