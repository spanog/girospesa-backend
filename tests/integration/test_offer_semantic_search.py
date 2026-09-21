"""Integration coverage for Italian linguistic offer discovery."""

from __future__ import annotations

import uuid


_SEARCH_PARAMETERS = {
    "candidate_distances_km": [0.0],
    "filter_category": None,
    "filter_subcategory": None,
    "filter_supermarket_id": None,
    "filter_supermarket_ids": [],
    "page_limit": 20,
    "page_offset": 0,
}


def _supermarket_payload() -> dict:
    suffix = uuid.uuid4().hex[:8]
    return {
        "name": f"Semantic market {suffix}",
        "slug": f"semantic-market-{suffix}",
        "municipality_code": "015146",
    }


def _offer_payload(supermarket_id: str, name: str, offer_key: str) -> dict:
    return {
        "supermarket_id": supermarket_id,
        "supermarket_name": "Semantic market",
        "name": name,
        "brand": "Loriana",
        "category": "Dispensa",
        "subcategory": "Colazione e Prodotti da Forno",
        "offer_key": offer_key,
        "price_offer": 1.99,
        "format": {"tipo": "confezione_singola"},
        "format_key": "confezione_singola",
        "format_label": "Confezione singola",
        "is_confirmed": True,
        "is_reviewed": True,
        "offer_kind": "published_target",
        "valid_from": "2026-01-01",
        "valid_to": "2099-12-31",
    }


def _search(supabase_client, supermarket_id: str, query: str) -> list[dict]:
    response = supabase_client.rpc(
        "nearby_public_offer_page",
        {
            **_SEARCH_PARAMETERS,
            "candidate_supermarket_ids": [supermarket_id],
            "filter_query": query,
        },
    ).execute()
    return response.data


def test_offer_search_stems_singular_and_plural_real_flyer_names(
    supabase_client, clean_db
):
    supermarket = (
        supabase_client.table("supermarkets").insert(_supermarket_payload()).execute().data[0]
    )
    offers = [
        "Piadina Romagnola IGP alla Riminese XXL",
        "3 piadine sfogliatissime all'olio extravergine d'oliva Loriana",
    ]
    supabase_client.table("offers").insert(
        [_offer_payload(supermarket["id"], name, f"piadina-{index}") for index, name in enumerate(offers)]
    ).execute()

    singular = _search(supabase_client, supermarket["id"], "piadina")
    plural = _search(supabase_client, supermarket["id"], "piadine")
    prefix = _search(supabase_client, supermarket["id"], "piadin")

    assert singular[0]["total"] == 2
    assert plural[0]["total"] == 2
    assert prefix[0]["total"] == 2


def test_offer_search_uses_trigram_fallback_only_when_linguistic_match_is_empty(
    supabase_client, clean_db
):
    supermarket = (
        supabase_client.table("supermarkets").insert(_supermarket_payload()).execute().data[0]
    )
    name = "Piadina Romagnola IGP alla Riminese XXL"
    supabase_client.table("offers").insert(
        _offer_payload(supermarket["id"], name, "piadina-typo")
    ).execute()

    results = _search(supabase_client, supermarket["id"], "piadnia")

    assert results[0]["total"] == 1
    assert results[0]["id"] is not None
