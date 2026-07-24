from __future__ import annotations

from scripts import reset_offer_only_storage as reset


class _Bucket:
    def __init__(self, objects: set[str]) -> None:
        self.objects = objects
        self.removed: list[list[str]] = []

    def list(self, prefix: str, options: dict[str, int]) -> list[dict[str, str]]:
        names = sorted(
            path[len(prefix) + 1 :] if prefix else path
            for path in self.objects
            if not prefix or path.startswith(f"{prefix}/")
        )
        direct = [name for name in names if "/" not in name]
        start = options["offset"]
        limit = options["limit"]
        return [{"name": name, "id": name} for name in direct[start : start + limit]]

    def remove(self, paths: list[str]) -> None:
        self.removed.append(paths)
        self.objects.difference_update(paths)


class _Storage:
    def __init__(self, bucket: _Bucket) -> None:
        self.bucket = bucket

    def from_(self, _: str) -> _Bucket:
        return self.bucket


class _Client:
    def __init__(self, bucket: _Bucket) -> None:
        self.storage = _Storage(bucket)


def test_empty_bucket_paginates_and_verifies(monkeypatch) -> None:
    monkeypatch.setattr(reset, "BATCH_SIZE", 2)
    bucket = _Bucket({"a.png", "b.png", "c.png", "d.png", "e.png"})

    removed = reset.empty_bucket(_Client(bucket), "product-images")

    assert removed == 5
    assert bucket.objects == set()
    assert bucket.removed == [["a.png", "b.png"], ["c.png", "d.png"], ["e.png"]]


def test_empty_bucket_fails_when_verification_finds_object(monkeypatch) -> None:
    monkeypatch.setattr(reset, "BATCH_SIZE", 2)

    class _StubbornBucket(_Bucket):
        def remove(self, paths: list[str]) -> None:
            self.removed.append(paths)

    bucket = _StubbornBucket({"a.png"})

    try:
        reset.empty_bucket(_Client(bucket), "flyers")
    except RuntimeError as error:
        assert "still contains" in str(error)
    else:
        raise AssertionError("expected final storage verification to fail")
