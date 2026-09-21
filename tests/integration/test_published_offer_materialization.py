"""Integration coverage for SQL-backed source-offer publication."""

from __future__ import annotations

import uuid


_SOURCE_COUNT = 285
_TARGET_COUNT = 3


def _supermarket_payload(index: int) -> dict:
    suffix = uuid.uuid4().hex[:8]
    return {
        "name": f"Materialization market {index}-{suffix}",
        "slug": f"materialization-{index}-{suffix}",
        "municipality_code": "015146",
    }


def _flyer_payload(supermarket: dict, **extra: object) -> dict:
    return {
        "supermarket_id": supermarket["id"],
        "supermarket_name": supermarket["name"],
        "file_url": f"materialization/{uuid.uuid4()}.pdf",
        "file_type": "pdf",
        "file_name": "materialization.pdf",
        "status": "done",
        "valid_from": "2026-01-01",
        "valid_to": "2099-12-31",
        **extra,
    }


def _source_offer_payload(source_flyer: dict, supermarket: dict, index: int) -> dict:
    return {
        "flyer_id": source_flyer["id"],
        "supermarket_id": supermarket["id"],
        "supermarket_name": supermarket["name"],
        "name": f"Materialization product {index}",
        "brand": "GiroSpesa",
        "category": "dispensa",
        "offer_key": f"materialization-{index}",
        "price_offer": 1.99,
        "price_original": 2.99,
        "image_url": f"https://storage.test/source-{index}.webp",
        "packshot_source_page": 1,
        "packshot_bbox": [100, 100, 900, 900],
        "format": {"tipo": "confezione_singola"},
        "format_key": "confezione_singola",
        "format_label": "Confezione singola",
        "is_confirmed": False,
        "is_reviewed": True,
        "offer_kind": "source_master",
    }


def _clone_counts(supabase_client, target_flyers: list[dict]) -> dict[str, int]:
    rows = (
        supabase_client.table("offers")
        .select("flyer_id")
        .in_("flyer_id", [flyer["id"] for flyer in target_flyers])
        .eq("offer_kind", "published_target")
        .execute()
        .data
    )
    return {
        flyer["id"]: sum(row["flyer_id"] == flyer["id"] for row in rows)
        for flyer in target_flyers
    }


def test_materialization_keeps_clone_lifecycle_and_counts(supabase_client, clean_db):
    supermarkets = [
        supabase_client.table("supermarkets").insert(_supermarket_payload(index)).execute().data[0]
        for index in range(_TARGET_COUNT)
    ]
    source_flyer = (
        supabase_client.table("flyers").insert(_flyer_payload(supermarkets[0])).execute().data[0]
    )
    target_flyers = [
        supabase_client.table("flyers")
        .insert(
            _flyer_payload(
                supermarket,
                flyer_kind="published_target",
                source_flyer_id=source_flyer["id"],
                is_public=True,
            )
        )
        .execute()
        .data[0]
        for supermarket in supermarkets
    ]
    source_offers = [
        _source_offer_payload(source_flyer, supermarkets[0], index)
        for index in range(_SOURCE_COUNT)
    ]
    inserted_sources = supabase_client.table("offers").insert(source_offers).execute().data

    confirmed = supabase_client.rpc(
        "confirm_source_flyer_offers", {"p_flyer_id": source_flyer["id"]}
    ).execute()
    assert confirmed.data == _SOURCE_COUNT

    first = supabase_client.rpc(
        "materialize_source_flyer_targets", {"p_source_flyer_id": source_flyer["id"]}
    ).execute().data
    assert {row["products_count"] for row in first} == {_SOURCE_COUNT}
    assert _clone_counts(supabase_client, target_flyers) == {
        flyer["id"]: _SOURCE_COUNT for flyer in target_flyers
    }
    assert sum(_clone_counts(supabase_client, target_flyers).values()) == _SOURCE_COUNT * _TARGET_COUNT

    supabase_client.rpc(
        "materialize_source_flyer_targets", {"p_source_flyer_id": source_flyer["id"]}
    ).execute()
    assert _clone_counts(supabase_client, target_flyers) == {
        flyer["id"]: _SOURCE_COUNT for flyer in target_flyers
    }

    changed_source = inserted_sources[0]
    supabase_client.table("offers").update(
        {"name": "Materialization product updated", "image_url": "https://storage.test/updated.webp"}
    ).eq("id", changed_source["id"]).execute()
    supabase_client.rpc(
        "materialize_source_flyer_targets", {"p_source_flyer_id": source_flyer["id"]}
    ).execute()
    changed_clones = (
        supabase_client.table("offers")
        .select("name, image_url")
        .eq("source_offer_id", changed_source["id"])
        .execute()
        .data
    )
    assert changed_clones == [
        {"name": "Materialization product updated", "image_url": "https://storage.test/updated.webp"}
    ] * _TARGET_COUNT

    supabase_client.table("offers").update({"is_confirmed": False}).eq(
        "id", changed_source["id"]
    ).execute()
    removed = supabase_client.rpc(
        "materialize_source_flyer_targets", {"p_source_flyer_id": source_flyer["id"]}
    ).execute().data
    assert {row["products_count"] for row in removed} == {_SOURCE_COUNT - 1}
    assert _clone_counts(supabase_client, target_flyers) == {
        flyer["id"]: _SOURCE_COUNT - 1 for flyer in target_flyers
    }
