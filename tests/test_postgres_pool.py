from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock

import pytest
from core.postgres_pool import PostgresConnectionPool


def test_waits_at_capacity_and_reuses_one_pool():
    pool = MagicMock()
    pool.getconn.return_value = object()
    factory = MagicMock(return_value=pool)
    bounded = PostgresConnectionPool(factory, 1)
    waiting, acquired = Event(), Event()
    def second_checkout():
        waiting.set()
        with bounded.connection(): acquired.set()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with bounded.connection():
            future = executor.submit(second_checkout)
            assert waiting.wait(1)
            assert not acquired.is_set()
        future.result(timeout=1)
    factory.assert_called_once()
    assert acquired.is_set()
    assert pool.getconn.call_count == pool.putconn.call_count == 2


def test_failed_checkout_releases_capacity():
    pool = MagicMock()
    pool.getconn.side_effect = [ValueError("closed"), object()]
    bounded = PostgresConnectionPool(lambda: pool, 1)
    with pytest.raises(ValueError, match="closed"):
        with bounded.connection(): pass
    with bounded.connection(): pass
    pool.putconn.assert_called_once()


def test_body_failure_returns_connection_and_capacity():
    pool = MagicMock()
    bounded = PostgresConnectionPool(lambda: pool, 1)
    with pytest.raises(ValueError, match="query"):
        with bounded.connection(): raise ValueError("query")
    with bounded.connection(): pass
    assert pool.putconn.call_count == 2


def test_concurrent_first_use_initializes_pool_once():
    from threading import Barrier
    pool = MagicMock()
    factory = MagicMock(return_value=pool)
    bounded = PostgresConnectionPool(factory, 3)
    ready = Barrier(3)
    def checkout():
        ready.wait(timeout=1)
        with bounded.connection(): pass
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(checkout) for _ in range(3)]
        for future in futures: future.result(timeout=2)
    factory.assert_called_once()
    assert pool.getconn.call_count == pool.putconn.call_count == 3
