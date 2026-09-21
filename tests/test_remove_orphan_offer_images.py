from __future__ import annotations

from pathlib import Path

from scripts import remove_orphan_offer_images as cleanup


class _Bucket:
    def __init__(self, objects: set[str]) -> None:
        self.objects = objects
        self.removed: list[list[str]] = []

    def list(self, prefix: str, options: dict[str, int]) -> list[dict[str, str]]:
        relative_paths = (
            path[len(prefix) + 1 :] if prefix else path
            for path in self.objects
            if not prefix or path.startswith(f"{prefix}/")
        )
        files: set[str] = set()
        folders: set[str] = set()
        for path in relative_paths:
            name, separator, _ = path.partition("/")
            (folders if separator else files).add(name)
        rows = [{"name": name} for name in sorted(folders)]
        rows.extend({"name": name, "id": name} for name in sorted(files))
        start = options["offset"]
        return rows[start : start + options["limit"]]

    def remove(self, paths: list[str]) -> None:
        self.removed.append(paths)
        self.objects.difference_update(paths)


class _Storage:
    def __init__(self, bucket: _Bucket) -> None:
        self.bucket = bucket

    def from_(self, _: str) -> _Bucket:
        return self.bucket


class _Query:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def select(self, _: str) -> _Query:
        return self

    def range(self, start: int, end: int) -> _Query:
        self.rows = self.rows[start : end + 1]
        return self

    def execute(self) -> _Query:
        self.data = self.rows
        return self


class _Client:
    def __init__(self, objects: set[str], offers: list[dict]) -> None:
        self.storage = _Storage(_Bucket(objects))
        self.offers = offers

    def table(self, _: str) -> _Query:
        return _Query(self.offers.copy())


def test_orphan_paths_only_returns_unreferenced_storage_objects() -> None:
    client = _Client(
        {"draft/used.webp", "draft/orphan.webp"},
        [{"image_url": "https://db.test/storage/v1/object/public/product-images/draft/used.webp"}],
    )

    assert cleanup.orphan_paths(client) == ["draft/orphan.webp"]


def test_remove_paths_batches_storage_deletions(monkeypatch) -> None:
    monkeypatch.setattr(cleanup, "_BATCH_SIZE", 2)
    client = _Client({"a.webp", "b.webp", "c.webp"}, [])

    cleanup.remove_paths(client, ["a.webp", "b.webp", "c.webp"])

    assert client.storage.bucket.removed == [["a.webp", "b.webp"], ["c.webp"]]
    assert client.storage.bucket.objects == set()


def test_env_file_uses_its_own_supabase_credentials(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / "production.env"
    env_file.write_text("SUPABASE_URL=https://project.supabase.co\nSUPABASE_SECRET_KEY=key\n")
    created: dict[str, str] = {}

    def create_client(url: str, key: str) -> object:
        created.update(url=url, key=key)
        return object()

    monkeypatch.setattr(cleanup, "create_supabase_client", create_client)

    assert cleanup._supabase_client(env_file)
    assert created == {"url": "https://project.supabase.co", "key": "key"}
