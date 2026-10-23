"""Real PostgreSQL/API lifecycle; inference and Storage remain mocked."""
from datetime import datetime, timedelta, timezone
from functools import partial
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from unittest.mock import Mock
import fitz
import os
import psycopg2
import httpx
import pytest
from fastapi import FastAPI
from api.routers.local_extraction import router, application
from core.auth import get_current_user_profile
from services.extraction.local.application import LocalExtractionApplication
from services.extraction.local.repository import LocalExtractionRepository
from services.extraction.local.session import LocalExtractionConflict
from services.extraction.service import DownloadedFlyer, ExtractionService
from services.repositories.flyer_source_cache import FlyerSourceCache, FlyerSourceIntegrityError

OWNER = str(uuid4())
WORKER = str(uuid4())
CONFIG = dict(model='test-local-model', pages_total=2, chunk_size_pages=1,
              file_hash='a'*64, prompt_hash='b'*64, image_dpi=150,
              context_tokens=32768, max_output_tokens=16384)
PRODUCT = dict(name='Latte', brand='Marca', price_current=1.29, source_page=1,
               packshot_bbox=dict(x_min=100, y_min=200, x_max=400, y_max=700))


@pytest.fixture
def fixture(supabase_client, clean_db):
    store = supabase_client.table('supermarkets').insert(dict(name='Local worker test',
        slug='local-'+uuid4().hex, municipality_code='015146')).execute().data[0]
    flyer = supabase_client.table('flyers').insert(dict(file_url='test/flyer.pdf',
        file_name='flyer.pdf', file_type='pdf', file_hash='a'*64, flyer_kind='source',
        supermarket_id=store['id'], supermarket_name=store['name'],
        valid_from='2026-10-01', valid_to='2026-10-31')).execute().data[0]
    repository = LocalExtractionRepository(partial(psycopg2.connect, os.environ['DB_DSN']))
    drafts = ExtractionService(provider=object()).prepare_local_offer_rows
    app_service = LocalExtractionApplication(repository, drafts, lambda flyer: None, lambda flyer: 2)
    app = FastAPI()
    app.include_router(router, prefix='/flyers')
    app.dependency_overrides[application] = lambda: app_service
    app.dependency_overrides[get_current_user_profile] = lambda: dict(id=OWNER, role='admin')
    return app, app_service, flyer, supabase_client


@pytest.mark.asyncio
async def test_local_lifecycle_reuses_pdf_across_claim_chunks_and_finish(fixture, tmp_path, monkeypatch):
    app, _, flyer, sb = fixture
    content = _two_page_pdf()
    config = dict(CONFIG, file_hash=sha256(content).hexdigest())
    sb.table('flyers').update({'file_hash': config['file_hash']}).eq('id', flyer['id']).execute()
    extraction, download = _cached_extraction(sb, tmp_path, content, monkeypatch)
    repository = LocalExtractionRepository(partial(psycopg2.connect, os.environ['DB_DSN']))
    service = LocalExtractionApplication(repository, extraction.prepare_local_offer_rows,
                                         extraction.save_local_packshots, extraction.local_pdf_page_count)
    app.dependency_overrides[application] = lambda: service
    await _run_cached_pdf_lifecycle(app, flyer['id'], config)
    download.assert_called_once()
    assert len(sb.table('offers').select('id').eq('flyer_id', flyer['id']).execute().data) == 2
    assert list(tmp_path.glob('*.bin'))[0].read_bytes() == content


def _two_page_pdf():
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "First offer")
        document.new_page().insert_text((40, 40), "Second offer")
        return document.tobytes()


def _cached_extraction(sb, tmp_path, content, monkeypatch):
    extraction = ExtractionService(provider=object(), supabase_factory=lambda: sb,
                                   source_cache=FlyerSourceCache(tmp_path, 1024 * 1024, 60))
    download = Mock(return_value=DownloadedFlyer(content))
    monkeypatch.setattr(extraction, '_download_file', download)
    monkeypatch.setattr(extraction, '_upload_packshot', Mock())
    return extraction, download


async def _run_cached_pdf_lifecycle(app, flyer_id, config):
    prefix = f'/flyers/{flyer_id}/local-extraction'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(prefix+'/claim', json=dict(worker_id=WORKER, **config))).status_code == 200
        for index in (1, 2):
            body = dict(worker_id=WORKER, products=[dict(PRODUCT, source_page=index, name=f'Offer {index}')])
            assert (await client.put(prefix+f'/chunks/{index}', json=body)).status_code == 200
        assert (await client.post(prefix+'/finish', json=dict(worker_id=WORKER))).status_code == 200


@pytest.mark.asyncio
async def test_cache_integrity_failure_rejects_claim_without_mutating_session(fixture):
    app, service, flyer, sb = fixture
    service._inspect_pdf = Mock(side_effect=FlyerSourceIntegrityError('Source fingerprint mismatch'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f"/flyers/{flyer['id']}/local-extraction/claim",
                                     json=dict(worker_id=WORKER, **CONFIG))
    assert response.status_code == 409
    assert response.json()['detail'] == 'Source fingerprint mismatch'
    stored = sb.table('flyers').select('status, extraction_metadata').eq('id', flyer['id']).single().execute().data
    assert stored['status'] == flyer['status']
    assert stored['extraction_metadata'] == flyer['extraction_metadata']


@pytest.mark.asyncio
async def test_api_chunk_replay_resume_finish_and_cloud_rejection(fixture):
    app, service, flyer, sb = fixture
    prefix = f"/flyers/{flyer['id']}/local-extraction"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(prefix+'/claim', json=dict(worker_id=WORKER, **CONFIG))
        assert response.status_code == 200
        assert response.json()['next_chunk_index'] == 1
        assert (await client.post(prefix+'/finish', json=dict(worker_id=WORKER))).status_code == 409
        body = dict(worker_id=WORKER, products=[PRODUCT])
        assert (await client.put(prefix+'/chunks/1', json=body)).status_code == 200
        assert (await client.put(prefix+'/chunks/1', json=body)).status_code == 200
        assert len(sb.table('offers').select('id').eq('flyer_id', flyer['id']).execute().data) == 1
        changed = dict(worker_id=WORKER, products=[dict(PRODUCT, price_current=9.99)])
        assert (await client.put(prefix+'/chunks/1', json=changed)).status_code == 409
        assert (await client.post(prefix+'/claim', json=dict(worker_id=str(uuid4()), **CONFIG))).status_code == 409
        wrong_page = dict(worker_id=WORKER, products=[PRODUCT])
        assert (await client.put(prefix+'/chunks/2', json=wrong_page)).status_code == 409
        await client.post(prefix+'/fail', json=dict(worker_id=WORKER, message='Interrupted locally'))
        assert sb.table('flyers').select('status').eq('id', flyer['id']).single().execute().data['status'] == 'error'
        resumed = await client.post(prefix+'/claim', json=dict(worker_id=WORKER, **CONFIG))
        assert resumed.json()['next_chunk_index'] == 2
        second = dict(worker_id=WORKER, products=[dict(PRODUCT, name='Pasta', source_page=2)])
        assert (await client.put(prefix+'/chunks/2', json=second)).status_code == 200
        done = await client.post(prefix+'/finish', json=dict(worker_id=WORKER))
        assert done.status_code == 200 and done.json()['chunks_completed'] == 2
        assert (await client.post(prefix+'/finish', json=dict(worker_id=WORKER))).status_code == 200
        stored = sb.table('flyers').select('*').eq('id', flyer['id']).single().execute().data
        assert stored['status'] == 'done' and stored['products_count'] == 2
        rows = sb.table('offers').select('*').eq('flyer_id', flyer['id']).execute().data
        assert all(not row['is_confirmed'] and row['packshot_bbox_version'] == 2 for row in rows)
        assert rows[0]['packshot_bbox'] == [100, 200, 400, 700]


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['customer', 'supermarket_manager'])
async def test_worker_api_requires_real_admin_role(fixture, role):
    app, _, flyer, _ = fixture
    app.dependency_overrides[get_current_user_profile] = lambda: dict(id=OWNER, role=role)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f"/flyers/{flyer['id']}/local-extraction/claim", json=dict(worker_id=WORKER, **CONFIG))
    assert response.status_code == 403


def test_concurrent_claim_and_replay_are_serialized(fixture):
    _, service, flyer, sb = fixture
    def claim(worker):
        try:
            service.claim(flyer['id'], OWNER, worker, CONFIG)
            return worker
        except LocalExtractionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [result for result in pool.map(claim, [WORKER, str(uuid4())]) if result]
    assert len(winners) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.chunk(flyer['id'], OWNER, winners[0], 1, [PRODUCT]), range(2)))
    assert all(result['last_completed_chunk'] == 1 for result in results)
    assert len(sb.table('offers').select('id').eq('flyer_id', flyer['id']).execute().data) == 1


def test_chunk_and_checkpoint_rollback_together(fixture):
    _, service, flyer, sb = fixture
    service.claim(flyer['id'], OWNER, WORKER, CONFIG)
    original = service._repository.save
    def fail(*args, **kwargs):
        raise RuntimeError('Simulated failure before checkpoint commit')
    service._repository.save = fail
    with pytest.raises(RuntimeError):
        service.chunk(flyer['id'], OWNER, WORKER, 1, [PRODUCT])
    service._repository.save = original
    assert not sb.table('offers').select('id').eq('flyer_id', flyer['id']).execute().data
    metadata = sb.table('flyers').select('extraction_metadata').eq('id', flyer['id']).single().execute().data['extraction_metadata']
    assert metadata['last_completed_chunk'] == 0


@pytest.mark.asyncio
async def test_capability_contract_and_box_validation(fixture, request):
    from tests.snapshot_utils import assert_matches_json_snapshot
    app, service, flyer, _ = fixture
    service.claim(flyer['id'], OWNER, WORKER, CONFIG)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/flyers/local-extraction/capabilities')
        assert response.status_code == 200
        assert_matches_json_snapshot(request, 'local_extraction_capabilities', response.json())
        invalid = dict(worker_id=WORKER, products=[dict(PRODUCT, packshot_bbox=[100, 200, 400, 700])])
        assert (await client.put(f"/flyers/{flyer['id']}/local-extraction/chunks/1", json=invalid)).status_code == 422


def test_claim_rejects_cloud_drafts_and_mismatched_original_page_count(fixture):
    _, service, flyer, sb = fixture
    with pytest.raises(LocalExtractionConflict, match='Page count'):
        service.claim(flyer['id'], OWNER, WORKER, dict(CONFIG, pages_total=3))
    rows = service._drafts([PRODUCT], flyer)
    sb.table('offers').insert(rows).execute()
    with pytest.raises(LocalExtractionConflict, match='existing drafts'):
        service.claim(flyer['id'], OWNER, WORKER, CONFIG)


def test_expired_lease_can_be_replaced_and_old_worker_is_fenced(fixture):
    _, service, flyer, sb = fixture
    service.claim(flyer['id'], OWNER, WORKER, CONFIG)
    now = datetime.now(timezone.utc)
    service._clock = lambda: now+timedelta(minutes=11)
    replacement = str(uuid4())
    service.claim(flyer['id'], OWNER, replacement, CONFIG)
    with pytest.raises(LocalExtractionConflict):
        service.chunk(flyer['id'], OWNER, WORKER, 1, [PRODUCT])
    service.chunk(flyer['id'], OWNER, replacement, 1, [PRODUCT])
    assert len(sb.table('offers').select('id').eq('flyer_id', flyer['id']).execute().data) == 1


@pytest.mark.asyncio
async def test_cloud_endpoint_refuses_local_session(fixture):
    from api.routers import flyers
    from core.auth import require_admin_or_manager
    from unittest.mock import patch
    app, service, flyer, sb = fixture
    app.include_router(flyers.router, prefix='/flyers')
    app.dependency_overrides[require_admin_or_manager] = lambda: dict(id=OWNER, role='admin')
    service.claim(flyer['id'], OWNER, WORKER, CONFIG)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        with patch.object(flyers, 'get_supabase', return_value=sb):
            response = await client.post(f"/flyers/{flyer['id']}/extract", json={})
    assert response.status_code == 409
    assert 'local worker' in response.json()['detail']


@pytest.mark.asyncio
async def test_cloud_start_still_works_and_does_not_start_twice(fixture):
    from api.routers import flyers
    from core.auth import require_admin_or_manager
    from unittest.mock import patch
    app, _, flyer, sb = fixture
    app.include_router(flyers.router, prefix='/flyers')
    app.dependency_overrides[require_admin_or_manager] = lambda: dict(id=OWNER, role='admin')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        with patch.object(flyers, 'get_supabase', return_value=sb), patch('services.extraction.service.ExtractionService') as cloud:
            first = await client.post(f"/flyers/{flyer['id']}/extract", json={})
            second = await client.post(f"/flyers/{flyer['id']}/extract", json={})
    assert first.status_code == 202
    assert second.status_code == 409
    cloud.return_value.run.assert_called_once_with(flyer['id'])
