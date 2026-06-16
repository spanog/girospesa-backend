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
        self.filters: dict[str, object] = {}

    def insert(self, payload):
        self.payload = payload
        return self

    def delete(self):
        return self

    def eq(self, key: str, value: object):
        self.filters[key] = value
        return self

    def execute(self):
        if self.name == "shopping_lists":
            row = {"id": "list-1", **self.payload}
            self.store[self.name].append(row)
            return _Result([row])
        if self.payload is None and self.filters:
            remaining = []
            removed = []
            for row in self.store[self.name]:
                if all(row.get(key) == value for key, value in self.filters.items()):
                    removed.append(row)
                else:
                    remaining.append(row)
            self.store[self.name] = remaining
            return _Result(removed)
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
        items=[],
    )

    assert created["id"] == "list-1"
    assert fake.store["shopping_lists"][0]["name"] == "Weekend"
    assert fake.store["list_members"][0] == {
        "list_id": "list-1",
        "user_id": "user-1",
        "role": "owner",
    }


def test_delete_member_falls_back_to_supabase_when_direct_postgres_missing(monkeypatch):
    fake = _FakeSupabase()
    fake.store["list_members"] = [
        {"list_id": "list-1", "user_id": "user-1", "role": "owner"},
        {"list_id": "list-1", "user_id": "user-2", "role": "member"},
    ]
    monkeypatch.setattr(lists_repository, "has_direct_postgres", lambda: False)
    monkeypatch.setattr(lists_repository, "get_supabase", lambda: fake)

    lists_repository.delete_member("list-1", "user-2")

    assert fake.store["list_members"] == [
        {"list_id": "list-1", "user_id": "user-1", "role": "owner"}
    ]


def test_resolved_list_id_prefers_explicit_active_selection(monkeypatch):
    monkeypatch.setattr(
        lists_repository,
        "active_list_id_for_user",
        lambda _sb, _user_id: "list-shared",
    )
    monkeypatch.setattr(
        lists_repository,
        "visible_memberships",
        lambda _sb, _user_id: [
            {"list_id": "list-owner", "role": "owner"},
            {"list_id": "list-shared", "role": "member"},
        ],
    )
    monkeypatch.setattr(
        lists_repository,
        "owner_list_id_for_user",
        lambda _sb, _user_id: "list-owner",
    )

    resolved = lists_repository.resolved_list_id_for_user(object(), "user-1")

    assert resolved == "list-shared"


def test_resolved_list_id_falls_back_to_owner_when_active_selection_invalid(monkeypatch):
    monkeypatch.setattr(
        lists_repository,
        "active_list_id_for_user",
        lambda _sb, _user_id: "list-removed",
    )
    monkeypatch.setattr(
        lists_repository,
        "visible_memberships",
        lambda _sb, _user_id: [
            {"list_id": "list-owner", "role": "owner"},
            {"list_id": "list-shared", "role": "member"},
        ],
    )
    monkeypatch.setattr(
        lists_repository,
        "owner_list_id_for_user",
        lambda _sb, _user_id: "list-owner",
    )

    resolved = lists_repository.resolved_list_id_for_user(object(), "user-1")

    assert resolved == "list-owner"
