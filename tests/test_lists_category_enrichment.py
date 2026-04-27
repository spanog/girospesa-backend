from api.routers.lists import _enrich_items_with_categories


class Query:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.ids: set[str] = set()

    def select(self, _fields: str) -> "Query":
        return self

    def in_(self, _field: str, values: list[str]) -> "Query":
        self.ids = set(values)
        return self

    def execute(self):
        return type("Response", (), {"data": [row for row in self.rows if row["id"] in self.ids]})()


class SupabaseStub:
    def __init__(self):
        self.products = Query([
            {"id": "prod-1", "category": "dispensa", "subcategory": "Primi Piatti e Preparati"},
        ])
        self.offers = Query([
            {
                "id": "offer-1",
                "product_id": "prod-2",
                "products": {
                    "category": "bevande",
                    "subcategory": "Acqua e Bibite",
                },
            },
        ])

    def table(self, name: str) -> Query:
        return self.products if name == "products" else self.offers


def test_enriches_items_from_pinned_product_id():
    items = [{"id": "item-1", "name": "Pasta", "pinned_product_id": "prod-1"}]

    enriched = _enrich_items_with_categories(SupabaseStub(), items)

    assert enriched[0]["category"] == "dispensa"
    assert enriched[0]["subcategory"] == "Primi Piatti e Preparati"


def test_enriches_items_from_pinned_offer_id():
    items = [{"id": "item-1", "name": "Acqua", "pinned_offer_id": "offer-1"}]

    enriched = _enrich_items_with_categories(SupabaseStub(), items)

    assert enriched[0]["category"] == "bevande"
    assert enriched[0]["subcategory"] == "Acqua e Bibite"


def test_keeps_manual_items_uncategorized():
    items = [{"id": "item-1", "name": "Promemoria"}]

    enriched = _enrich_items_with_categories(SupabaseStub(), items)

    assert enriched[0]["category"] is None
    assert enriched[0]["subcategory"] is None


def test_preserves_existing_snapshot_when_lookup_is_missing():
    items = [{
        "id": "item-1",
        "name": "Archivio",
        "pinned_product_id": "missing",
        "category": "surgelati",
        "subcategory": "Gelati",
    }]

    enriched = _enrich_items_with_categories(SupabaseStub(), items)

    assert enriched[0]["category"] == "surgelati"
    assert enriched[0]["subcategory"] == "Gelati"
