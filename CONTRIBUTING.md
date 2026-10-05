# Contributing

Thanks for helping. A few rules keep the guarantee honest.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before you open a pull request

```bash
ruff check . && ruff format --check . && mypy
pytest -m "not slow"
pytest -m slow          # required if you touch stats.py, certify.py or gate.py
```

Or run the whole CI pipeline in Docker, the same jobs as GitHub Actions:
`bash ci/local.sh all`. See the header of `ci/local.sh` for the options.

- **Zero runtime dependencies.** The core runs on the standard library. Optional
  integrations go behind an extra (`[sdk]`, `[laya]`) and are imported lazily.
- **No network in the default test run.** A fixture blocks sockets. Use
  `FakeBackend` or monkeypatch `urllib.request.urlopen`.
- **Never weaken a statistical test to make it pass.** If a simulation in
  `tests/test_simulation.py` fails, the method or the code is wrong. Open an issue
  instead.
- **Method changes need a written argument.** Anything that changes which
  threshold gets certified (the grid, the test, the error definition, the
  confidence definition) must update `docs/method.md` with the proof sketch. It
  also needs a CHANGELOG entry, because it invalidates existing certificates.
- **Fail closed.** Any new reason the gate can't vouch for a decision must
  return REVIEW, never raise past the caller or automate.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`).

## Reporting bugs

Include the `surety --version` output, the command you ran, and, if you can,
a small synthetic dataset that reproduces it. Please don't attach real
customer data.
