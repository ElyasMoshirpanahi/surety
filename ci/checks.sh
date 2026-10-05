#!/usr/bin/env bash
# The `test` job of .github/workflows/ci.yml, step for step.
set -euo pipefail

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "ruff check";        ruff check .
step "ruff format";       ruff format --check .
step "mypy (strict)";     mypy
step "pytest (fast)";     pytest -m "not slow" -q
step "surety --help";     surety --help >/dev/null
step "example (offline)"; python examples/jev_vs_laya/run.py --fake
