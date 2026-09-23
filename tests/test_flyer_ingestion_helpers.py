from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scripts.flyer_ingestion.config import FlyerSourceConfigError, load_sources
from scripts.flyer_ingestion import cli
from scripts.flyer_ingestion.pdf import build_pdf_from_images
from scripts.flyer_ingestion.state import load_records, record_result
from scripts.flyer_ingestion.workflow import FlyerCandidate, FlyerIngestionPolicy, IngestionAction


def test_config_loads_an_enabled_source(tmp_path: Path):
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n  - id: coop\n    listing_url: https://example.test/flyers\n"
        "    target_supermarket_ids: [store-1]\n    enabled: true\n",
        encoding="utf-8",
    )

    source = load_sources(config)[0]

    assert source.source_id == "coop"
    assert source.target_supermarket_ids == ("store-1",)


def test_config_rejects_duplicate_target_ids(tmp_path: Path):
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n  - id: coop\n    listing_url: https://example.test/flyers\n"
        "    target_supermarket_ids: [store-1, store-1]\n",
        encoding="utf-8",
    )

    with pytest.raises(FlyerSourceConfigError, match="must not repeat"):
        load_sources(config)


def test_state_records_the_preflight_fingerprint(tmp_path: Path):
    state_path = tmp_path / "girospesa-volantini.json"

    record_result(
        state_path,
        source_id="eurospin-cittanova",
        fingerprint="a" * 64,
        status="known",
        flyer_id=None,
    )

    records = load_records(state_path)
    assert records["eurospin-cittanova:" + "a" * 64].status == "known"


def test_build_pdf_from_ordered_viewer_images(tmp_path: Path):
    first_page = tmp_path / "001.png"
    second_page = tmp_path / "002.png"
    output = tmp_path / "volantino.pdf"
    Image.new("RGB", (120, 240), "red").save(first_page)
    Image.new("RGB", (240, 120), "blue").save(second_page)

    page_count = build_pdf_from_images((first_page, second_page), output)

    assert page_count == 2
    assert output.read_bytes().startswith(b"%PDF-")


def test_cli_validates_a_downloaded_pdf(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    image = tmp_path / "page.png"
    output = tmp_path / "downloaded.pdf"
    Image.new("RGB", (120, 240), "green").save(image)
    build_pdf_from_images((image,), output)

    cli._validate_pdf(output, expected_pages=1)

    assert '"page_count": 1' in capsys.readouterr().out


def test_first_run_known_candidate_never_advances_to_upload():
    action = FlyerIngestionPolicy().candidate_action(
        FlyerCandidate(valid_from="2026-09-22", valid_to="2026-09-30"),
        "known",
    )

    assert action is IngestionAction.RECORD_KNOWN


def test_candidate_without_dates_stops_before_preflight_or_upload():
    action = FlyerIngestionPolicy().candidate_action(FlyerCandidate(None, "2026-09-30"), None)

    assert action is IngestionAction.REQUEST_DATES


def test_gemini_resume_retries_are_limited_and_back_off():
    policy = FlyerIngestionPolicy()

    assert [policy.retry_delay_seconds(attempt) for attempt in range(4)] == [2, 10, 30, None]


def test_done_flyer_requires_at_least_one_draft_before_confirmation():
    policy = FlyerIngestionPolicy()

    assert policy.extraction_action("done", 0) is IngestionAction.STOP
    assert policy.extraction_action("done", 1) is IngestionAction.CONFIRM
