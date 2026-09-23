"""Read-only duplicate checks for Codex-managed flyer ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from io import BytesIO

import fitz
from PIL import Image


class FlyerPreflightStatus(StrEnum):
    KNOWN = "known"
    PARTIAL = "partial"
    NEW = "new"
    INDETERMINATE = "indeterminate"


class DocumentFingerprintError(ValueError):
    """Raised when a flyer cannot be rendered into a stable fingerprint."""


@dataclass(frozen=True)
class DocumentFingerprint:
    file_hash: str
    page_hashes: tuple[int, ...]


@dataclass(frozen=True)
class ExistingFlyerDocument:
    flyer_id: str
    target_ids: frozenset[str]
    content: bytes | None


@dataclass(frozen=True)
class FlyerPreflightDecision:
    status: FlyerPreflightStatus
    file_hash: str
    processed_target_ids: frozenset[str]
    upload_target_ids: frozenset[str]


class FlyerDocumentFingerprinter:
    """Build file and visual fingerprints without persisting any data."""

    def fingerprint(self, content: bytes) -> DocumentFingerprint:
        page_hashes = self._pdf_page_hashes(content) if content.startswith(b"%PDF-") else self._image_page_hashes(content)
        return DocumentFingerprint(file_hash=sha256(content).hexdigest(), page_hashes=page_hashes)

    def _pdf_page_hashes(self, content: bytes) -> tuple[int, ...]:
        try:
            document = fitz.open(stream=content, filetype="pdf")
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            raise DocumentFingerprintError("PDF cannot be rendered") from exc
        try:
            hashes = tuple(self._pdf_page_hash(page) for page in document)
        finally:
            document.close()
        if not hashes:
            raise DocumentFingerprintError("PDF has no pages")
        return hashes

    def _pdf_page_hash(self, page: fitz.Page) -> int:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False)
        image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
        return _difference_hash(image)

    def _image_page_hashes(self, content: bytes) -> tuple[int, ...]:
        try:
            with Image.open(BytesIO(content)) as image:
                return (_difference_hash(image.convert("L")),)
        except (OSError, ValueError) as exc:
            raise DocumentFingerprintError("Image cannot be rendered") from exc


class FlyerIngestionPreflightService:
    """Classify targets before a source file reaches Storage."""

    def __init__(self, fingerprinter: FlyerDocumentFingerprinter | None = None):
        self._fingerprinter = fingerprinter or FlyerDocumentFingerprinter()

    def decide(
        self,
        candidate_content: bytes,
        target_ids: frozenset[str],
        exact_target_ids: frozenset[str],
        existing_documents: tuple[ExistingFlyerDocument, ...],
    ) -> FlyerPreflightDecision:
        candidate = self._fingerprinter.fingerprint(candidate_content)
        if target_ids.issubset(exact_target_ids):
            return self._decision(FlyerPreflightStatus.KNOWN, candidate, target_ids, target_ids)
        remaining_ids = target_ids - exact_target_ids
        visual_ids, unavailable = self._visual_conflicts(candidate, existing_documents, remaining_ids)
        processed_ids = exact_target_ids | visual_ids
        if unavailable:
            return self._decision(FlyerPreflightStatus.INDETERMINATE, candidate, target_ids, processed_ids)
        return self._classified_decision(candidate, target_ids, processed_ids)

    def _visual_conflicts(
        self,
        candidate: DocumentFingerprint,
        documents: tuple[ExistingFlyerDocument, ...],
        remaining_target_ids: frozenset[str],
    ) -> tuple[frozenset[str], bool]:
        processed: set[str] = set()
        unavailable = False
        for document in documents:
            if not document.target_ids.intersection(remaining_target_ids):
                continue
            if document.content is None:
                unavailable = True
                continue
            try:
                existing = self._fingerprinter.fingerprint(document.content)
            except DocumentFingerprintError:
                unavailable = True
                continue
            if _documents_match(candidate, existing):
                processed.update(document.target_ids)
        return frozenset(processed), unavailable

    def _classified_decision(
        self,
        candidate: DocumentFingerprint,
        target_ids: frozenset[str],
        processed_ids: frozenset[str],
    ) -> FlyerPreflightDecision:
        if not processed_ids:
            return self._decision(FlyerPreflightStatus.NEW, candidate, target_ids, processed_ids)
        status = FlyerPreflightStatus.KNOWN if target_ids.issubset(processed_ids) else FlyerPreflightStatus.PARTIAL
        return self._decision(status, candidate, target_ids, processed_ids)

    def _decision(
        self,
        status: FlyerPreflightStatus,
        fingerprint: DocumentFingerprint,
        target_ids: frozenset[str],
        processed_ids: frozenset[str],
    ) -> FlyerPreflightDecision:
        uploads = frozenset() if status is FlyerPreflightStatus.INDETERMINATE else target_ids - processed_ids
        return FlyerPreflightDecision(status, fingerprint.file_hash, processed_ids & target_ids, uploads)


def _difference_hash(image: Image.Image) -> int:
    scaled = image.resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(scaled.get_flattened_data())
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return value


def _documents_match(left: DocumentFingerprint, right: DocumentFingerprint) -> bool:
    if len(left.page_hashes) != len(right.page_hashes):
        return False
    return all(_hamming_distance(a, b) <= 4 for a, b in zip(left.page_hashes, right.page_hashes, strict=True))


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()
