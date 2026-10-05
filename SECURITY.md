# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[security advisory form](https://github.com/ElyasMoshirpanahi/surety/security/advisories/new)
rather than in a public issue. We aim to acknowledge reports within five
working days.

## What counts

Beyond ordinary code bugs, these are security issues for this project:

- **A decision automated when it should not be.** Any path through `Gate.decide`
  that returns AUTOMATE without a current, matching, unsuspended certificate and
  a confidence at or above its threshold.
- **Credential leaks.** The `TYPESAFE_API_KEY` environment variable reaching any host
  other than `https://api.typesafe.ai`, or keys appearing in ledgers, reports or logs.
- **Ledger forgery.** Any edit, deletion or reordering of ledger records that
  `surety verify` doesn't detect.
- **Statistical invalidity.** A reproducible case where certificates are
  violated more often than δ under the stated assumptions.

## Scope notes

- The ledger is tamper-*evident*, not tamper-proof. Someone with write access can
  rewrite the whole chain. Publish `surety ledger anchor` output somewhere you
  don't control alone to detect that.
- The ledger stores a SHA-256 of each state, never the state. A low-entropy state
  (a short yes/no string, say) can be brute-forced from its hash. Don't treat the
  hash as anonymisation.
- File locking protects writers on one machine. Network filesystems may not honour
  OS locks, so use one writer per ledger there.
