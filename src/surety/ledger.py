"""Append-only, hash-chained JSONL evidence log."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from .answers import canon, sha

GENESIS = "0" * 64


@contextmanager
def _locked(path: Path, timeout: float) -> Iterator[None]:
    """Exclusive OS lock on `<ledger>.lock`, released by the OS if the process dies.

    A sidecar file is locked rather than the ledger itself so readers (verify,
    replay) never block and never trip over Windows' mandatory byte locks.
    """
    lock = (path.parent / (path.name + ".lock")).open("a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _try_lock(lock)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"could not lock {path} within {timeout}s") from None
                time.sleep(0.005)
        try:
            yield
        finally:
            _unlock(lock)
    finally:
        lock.close()


if sys.platform == "win32":
    import msvcrt

    def _try_lock(f: IO[bytes]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f: IO[bytes]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(f: IO[bytes]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f: IO[bytes]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _last_line(path: Path, chunk: int = 8192) -> bytes | None:
    """The last non-empty line, read backwards so appends stay O(1) in ledger size."""
    if not path.exists():
        return None
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos, buf = f.tell(), b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            lines = [ln for ln in buf.splitlines() if ln.strip()]
            if len(lines) > 1 or (lines and pos == 0):
                return lines[-1]
    return None


class Ledger:
    """Append-only, hash-chained JSONL evidence log.

    Stores a SHA-256 of the state, never the state itself. Tamper-evident, not
    tamper-proof: anchor the latest hash (`surety ledger anchor`) somewhere you
    do not control alone. Safe for several processes on one machine: each
    append takes an OS file lock and re-reads the chain tail under it.
    """

    def __init__(self, path: str | Path, *, lock_timeout: float = 10.0, fsync: bool = True):
        self.path = Path(path)
        self.lock_timeout = lock_timeout
        self.fsync = fsync
        self.prev = self._tail()

    def _tail(self) -> str:
        line = _last_line(self.path)
        return GENESIS if line is None else str(json.loads(line)["hash"])

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        with _locked(self.path, self.lock_timeout):
            prev = self._tail()  # another process may have appended since we last looked
            rec = {**record, "ts": time.time(), "prev": prev}
            rec["hash"] = sha(rec)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(canon(rec) + "\n")
                f.flush()
                if self.fsync:
                    os.fsync(f.fileno())
        self.prev = rec["hash"]
        return rec

    @staticmethod
    def records(path: str | Path) -> Iterator[dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            return
        with p.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    @staticmethod
    def verify(path: str | Path) -> tuple[bool, int]:
        """(intact, records checked before the first break)."""
        prev, n = GENESIS, 0
        with Path(path).open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    claimed = rec.pop("hash")
                except (ValueError, KeyError, TypeError, AttributeError):
                    return False, n
                if rec.get("prev") != prev or sha(rec) != claimed:
                    return False, n
                prev, n = claimed, n + 1
        return True, n

    @staticmethod
    def anchor(path: str | Path) -> tuple[int, str]:
        """(record count, tail hash) after verifying the chain. Publish the hash somewhere external."""
        ok, n = Ledger.verify(path)
        if not ok:
            raise ValueError(f"ledger {path} is broken after {n} records; refusing to anchor it")
        line = _last_line(Path(path))
        return n, GENESIS if line is None else str(json.loads(line)["hash"])
