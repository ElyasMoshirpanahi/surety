# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0]

The package layout is split from the single-file v0.1 core. The statistical method
is unchanged, and v0.1 `certs.json` files and ledgers load as they are.

### Added
- `src/surety/` package: `answers`, `stats`, `certify`, `gate`, `ledger`,
  `backends`, `report` and `cli` modules, with type hints and `mypy --strict`.
- Backends: the `Backend` protocol, `HttpBackend` (v0.1 `SystemOne`, still
  exported), `SdkBackend` (official typesafe-sdk), `LayaBackend` (in-process
  `laya.Router`, with the pinned model checked on every response) and `FakeBackend`.
- `surety collect`: concurrency, exponential backoff with jitter on 429/5xx and
  network errors (honours `Retry-After`), `--resume`, repair of a torn last
  line, and recording of the server's resolved model.
- `surety report`: markdown with the test at every grid threshold (n, errors,
  Clopper–Pearson bound, p-value, accepted) and reliability bins per slice.
- `surety ledger anchor`: verifies the chain, then prints the record count and tail hash.
- `Gate.load(..., ledger=...)` rebuilds the drift monitors and suspensions from the
  ledger, so a suspension survives restarts. Audit records now carry
  `certificate_id`, `question_id` and `slice`, and a `suspend` record is written
  when an alarm fires. v0.1 audit records are resolved through `decision_hash`.
- An OS file lock (`fcntl.flock` / `msvcrt.locking` on `<ledger>.lock`) around
  ledger appends, with the chain tail re-read under the lock.
- `examples/jev_vs_laya/` with a 400-row synthetic support-triage dataset.
- Monte Carlo tests of the coverage guarantee and of the drift detector's ARL.

### Changed
- The gate now fails closed with "unreadable answer" when `read_answer` can't parse
  an answer. v0.1 raised instead.
- `read_answer` rejects NaN, infinite and out-of-range probabilities. In v0.1 a
  NaN confidence compared false against the threshold and would have been automated.
- `certify` rejects calibration rows whose `question_fp` doesn't match the
  question being certified.
- `collect` and `certify` refuse moving aliases such as `jev-latest` and
  `jev-preview` unless you pass `--allow-alias`.
- `SdkBackend` always passes `base_url` explicitly, so `TYPESAFE_BASE_URL` can't
  redirect the environment's key.

## [0.1.0]

- A single-file, zero-dependency core: exact binomial certification over a fixed
  grid, a fail-closed gate, a Shiryaev–Roberts drift monitor, a hash-chained
  ledger and the `plan`/`collect`/`certify`/`verify` commands.
