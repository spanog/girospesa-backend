"""Bounded, lazy checkout for synchronous PostgreSQL workers."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from typing import Protocol

from psycopg2.extensions import connection


class ConnectionPoolPort(Protocol):
    def getconn(self) -> connection: ...
    def putconn(self, value: connection) -> None: ...


class PostgresConnectionPool:
    def __init__(self, factory: Callable[[], ConnectionPoolPort], capacity: int) -> None:
        self._factory = factory
        self._slots = BoundedSemaphore(capacity)
        self._initialization = Lock()
        self._pool: ConnectionPoolPort | None = None

    def _initialized_pool(self) -> ConnectionPoolPort:
        with self._initialization:
            if self._pool is None:
                self._pool = self._factory()
            return self._pool

    @contextmanager
    def connection(self) -> Iterator[connection]:
        # Wait on a worker, never exhaust the pool or block the ASGI event loop.
        with self._slots:
            pool = self._initialized_pool()
            value = pool.getconn()
            try:
                yield value
            finally:
                pool.putconn(value)
