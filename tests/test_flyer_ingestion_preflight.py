from __future__ import annotations

from pathlib import Path

from services.flyer_ingestion_preflight import (
    ExistingFlyerDocument,
    FlyerDocumentFingerprinter,
    FlyerIngestionPreflightService,
    FlyerPreflightStatus,
)


PDF_FIXTURE = Path(__file__).parent / "extraction_eval/fixtures/volantino-conad-ridotto.pdf"


def _candidate_pdf() -> bytes:
    return PDF_FIXTURE.read_bytes()


def test_visual_fingerprint_matches_a_pdf_with_different_file_hash():
    content = _candidate_pdf()
    copied = content + b"\n% copied by ingestion preflight\n"

    original = FlyerDocumentFingerprinter().fingerprint(content)
    duplicate = FlyerDocumentFingerprinter().fingerprint(copied)

    assert original.file_hash != duplicate.file_hash
    assert original.page_hashes == duplicate.page_hashes


def test_preflight_returns_known_when_visual_copy_covers_every_target():
    content = _candidate_pdf()
    existing = ExistingFlyerDocument("existing-1", frozenset({"sup-1"}), content + b"\n% copied\n")

    decision = FlyerIngestionPreflightService().decide(
        content,
        frozenset({"sup-1"}),
        frozenset(),
        (existing,),
    )

    assert decision.status is FlyerPreflightStatus.KNOWN
    assert decision.upload_target_ids == frozenset()


def test_preflight_returns_partial_when_only_one_target_is_known():
    content = _candidate_pdf()

    decision = FlyerIngestionPreflightService().decide(
        content,
        frozenset({"sup-1", "sup-2"}),
        frozenset({"sup-1"}),
        (),
    )

    assert decision.status is FlyerPreflightStatus.PARTIAL
    assert decision.processed_target_ids == frozenset({"sup-1"})
    assert decision.upload_target_ids == frozenset({"sup-2"})


def test_preflight_is_indeterminate_when_a_matching_scope_cannot_be_read():
    content = _candidate_pdf()
    existing = ExistingFlyerDocument("existing-1", frozenset({"sup-1"}), None)

    decision = FlyerIngestionPreflightService().decide(
        content,
        frozenset({"sup-1"}),
        frozenset(),
        (existing,),
    )

    assert decision.status is FlyerPreflightStatus.INDETERMINATE
    assert decision.upload_target_ids == frozenset()
