from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import TextIO


class LockUnavailable(RuntimeError):
    pass


class ProcessLock:
    """Non-blocking process lock for the one-daily-run invariant."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._handle = handle
        return True

    def __enter__(self) -> ProcessLock:
        if not self.acquire():
            raise LockUnavailable(f"daily post-match lock is already held: {self.path}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
