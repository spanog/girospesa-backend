"""Compose a disk adapter scoped to the configured Supabase project."""

from hashlib import sha256
from pathlib import Path

from core.config import settings
from services.repositories.flyer_source_cache import FlyerSourceCache


def create_flyer_source_cache() -> FlyerSourceCache:
    project = sha256(settings.supabase_url.encode()).hexdigest()[:16]
    return FlyerSourceCache(
        Path(settings.flyer_source_cache_dir) / project,
        settings.flyer_source_cache_max_bytes,
        settings.flyer_source_cache_ttl_seconds,
    )
