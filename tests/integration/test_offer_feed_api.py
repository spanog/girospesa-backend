"""Exercise real public HTTP endpoints and SQL pagination against one filter contract."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import uuid

import httpx
import pytest


@pytest.fixture
async def feed_client():
    from main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client


@pytest.fixture
async def offer_feed(supabase_client, clean_db, feed_client):
    markets = _seed_markets(supabase_client)
    offers = supabase_client.table('offers').insert([
        _offer(market, i) for market in markets for i in range(24)
    ]).execute().data
    response = await feed_client.post('/guest-location', json={'municipality_code': '015146'})
    assert response.status_code == 204
    yield feed_client, markets, offers


def _seed_markets(client):
    return client.table('supermarkets').insert([
        {'name': f'Feed {i}', 'slug': f'feed-{uuid.uuid4().hex[:8]}',
         'municipality_code': '015146' if i < 2 else '080061'}
        for i in range(3)
    ]).execute().data


def _offer(market, index):
    today = datetime.now(ZoneInfo('Europe/Rome')).date()
    return {
        'supermarket_id': market['id'], 'supermarket_name': market['name'],
        'name': f'{"Acqua" if index % 2 == 0 else "Pasta"} {index}', 'brand': market['name'],
        'category': 'bevande' if index % 2 == 0 else 'dispensa',
        'subcategory': 'Acqua e Bibite' if index % 4 == 0 else 'Altro',
        'offer_key': uuid.uuid4().hex, 'price_offer': 1.99,
        'format': {'tipo': 'confezione_singola'}, 'format_key': 'confezione_singola',
        'format_label': 'Confezione singola', 'is_confirmed': True,
        'is_reviewed': True, 'offer_kind': 'published_target',
        'valid_from': (today - timedelta(days=1)).isoformat(),
        'valid_to': (today + timedelta(days=20)).isoformat(),
    }


def _filters(case, markets):
    if case == 'single':
        return [('supermarket_id', markets[1]['id'])]
    if case == 'multiple':
        return [('supermarket_ids', market['id']) for market in markets[:2]]
    if case == 'search':
        return [('q', 'Acqua')]
    if case == 'category':
        return [('category', 'bevande')]
    if case == 'combined':
        return [('supermarket_id', markets[1]['id']), ('q', 'Acqua'),
                ('category', 'bevande'), ('subcategory', 'Acqua e Bibite')]
    return []


def _expected(offers, filters, markets):
    values = dict(filters)
    store_ids = [value for key, value in filters if key in {'supermarket_id', 'supermarket_ids'}]
    return [o for o in offers if o['supermarket_id'] in {m['id'] for m in markets[:2]}
            and (not store_ids or o['supermarket_id'] in store_ids)
            and (not values.get('q') or values['q'].lower() in o['name'].lower())
            and (not values.get('category') or o['category'] == values['category'])
            and (not values.get('subcategory') or o['subcategory'] == values['subcategory'])]


@pytest.mark.parametrize('case', ['all', 'single', 'multiple', 'search', 'category', 'combined'])
async def test_discovery_and_next_pages_obey_identical_filters(offer_feed, case):
    client, markets, offers = offer_feed
    filters = _filters(case, markets)
    expected = _expected(offers, filters, markets)
    params = [*filters, ('limit', '20'), ('offset', '0')]
    first = (await client.get('/offers/discovery', params=params)).json()
    assert len(first['items']) == min(20, len(expected))
    assert first['total'] == len(expected)
    assert {m['id'] for m in first['supermarkets']} == {m['id'] for m in markets[:2]}
    assert (await client.get('/offers', params=params)).json()['items'] == first['items']
    collected = await _collect_pages(client, filters, first)
    assert len(collected) == len({o['id'] for o in collected}) == len(expected)
    assert {o['id'] for o in collected} == {o['id'] for o in expected}
    assert sum(first['counts_by_supermarket_id'].values()) == len(expected)
    repeated = (await client.get('/offers/discovery', params=params)).json()
    assert [o['id'] for o in repeated['items']] == [o['id'] for o in first['items']]


async def _collect_pages(client, filters, first):
    collected = list(first['items'])
    page = first
    while page['nextPage'] is not None:
        offset = page['nextPage']
        result = await client.get('/offers', params=[*filters, ('limit', '20'), ('offset', str(offset))])
        assert result.status_code == 200
        page = result.json()
        assert page['total'] == first['total']
        assert len(page['items']) == min(20, first['total'] - offset)
        collected.extend(page['items'])
    return collected


@pytest.mark.parametrize('path', ['/offers', '/offers/discovery'])
async def test_empty_and_out_of_area_filters_do_not_leak_offers(offer_feed, path):
    client, markets, _ = offer_feed
    for params in [{'q': 'zzzznessunrisultato'}, {'supermarket_id': markets[2]['id']}, {'offset': 999}]:
        result = await client.get(path, params=params)
        assert result.status_code == 200
        assert result.json()['items'] == []
        assert result.json()['nextPage'] is None


@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 101}, {'offset': -1}])
async def test_public_offer_pagination_rejects_invalid_bounds(offer_feed, params):
    client, _, _ = offer_feed
    for path in ['/offers', '/offers/discovery']:
        assert (await client.get(path, params=params)).status_code == 422


@pytest.fixture
def authenticated_reader(supabase_client):
    from main import app
    from core.auth import get_optional_user_id
    user = supabase_client.auth.admin.create_user({
        'email': f'feed-{uuid.uuid4().hex}@local.test',
        'password': 'FixturePassword123!', 'email_confirm': True,
    }).user
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_optional_user_id] = lambda: user.id
    yield user.id
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    supabase_client.auth.admin.delete_user(user.id)


@pytest.mark.parametrize('role', ['customer', 'supermarket_manager', 'admin'])
async def test_authenticated_readers_use_profile_area_and_same_filter_contract(
    offer_feed, authenticated_reader, supabase_client, role,
):
    client, markets, _ = offer_feed
    supabase_client.table('user_profiles').update({
        'municipality_code': '015146', 'max_distance_km': 10, 'role': role,
        'managed_supermarket_id': markets[0]['id'] if role == 'supermarket_manager' else None,
    }).eq('id', authenticated_reader).execute()
    # A different signed guest cookie must never replace the account's area.
    assert (await client.post('/guest-location', json={'municipality_code': '080061'})).status_code == 204
    params = {'supermarket_id': markets[1]['id'], 'limit': 20}
    response = await client.get('/offers/discovery', params=params)
    assert response.status_code == 200
    first = response.json()
    assert first['total'] == 24 and len(first['items']) == 20
    assert all(o['supermarket_id'] == markets[1]['id'] for o in first['items'])
    assert (await client.get('/offers', params=params)).json()['items'] == first['items']
