"""Unit tests for scripts/extraction/submit_flyer.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.extraction import submit_flyer


def test_parse_args_reads_expected_options():
    args = submit_flyer.parse_args(
        [
            "volantino.pdf",
            "-s",
            "Conad",
            "--valid-from",
            "2026-04-07",
            "--valid-to",
            "2026-04-13",
            "--model",
            "gemini-2.5-pro",
            "--api-key",
            "abc",
        ]
    )

    assert args.file == "volantino.pdf"
    assert args.supermarket == "Conad"
    assert args.valid_from == "2026-04-07"
    assert args.valid_to == "2026-04-13"
    assert args.model == "gemini-2.5-pro"
    assert args.api_key == "abc"


def test_main_writes_normalized_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    flyer_path = tmp_path / "volantino.pdf"
    output_path = tmp_path / "result.json"
    flyer_path.write_bytes(b"%PDF-fake")

    provider = MagicMock()
    provider.extract_products.return_value = (
        [
            {
                "name": "Pasta Barilla",
                "brand": "Barilla",
                "category": "dispensa",
                "format": {
                    "tipo": "confezione_singola",
                    "peso_volume": 500,
                    "unita_misura": "g",
                },
                "price_offer": 1.29,
                "price_original": 1.79,
            }
        ],
        [],
    )

    monkeypatch.setattr(submit_flyer, "GeminiProvider", lambda api_key, model: provider)
    monkeypatch.setattr(submit_flyer, "count_pdf_pages", lambda _content: 1)
    monkeypatch.setattr(submit_flyer, "is_pdf", lambda _name: True)
    monkeypatch.setattr(submit_flyer, "mime_type_for_filename", lambda _name: "application/pdf")

    submit_flyer.main(
        [
            str(flyer_path),
            "-s",
            "Conad",
            "-o",
            str(output_path),
            "--api-key",
            "test-key",
        ]
    )

    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["supermarket"] == "Conad"
    assert result["products_count"] == 1
    assert result["products"][0]["name"] == "Pasta Barilla"
    assert result["products"][0]["format_label"] == "500 g"
