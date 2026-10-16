from datetime import datetime, timedelta, timezone
import pytest
from services.extraction.local.session import LocalSession, LocalExtractionConflict

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)
CONFIG = dict(model='gemma4:26b-mlx', pages_total=3, chunk_size_pages=1, file_hash='a'*64)


def session(worker='one', now=NOW):
    return LocalSession.claim({}, 'pending', 'admin', worker, CONFIG, now)


def test_claim_tracks_progress_and_expiring_lease():
    value = session()
    assert value.metadata['next_chunk_index'] == 1
    assert value.metadata['execution_mode'] == 'local'
    assert value.metadata['local_extraction']['lease_until'] > NOW.isoformat()


def test_active_owner_cannot_be_replaced():
    with pytest.raises(LocalExtractionConflict):
        LocalSession.claim(session().metadata, 'processing', 'admin', 'two', CONFIG, NOW)


def test_expired_worker_is_fenced_and_checkpoint_preserved():
    value = session()
    value.commit(1, 'digest', 2)
    later = NOW + timedelta(minutes=11)
    resumed = LocalSession.claim(value.metadata, 'processing', 'admin', 'two', CONFIG, later)
    assert resumed.metadata['next_chunk_index'] == 2
    with pytest.raises(LocalExtractionConflict):
        resumed.authorize('admin', 'one', later)


def test_chunk_replay_is_idempotent_but_changed_payload_rejected():
    value = session()
    assert value.accept(1, 'digest')
    value.commit(1, 'digest', 2)
    assert not value.accept(1, 'digest')
    with pytest.raises(LocalExtractionConflict):
        value.accept(1, 'changed')
    with pytest.raises(LocalExtractionConflict):
        value.accept(3, 'digest')


def test_cannot_finish_partial_or_empty_extraction():
    with pytest.raises(LocalExtractionConflict):
        session().finish(NOW)
    value = session()
    for i in range(1, 4):
        value.commit(i, str(i), 0)
    with pytest.raises(LocalExtractionConflict):
        value.finish(NOW)


def test_finish_requires_every_chunk_and_records_duration():
    value = session()
    for i in range(1, 4):
        value.commit(i, str(i), 1)
    value.metadata['partial_products_count'] = 3
    value.finish(NOW + timedelta(seconds=30))
    assert value.metadata['total_seconds'] == 30
    assert value.metadata['resume_available'] is False


def test_resume_rejects_changed_model_or_pdf():
    value = session()
    for field in ('model', 'file_hash', 'chunk_size_pages', 'pages_total'):
        altered = {**CONFIG, field: 'changed'}
        with pytest.raises(LocalExtractionConflict):
            LocalSession.claim(value.metadata, 'error', 'admin', 'one', altered, NOW)


def test_cloud_extraction_cannot_be_claimed():
    with pytest.raises(LocalExtractionConflict):
        LocalSession.claim({}, 'processing', 'admin', 'one', CONFIG, NOW)
