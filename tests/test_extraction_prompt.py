"""Shared offer-granularity contract for normal and recovery inference."""
from services.extraction.providers.prompts import EXTRACTION_PROMPT
from services.extraction.providers.gemini import LOW_COVERAGE_RETRY_PROMPT
from services.extraction.normalizer import normalize_products


def test_prompt_defines_grouped_alternatives_without_false_multipacks():
    assert 'NON usare `format.varianti`' in EXTRACTION_PROMPT
    assert 'stesso formato, prezzo, validità e condizioni' in EXTRACTION_PROMPT
    assert 'NON emettere due offerte o un formato 2x1 L' in EXTRACTION_PROMPT
    assert 'Non accorpare prodotti di marche o linee diverse' in EXTRACTION_PROMPT
    assert 'Mantieni accorpate le varianti' in LOW_COVERAGE_RETRY_PROMPT


def test_grouped_accadi_remains_one_offer_with_one_litre_format():
    raw = {"name": "Latte UHT Accadì 1% / senza grassi", "brand": "Granarolo",
           "price_current": 1.09, "format": {"tipo": "confezione_singola", "peso_volume": 1, "unita_misura": "L"},
           "packshot_bbox": {"x_min": 710, "y_min": 60, "x_max": 960, "y_max": 355}}
    products = normalize_products([raw])
    assert len(products) == 1
    assert products[0]["format_label"] == "1 L"
    assert products[0]["packshot_bbox"] == [710, 60, 960, 355]
    assert '1% / senza grassi' in products[0]["name"]


def test_grouped_flavours_preserve_actual_yogurt_multipack():
    products = normalize_products([{
        "name": "Yogurt Müller bianco / mirtillo", "price_current": 0.79,
        "format": {"tipo": "multipack_omogeneo", "quantita": 2, "peso_volume": 125, "unita_misura": "g"},
    }])
    assert len(products) == 1
    assert products[0]["format_label"] == "2x125 g"
    assert 'NON ridurre a 125 g' in EXTRACTION_PROMPT
