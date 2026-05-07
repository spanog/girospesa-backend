import pytest
from fastapi import HTTPException

from api.routers.lists import _enrich_items_with_categories, _selected_offer_patch


class Query:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.ids: set[str] = set()
        self.single_id: str | None = None

    def select(self, _fields: str) -> "Query":
        return self

    def in_(self, _field: str, values: list[str]) -> "Query":
        self.ids = set(values)
        return self

    def eq(self, _field: str, value: str) -> "Query":
        self.single_id = value
        return self

    def limit(self, _value: int) -> "Query":
        return self

    def execute(self):
        if self.single_id is not None:
            data = [row for row in self.rows if row["id"] == self.single_id]
        else:
            data = [row for row in self.rows if row["id"] in self.ids]
        return type("Response", (), {"data": data})()


class SupabaseStub:
    def __init__(self):
        self.products = Query([
            {
                "id": "prod-1",
                "name": "Pasta",
                "category": "dispensa",
                "subcategory": "Primi Piatti e Preparati",
            },
            {
                "id": "prod-2",
                "name": "Acqua naturale",
                "category": "bevande",
                "subcategory": "Acqua e Bibite",
            },
        ])
        self.offers = Query([
            {
                "id": "offer-1",
                "product_id": "prod-2",
                "products": {
                    "category": "bevande",
                    "subcategory": "Acqua e Bibite",
                },
                "supermarket_id": "store-1",
                "price_offer": 0.49,
                "price_original": 0.79,
                "discount_pct": 38,
                "unit_price": "0,25 €/l",
                "unit_price_value": 0.25,
                "unit_price_unit": "l",
                "unit_price_label": "0,25 €/l",
                "valid_to": "2099-12-31",
            },
        ])
        self.supermarkets = Query([
            {"id": "store-1", "name": "Coop"},
        ])

    def table(self, name: str) -> Query:
        if name == "products":
            return self.products
        if name == "supermarkets":
            return self.supermarkets
        return self.offers


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


def test_selected_offer_patch_builds_coherent_snapshot():
    patch = _selected_offer_patch(SupabaseStub(), "offer-1")

    assert patch["source"] == "offer"
    assert patch["pinned_product_id"] == "prod-2"
    assert patch["pinned_offer_id"] == "offer-1"
    assert patch["category"] == "bevande"
    assert patch["subcategory"] == "Acqua e Bibite"
    assert patch["found_deals"][0]["offer_id"] == "offer-1"
    assert patch["found_deals"][0]["product_name"] == "Acqua naturale"
    assert patch["found_deals"][0]["supermarket_name"] == "Coop"


def test_selected_offer_patch_404_when_offer_missing():
    with pytest.raises(HTTPException) as exc_info:
        _selected_offer_patch(SupabaseStub(), "missing-offer")

    assert exc_info.value.status_code == 404
