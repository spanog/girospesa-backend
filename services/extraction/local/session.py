"""Pure state transitions for a leased, resumable local extraction."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from copy import deepcopy

LEASE_SECONDS = 600
MODE = 'local'


class LocalExtractionConflict(ValueError):
    """Requested transition would replace or corrupt an extraction."""


@dataclass
class LocalSession:
    metadata: dict[str, Any]

    @classmethod
    def claim(cls, metadata: dict | None, status: str, owner: str, worker: str,
              config: dict, now: datetime) -> "LocalSession":
        current = deepcopy(metadata) if isinstance(metadata, dict) else {}
        lease = current.get('local_extraction')
        if status == 'done' or (not lease and status not in {'pending', 'error'}):
            raise LocalExtractionConflict('Flyer cannot be claimed for local extraction')
        if not lease and current.get('last_completed_chunk'):
            raise LocalExtractionConflict('Cannot mix cloud and local checkpoints')
        result = cls(current) if lease else cls.initial(config, now)
        result.validate_claim(owner, worker, config, now)
        result.metadata['local_extraction'].update(owner=owner, worker_id=worker)
        result.renew(now)
        return result

    @classmethod
    def initial(cls, config: dict, now: datetime) -> "LocalSession":
        size, pages = config['chunk_size_pages'], config['pages_total']
        return cls(dict(execution_mode=MODE, stage='extracting', **config,
            chunks_total=(pages+size-1)//size, chunks_completed=0, last_completed_chunk=0,
            next_chunk_index=1, pages_processed=0, progress_percent=0,
            products_found=0, partial_products_count=0, products_raw_count=0,
            extraction_started_at=now.isoformat(), local_extraction=dict(config=config, receipts={})))

    def validate_claim(self, owner: str, worker: str, config: dict, now: datetime) -> None:
        lease = self.metadata['local_extraction']
        if lease['config'] != config:
            raise LocalExtractionConflict('Resume requires identical PDF, model and chunk configuration')
        if self.active(now) and (lease.get('owner'), lease.get('worker_id')) != (owner, worker):
            raise LocalExtractionConflict('Another local worker owns this flyer')

    def active(self, now: datetime) -> bool:
        until = self.metadata.get('local_extraction', {}).get('lease_until')
        return bool(until and datetime.fromisoformat(until) > now)

    def authorize(self, owner: str, worker: str, now: datetime) -> None:
        lease = self.metadata.get('local_extraction', {})
        if not self.active(now) or (lease.get('owner'), lease.get('worker_id')) != (owner, worker):
            raise LocalExtractionConflict('Local lease expired or belongs to another worker; claim again')

    def renew(self, now: datetime) -> None:
        self.metadata['local_extraction']['lease_until'] = (now+timedelta(seconds=LEASE_SECONDS)).isoformat()
        self.metadata['resume_available'] = True
        self.update_page_progress()

    def accept(self, index: int, digest: str) -> bool:
        existing = self.metadata['local_extraction']['receipts'].get(str(index))
        if existing is not None:
            if existing != digest:
                raise LocalExtractionConflict('Chunk already committed with different contents')
            return False
        if not 1 <= index <= self.metadata['chunks_total'] or index != self.metadata['next_chunk_index']:
            raise LocalExtractionConflict('Chunks must be committed in sequence')
        return True

    def commit(self, index: int, digest: str, count: int) -> None:
        self.metadata['local_extraction']['receipts'][str(index)] = digest
        self.metadata.update(chunks_completed=index, last_completed_chunk=index, next_chunk_index=index+1)
        self.metadata['products_raw_count'] += count
        self.update_page_progress()

    def update_page_progress(self) -> None:
        m = self.metadata
        pages = min(m['chunks_completed']*m['chunk_size_pages'], m['pages_total'])
        m.update(pages_processed=pages, progress_percent=round(100*pages/m['pages_total']),
            current_chunk_start=min(pages+1, m['pages_total']),
            current_chunk_end=min(pages+m['chunk_size_pages'], m['pages_total']))

    def finish(self, now: datetime) -> None:
        m = self.metadata
        if m['chunks_completed'] != m['chunks_total'] or m['partial_products_count'] < 1:
            raise LocalExtractionConflict('All chunks and at least one valid draft are required')
        m.update(stage='complete', resume_available=False, next_chunk_index=None,
            extraction_finished_at=now.isoformat(),
            total_seconds=(now-datetime.fromisoformat(m['extraction_started_at'])).total_seconds())
