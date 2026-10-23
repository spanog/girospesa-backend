from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock
import os
import time

import pytest

from services.repositories.flyer_source_cache import FlyerSourceCache, FlyerSourceIntegrityError


def _lease(cache, content=b"%PDF-original", source="user/flyer.pdf", populate=None):
    return cache.lease(source, sha256(content).hexdigest(), populate or (lambda path: path.write_bytes(content)))


def test_reuses_source_across_adapter_instances_and_checks_content(tmp_path):
    populate = Mock(side_effect=lambda path: path.write_bytes(b"%PDF-original"))
    for _ in range(3):
        with _lease(FlyerSourceCache(tmp_path, 100, 60), populate=populate) as path:
            assert path.read_bytes() == b"%PDF-original"
    populate.assert_called_once()


def test_distinct_source_and_hash_have_separate_entries(tmp_path):
    cache = FlyerSourceCache(tmp_path, 100, 60)
    with _lease(cache) as first:
        original = first
    with _lease(cache, content=b"updated") as updated:
        assert updated != original
    with _lease(cache, source="another-project/flyer.pdf") as other:
        assert other != original


def test_corrupt_cached_source_is_downloaded_again(tmp_path):
    cache = FlyerSourceCache(tmp_path, 100, 60)
    with _lease(cache) as path:
        path.write_bytes(b"corrupt")
    populate = Mock(side_effect=lambda path: path.write_bytes(b"%PDF-original"))
    with _lease(cache, populate=populate) as path:
        assert path.read_bytes() == b"%PDF-original"
    populate.assert_called_once()


def test_mismatching_download_is_not_cached(tmp_path):
    cache = FlyerSourceCache(tmp_path, 100, 60)
    with pytest.raises(FlyerSourceIntegrityError):
        with _lease(cache, populate=lambda path: path.write_bytes(b"wrong")):
            pytest.fail("Unverified source must never be handed to a reader")
    assert not list(tmp_path.glob("*.bin"))
    assert not list(tmp_path.glob("*.part"))


def test_failed_download_removes_partial_file_and_can_retry(tmp_path):
    cache = FlyerSourceCache(tmp_path, 100, 60)
    with pytest.raises(OSError):
        with _lease(cache, populate=Mock(side_effect=OSError("Interrupted download"))):
            pass
    assert not list(tmp_path.glob("*.part"))
    with _lease(cache) as path:
        assert path.read_bytes() == b"%PDF-original"


def test_expired_source_is_fetched_again(tmp_path):
    cache = FlyerSourceCache(tmp_path, 100, 60)
    with _lease(cache) as path:
        original = path
    os.utime(original, (time.time() - 120,) * 2)
    populate = Mock(side_effect=lambda path: path.write_bytes(b"%PDF-original"))
    with _lease(cache, populate=populate):
        pass
    populate.assert_called_once()


def test_cache_evicts_old_sources_and_preserves_active_readers(tmp_path):
    cache = FlyerSourceCache(tmp_path, 10, 60)
    with _lease(cache, b"old-source") as old:
        old_path = old
    os.utime(old_path, (time.time() - 1,) * 2)
    with _lease(cache, b"new-source") as current:
        assert not old_path.exists()
        assert current.exists()
        with _lease(cache, b"other-source"):
            assert current.read_bytes() == b"new-source"
    assert sum(path.stat().st_size for path in tmp_path.glob("*.bin")) <= 10


def test_concurrent_requests_download_once(tmp_path):
    cache = FlyerSourceCache(tmp_path, 100, 60)
    populate = Mock(side_effect=lambda path: path.write_bytes(b"%PDF-original"))

    def read_source(_):
        with _lease(cache, populate=populate) as path:
            return path.read_bytes()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read_source, range(8))) == [b"%PDF-original"] * 8
    populate.assert_called_once()


def test_reader_failure_releases_lock(tmp_path):
    cache = FlyerSourceCache(tmp_path, 100, 60)
    with pytest.raises(ValueError):
        with _lease(cache):
            raise ValueError("Invalid PDF")
    with _lease(cache) as path:
        assert path.exists()


def test_factory_separates_identical_sources_between_projects(tmp_path, monkeypatch):
    from core import flyer_cache
    config = SimpleNamespace(supabase_url="https://first.test", flyer_source_cache_dir=str(tmp_path),
                             flyer_source_cache_max_bytes=100, flyer_source_cache_ttl_seconds=60)
    monkeypatch.setattr(flyer_cache, "settings", config)
    with _lease(flyer_cache.create_flyer_source_cache()) as path:
        first = path
    config.supabase_url = "https://second.test"
    with _lease(flyer_cache.create_flyer_source_cache()) as second:
        assert first != second
        assert first.read_bytes() == second.read_bytes()
