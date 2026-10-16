"""Use cases for local inference and production draft persistence."""
from datetime import datetime, timezone
from collections.abc import Callable
from services.extraction.local.ports import LocalFlyerRepository
from hashlib import sha256
import json
from services.extraction.local.session import LocalSession, LocalExtractionConflict


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LocalExtractionApplication:
    def __init__(self, repository: LocalFlyerRepository, drafts: Callable[[list[dict], dict], list[dict]],
                 crops: Callable[[dict], None], inspect_pdf: Callable[[dict], int],
                 clock: Callable[[], datetime] = utc_now) -> None:
        self._repository, self._drafts, self._crops, self._clock = repository, drafts, crops, clock
        self._inspect_pdf = inspect_pdf

    def claim(self, flyer_id: str, owner: str, worker: str, config: dict) -> dict:
        with self._repository.locked(flyer_id) as (cursor, flyer):
            self._repository.reject_published(cursor, flyer_id)
            if not (flyer.get('extraction_metadata') or {}).get('local_extraction') and self._repository.draft_count(cursor, flyer_id):
                raise LocalExtractionConflict('Cannot mix existing drafts with a new local session')
            if self._inspect_pdf(flyer) != config['pages_total']:
                raise LocalExtractionConflict('Page count does not match the uploaded PDF')
            if flyer.get('file_hash') != config['file_hash']:
                raise LocalExtractionConflict('PDF fingerprint does not match the uploaded file')
            session = LocalSession.claim(flyer['extraction_metadata'], flyer['status'],
                                         owner, worker, config, self._clock())
            self._repository.save(cursor, flyer, session)
            return session.metadata

    def heartbeat(self, flyer_id: str, owner: str, worker: str) -> dict:
        with self._repository.locked(flyer_id) as (cursor, flyer):
            session = self._authorized(flyer, owner, worker)
            session.renew(self._clock())
            self._repository.save(cursor, flyer, session)
            return session.metadata

    def chunk(self, flyer_id: str, owner: str, worker: str, index: int, products: list[dict]) -> dict:
        digest = sha256(json.dumps(products, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self._repository.locked(flyer_id) as (cursor, flyer):
            session = self._authorized(flyer, owner, worker)
            if session.accept(index, digest):
                self._validate_pages(session, index, products)
                self._repository.insert_offers(cursor, self._drafts(products, flyer))
                session.commit(index, digest, len(products))
            session.metadata['partial_products_count'] = self._repository.draft_count(cursor, flyer_id)
            session.metadata['products_found'] = session.metadata['partial_products_count']
            session.renew(self._clock())
            self._repository.save(cursor, flyer, session)
        self._crops(flyer)
        return session.metadata

    def finish(self, flyer_id: str, owner: str, worker: str) -> dict:
        with self._repository.locked(flyer_id) as (_, flyer):
            if flyer['status'] == 'done' and flyer.get('extraction_metadata', {}).get('execution_mode') == 'local':
                return flyer['extraction_metadata']
            self._authorized(flyer, owner, worker)
        self._crops(flyer)
        with self._repository.locked(flyer_id) as (cursor, flyer):
            session = self._authorized(flyer, owner, worker)
            session.finish(self._clock())
            self._repository.save(cursor, flyer, session, status='done')
            return session.metadata

    def fail(self, flyer_id: str, owner: str, worker: str, message: str) -> dict:
        with self._repository.locked(flyer_id) as (cursor, flyer):
            session = self._authorized(flyer, owner, worker)
            session.metadata['local_extraction']['lease_until'] = self._clock().isoformat()
            self._repository.save(cursor, flyer, session, status='error', error=message)
            return session.metadata

    def _authorized(self, flyer: dict, owner: str, worker: str) -> LocalSession:
        if flyer['status'] != 'processing':
            raise LocalExtractionConflict('Flyer is not processing')
        session = LocalSession(flyer.get('extraction_metadata') or {})
        session.authorize(owner, worker, self._clock())
        return session

    def _validate_pages(self, session: LocalSession, index: int, products: list[dict]) -> None:
        m = session.metadata
        first = (index-1)*m['chunk_size_pages']+1
        last = min(first+m['chunk_size_pages']-1, m['pages_total'])
        for product in products:
            page = product.get('source_page')
            if type(page) is not int or not first <= page <= last:
                raise LocalExtractionConflict('Every product must reference an absolute page in this chunk')
