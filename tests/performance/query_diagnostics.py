"""Read-only query plans, collected only after a failed discovery measurement."""
import os
import re

import psycopg2


def explain_discovery(supermarkets: list[dict], filter_query: str | None = "PERF_Prodotto_") -> str:
    """Expose the SQL body plan without warming the measured request."""
    with psycopg2.connect(os.environ["DB_DSN"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET search_path = public, extensions")
            cursor.execute("SELECT prosrc FROM pg_proc WHERE oid = 'public.nearby_public_offer_page(uuid[],double precision[],text,text,text,uuid,uuid[],integer,integer)'::regprocedure")
            body = cursor.fetchone()[0].replace("%", "%%")
            values = discovery_values(supermarkets, filter_query)
            for name, (value, cast) in values.items():
                body = re.sub(r"\b" + name + r"\b", f"%({name})s::{cast}", body)
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + body, {key: value for key, (value, _) in values.items()})
            return "\n".join(row[0] for row in cursor.fetchall())


def discovery_values(supermarkets: list[dict], filter_query: str | None = "PERF_Prodotto_") -> dict:
    """Match the RPC inputs used by the first-request benchmark."""
    return {
        "candidate_supermarket_ids": ([market["id"] for market in supermarkets], "uuid[]"),
        "candidate_distances_km": ([float(index) for index in range(len(supermarkets))], "double precision[]"),
        "filter_query": (filter_query, "text"),
        "filter_category": (None, "text"),
        "filter_subcategory": (None, "text"),
        "filter_supermarket_id": (None, "uuid"),
        "filter_supermarket_ids": (None, "uuid[]"),
        "page_limit": (20, "integer"),
        "page_offset": (0, "integer"),
    }
