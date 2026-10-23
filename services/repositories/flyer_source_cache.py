"""Bounded, disposable disk cache for immutable flyer sources (never authorization)."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
import fcntl
import logging
import os
import tempfile
import time

logger = logging.getLogger(__name__)


class FlyerSourceIntegrityError(ValueError):
    """Downloaded source does not match its server-recorded SHA-256."""


class FlyerSourceCache:
    def __init__(self, root: Path, max_bytes: int, ttl_seconds: int):
        self._root, self._max_bytes, self._ttl = root, max_bytes, ttl_seconds

    @contextmanager
    def lease(self, source: str, file_hash: str, populate: Callable[[Path], None]) -> Iterator[Path]:
        key = sha256(f"{source}:{file_hash}".encode()).hexdigest()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._root / f"{key}.bin"
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            hit = self._valid(path, file_hash)
            if not hit:
                self._populate(path, file_hash, populate)
            logger.info("Flyer source cache %s key=%s bytes=%s", "hit" if hit else "miss", key[:12], path.stat().st_size)
            os.utime(path, None)
            self._prune()
            try:
                yield path
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                self._prune()

    def _valid(self, path: Path, file_hash: str) -> bool:
        if not path.exists() or time.time() - path.stat().st_mtime > self._ttl:
            return False
        with path.open("rb") as source:
            return file_digest(source, "sha256").hexdigest() == file_hash

    def _populate(self, path: Path, file_hash: str, populate: Callable[[Path], None]) -> None:
        with (self._root / "budget.lock").open("a") as budget:
            fcntl.flock(budget, fcntl.LOCK_EX)
            fd, name = tempfile.mkstemp(dir=self._root, suffix=".part")
            os.close(fd)
            temporary = Path(name)
            try:
                populate(temporary)
                if not self._valid(temporary, file_hash):
                    raise FlyerSourceIntegrityError("Flyer source does not match its stored fingerprint")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

    def _prune(self) -> None:
        with (self._root / "budget.lock").open("a") as budget:
            fcntl.flock(budget, fcntl.LOCK_EX)
            files = sorted(self._root.glob("*.bin"), key=lambda path: path.stat().st_mtime)
            total = sum(path.stat().st_size for path in files)
            for path in files:
                if total > self._max_bytes or time.time() - path.stat().st_mtime > self._ttl:
                    total -= self._evict(path)
            for path in self._root.glob("*.part"):
                if time.time() - path.stat().st_mtime > self._ttl:
                    path.unlink(missing_ok=True)

    def _evict(self, path: Path) -> int:
        with path.with_suffix(".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0  # Active readers retain their source until lease release.
            size = path.stat().st_size
            path.unlink()
            return size
