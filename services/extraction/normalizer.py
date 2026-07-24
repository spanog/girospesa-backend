"""
normalizer.py — Normalize extracted product data before writing to the database.

Handles:
- Category mapping (free-form AI output → controlled enum)
- Brand name standardization
- Price parsing (handles Italian decimal commas, stray € symbols)
- Discount percentage calculation
- Deduplication within a single flyer extraction
"""

from __future__ import annotations

import json
import re
import unicodedata

from services.product_format import (
    NormalizedFormatBundle,
    build_extraction_format_bundle,
    explode_format_variants,
)

# ---------------------------------------------------------------------------
# Category normalization
# ---------------------------------------------------------------------------

CATEGORY_ENUM = frozenset({
    "alimentari-freschi",
    "dispensa",
    "surgelati",
    "bevande",
    "cura-persona-salute",
    "cura-casa",
    "prodotti-animali",
    "altro",
})

_CATEGORY_ALIASES: dict[str, str] = {
    # main taxonomy
    "alimentari freschi": "alimentari-freschi",
    "dispensa": "dispensa",
    "surgelati": "surgelati",
    "bevande": "bevande",
    "cura della persona e salute": "cura-persona-salute",
    "cura della casa": "cura-casa",
    "prodotti per animali": "prodotti-animali",
    # alimentari-freschi variants
    "frutta": "alimentari-freschi",
    "verdura": "alimentari-freschi",
    "frutta e verdura": "alimentari-freschi",
    "frutta-verdura": "alimentari-freschi",
    "ortofrutta": "alimentari-freschi",
    "ortaggi": "alimentari-freschi",
    "carne": "alimentari-freschi",
    "pesce": "alimentari-freschi",
    "salumi": "alimentari-freschi",
    "affettati": "alimentari-freschi",
    "macelleria": "alimentari-freschi",
    "pescheria": "alimentari-freschi",
    "carne-pesce": "alimentari-freschi",
    "latticini": "alimentari-freschi",
    "formaggi": "alimentari-freschi",
    "uova": "alimentari-freschi",
    "latte": "alimentari-freschi",
    "yogurt": "alimentari-freschi",
    "latticini-uova": "alimentari-freschi",
    "pane": "alimentari-freschi",
    "forno": "alimentari-freschi",
    "pasticceria": "alimentari-freschi",
    "panetteria": "alimentari-freschi",
    "bakery": "alimentari-freschi",
    "pane-pasticceria": "alimentari-freschi",
    "latticini e formaggi": "alimentari-freschi",
    "macelleria e polleria": "alimentari-freschi",
    "salumeria e gastronomia": "alimentari-freschi",
    "colazione e prodotti da forno": "alimentari-freschi",
    # surgelati variants
    "surgelato": "surgelati",
    "congelati": "surgelati",
    "frozen": "surgelati",
    "verdure e preparati": "surgelati",
    "piatti pronti e pizze": "surgelati",
    "gelati": "surgelati",
    "pesce e frutti di mare": "surgelati",
    # bevande variants
    "bibite": "bevande",
    "acqua": "bevande",
    "vino": "bevande",
    "birra": "bevande",
    "succhi": "bevande",
    "alcolici": "bevande",
    "acqua e bibite": "bevande",
    "succhi e bevande alla frutta": "bevande",
    "alcolici e birre": "bevande",
    # dispensa variants
    "pasta": "dispensa",
    "riso": "dispensa",
    "conserve": "dispensa",
    "scatolame": "dispensa",
    "olio": "dispensa",
    "condimenti": "dispensa",
    "dolci": "dispensa",
    "snack": "dispensa",
    "biscotti": "dispensa",
    "primi piatti e preparati": "dispensa",
    "condimenti e conserve": "dispensa",
    "conserve ittiche e di carne": "dispensa",
    "caffè tè e tisane": "dispensa",
    "snack salati e dolciumi": "dispensa",
    # cura-persona-salute variants
    "igiene-bellezza": "cura-persona-salute",
    "igiene": "cura-persona-salute",
    "bellezza": "cura-persona-salute",
    "cosmetici": "cura-persona-salute",
    "cura persona": "cura-persona-salute",
    "personal care": "cura-persona-salute",
    "igiene orale": "cura-persona-salute",
    "igiene corpo e capelli": "cura-persona-salute",
    "igiene intima e salute": "cura-persona-salute",
    "infanzia": "cura-persona-salute",
    "integratori e parafarmacia": "cura-persona-salute",
    # cura-casa variants
    "casa-pulizia": "cura-casa",
    "pulizia": "cura-casa",
    "detergenti": "cura-casa",
    "casa": "cura-casa",
    "cleaning": "cura-casa",
    "detergenti bucato e stoviglie": "cura-casa",
    "pulizia superfici e cura ambienti": "cura-casa",
    "carta e monouso": "cura-casa",
    "accessori e manutenzione casa": "cura-casa",
    # prodotti-animali variants
    "animali": "prodotti-animali",
    "animali domestici": "prodotti-animali",
    "pet": "prodotti-animali",
    "cani": "prodotti-animali",
    "gatti": "prodotti-animali",
    "alimentazione cane e gatto": "prodotti-animali",
    "alimentazione piccoli animali": "prodotti-animali",
    "igiene e accessori animali": "prodotti-animali",
}

# ---------------------------------------------------------------------------
# Subcategory normalization
# ---------------------------------------------------------------------------

SUBCATEGORY_BY_CATEGORY: dict[str, list[str]] = {
    "alimentari-freschi": [
        "Latticini e Formaggi",
        "Macelleria e Polleria",
        "Salumeria e Gastronomia",
        "Ortofrutta",
        "Pescheria",
    ],
    "dispensa": [
        "Primi Piatti e Preparati",
        "Condimenti e Conserve",
        "Conserve Ittiche e di Carne",
        "Colazione e Prodotti da Forno",
        "Caffè Tè e Tisane",
        "Snack Salati e Dolciumi",
    ],
    "surgelati": [
        "Pesce e Frutti di Mare",
        "Verdure e Preparati",
        "Piatti Pronti e Pizze",
        "Gelati",
    ],
    "bevande": [
        "Acqua e Bibite",
        "Succhi e Bevande alla frutta",
        "Alcolici e Birre",
    ],
    "cura-persona-salute": [
        "Igiene Orale",
        "Igiene Corpo e Capelli",
        "Igiene Intima e Salute",
        "Infanzia",
        "Integratori e Parafarmacia",
    ],
    "cura-casa": [
        "Detergenti Bucato e Stoviglie",
        "Pulizia Superfici e Cura Ambienti",
        "Carta e Monouso",
        "Accessori e Manutenzione casa",
    ],
    "prodotti-animali": [
        "Alimentazione Cane e Gatto",
        "Alimentazione Piccoli Animali",
        "Igiene e Accessori Animali",
    ],
}

_ALL_SUBCATEGORIES: dict[str, str] = {
    sub.lower(): sub
    for subs in SUBCATEGORY_BY_CATEGORY.values()
    for sub in subs
}


def normalize_subcategory(raw: str | None) -> str | None:
    """Map a raw subcategory string to the canonical value, or None if unknown."""
    if not raw:
        return None
    return _ALL_SUBCATEGORIES.get(raw.strip().lower())


def normalize_category(raw: str | None) -> str:
    """Map a raw category string to the controlled enum value."""
    if not raw:
        return "altro"
    cleaned = raw.strip().lower()
    if cleaned in CATEGORY_ENUM:
        return cleaned
    if cleaned in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[cleaned]
    # Try alias lookup
    for alias, canonical in sorted(_CATEGORY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in cleaned:
            return canonical
    return "altro"


# ---------------------------------------------------------------------------
# Brand normalization
# ---------------------------------------------------------------------------

def normalize_brand(raw: str | None) -> str | None:
    """Standardize brand name casing. DB uses citext for case-insensitive deduplication."""
    if not raw:
        return None
    return raw.strip().title()


def normalize_for_comparison(s: str) -> str:
    """NFD decompose → strip combining diacritics → casefold. Used only for similarity checks, not storage."""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").casefold()


# ---------------------------------------------------------------------------
# Price normalization
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"[\d]+[.,]\d{1,2}")
_UNIT_PRICE_MEASURES = {
    "kg": "kg",
    "kilo": "kg",
    "kilogrammo": "kg",
    "l": "l",
    "lt": "l",
    "litro": "l",
    "litri": "l",
    "kg sgocc": "kg sgocc",
    "kg sgocc.": "kg sgocc",
    "kg sgocciolato": "kg sgocc",
}


def normalize_price(raw: str | int | float | None) -> float | None:
    """
    Parse a price value to float, handling:
    - Already-numeric input: returned directly
    - Italian comma decimals: "1,99" → 1.99
    - Stray € symbols: "€ 1.99" → 1.99
    - "al kg" suffix: ignored (returns value without unit)
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v if v > 0 else None
    text = str(raw).strip().replace("€", "").replace(" ", "")
    # Replace Italian comma decimal separator
    text = text.replace(",", ".")
    # Remove trailing/leading non-numeric chars
    match = _PRICE_RE.search(text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_unit_price_measure(raw: str | None) -> str | None:
    """Normalize unit price measure to supported values."""
    if not raw:
        return None
    cleaned = (
        raw.strip()
        .lower()
        .replace("l.", "l")
    )
    cleaned = re.sub(r"\s+", " ", cleaned)
    return _UNIT_PRICE_MEASURES.get(cleaned)


def format_unit_price_label(value: float | None, measure: str | None) -> str | None:
    """Format a human-readable unit price label."""
    if value is None or measure is None:
        return None
    formatted = f"{value:.2f}".replace(".", ",")
    return f"{formatted} €/{measure}"


# ---------------------------------------------------------------------------
# Discount calculation
# ---------------------------------------------------------------------------

def calculate_discount_pct(price_original: float | None, price_offer: float | None) -> int | None:
    """Calculate integer discount percentage, or None if data is missing/invalid."""
    if not price_original or not price_offer:
        return None
    if price_original <= 0 or price_offer >= price_original:
        return None
    pct = round((price_original - price_offer) / price_original * 100)
    return pct if pct > 0 else None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_products(products: list[dict]) -> list[dict]:
    """
    Remove duplicate products extracted from multiple pages.
    Deduplication key: (name, brand) — case-insensitive.
    Keeps the first occurrence (preserves format from earliest extraction).
    """
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for p in products:
        key = (
            (p.get("name") or "").strip().lower(),
            (p.get("brand") or "").strip().lower(),
        )
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Full product normalization pipeline
# ---------------------------------------------------------------------------

def _coerce_str(value: object) -> str | None:
    """Convert any scalar to stripped string, treating None/empty as None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip().replace(",", ".")))
    except ValueError:
        return None


def _normalized_packshot_box(value: object) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        box = [int(float(item)) for item in value]
    except (TypeError, ValueError):
        return None
    y1, x1, y2, x2 = box
    return box if 0 <= y1 < y2 <= 1000 and 0 <= x1 < x2 <= 1000 else None


def _coerce_format_bundle(raw: dict) -> NormalizedFormatBundle:
    existing_bundle = raw.get("_format_bundle")
    if isinstance(existing_bundle, NormalizedFormatBundle):
        return existing_bundle
    return build_extraction_format_bundle(raw.get("format"))


def json_size_bytes(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def normalize_product(raw: dict) -> dict:
    """Apply all normalization steps to a single extracted product dict."""
    price_offer = normalize_price(raw.get("price_offer") or raw.get("price_current"))
    price_original = normalize_price(raw.get("price_original"))
    unit_price_value = normalize_price(raw.get("price_per_unit"))
    unit_price_unit = normalize_unit_price_measure(_coerce_str(raw.get("price_per_unit_measure")))
    discount_pct = _coerce_int(raw.get("discount_pct") or raw.get("discount_percentage"))
    raw_category = _coerce_str(raw.get("category")) or _coerce_str(raw.get("category_main"))
    raw_subcategory = _coerce_str(raw.get("subcategory")) or _coerce_str(raw.get("category_sub"))
    category = normalize_category(raw_category)
    subcategory = normalize_subcategory(raw_subcategory)
    format_bundle = _coerce_format_bundle(raw)

    return {
        "name": _coerce_str(raw.get("name")) or "",
        "brand": normalize_brand(_coerce_str(raw.get("brand"))),
        "category": category,
        "subcategory": subcategory,
        "format": format_bundle.format_compact,
        "format_key": format_bundle.format_key,
        "format_label": format_bundle.format_label,
        "price_offer": price_offer,
        "price_original": price_original,
        "discount_pct": discount_pct or calculate_discount_pct(price_original, price_offer),
        "unit_price_value": unit_price_value,
        "unit_price_unit": unit_price_unit,
        "unit_price": format_unit_price_label(unit_price_value, unit_price_unit),
        "offer_notes": _coerce_str(raw.get("offer_notes")),
        "valid_from": _coerce_str(raw.get("valid_from")),
        "valid_to": _coerce_str(raw.get("valid_to")),
        "source_page": _coerce_int(raw.get("source_page")),
        "packshot_bbox": _normalized_packshot_box(raw.get("packshot_bbox")),
    }


def expand_products(raw_products: list[dict]) -> list[dict]:
    expanded: list[dict] = []
    for raw in raw_products:
        expanded.extend(explode_format_variants(raw))
    return expanded


def normalize_products(raw_products: list[dict]) -> list[dict]:
    return [normalize_product(candidate) for candidate in expand_products(raw_products)]
