from __future__ import annotations

from services.repositories import lists_repository


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, name: str, store: dict[str, list[dict]]):
        self.name = name
        self.store = store
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        if self.name == "shopping_lists":
            row = {"id": "list-1", **self.payload}
            self.store[self.name].append(row)
            return _Result([row])
        self.store[self.name].append(self.payload)
        return _Result([self.payload])


class _FakeSupabase:
    def __init__(self):
        self.store = {"shopping_lists": [], "list_members": []}

    def table(self, name: str):
        return _Table(name, self.store)


def test_create_owned_list_falls_back_to_supabase_when_direct_postgres_missing(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(lists_repository, "has_direct_postgres", lambda: False)
    monkeypatch.setattr(lists_repository, "get_supabase", lambda: fake)

    created = lists_repository.create_owned_list(
        user_id="user-1",
        name="Weekend",
        is_default=False,
        items=[],
    )

    assert created["id"] == "list-1"
    assert fake.store["shopping_lists"][0]["name"] == "Weekend"
    assert fake.store["list_members"][0] == {
        "list_id": "list-1",
        "user_id": "user-1",
        "role": "owner",
    }
