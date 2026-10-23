"""Real-DB ordering rules, applied to the full candidate set before pagination."""
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import uuid

import pytest

TODAY = datetime.now(ZoneInfo('Europe/Rome')).date()


@pytest.fixture
def markets(supabase_client, clean_db):
    return supabase_client.table('supermarkets').insert([
        {'id': str(uuid.UUID(int=900 + i)), 'name': f'Balanced {i}',
         'slug': f'balanced-{i}', 'municipality_code': '015146'}
        for i in range(3)
    ]).execute().data


def _offer(store, name, category='Dispensa', **changes):
    return {
        'supermarket_id': store['id'], 'supermarket_name': store['name'],
        'name': name, 'brand': 'Marca', 'category': category,
        'offer_key': uuid.uuid4().hex, 'price_offer': 1.99,
        'format': {'tipo': 'confezione_singola'},
        'format_key': 'confezione_singola', 'format_label': 'Confezione singola',
        'is_confirmed': True, 'is_reviewed': True, 'offer_kind': 'published_target',
        'valid_from': (TODAY - timedelta(days=20)).isoformat(),
        'valid_to': (TODAY + timedelta(days=20)).isoformat(), **changes,
    }


def _page(client, markets, **filters):
    return client.rpc('nearby_public_offer_page', {
        'candidate_supermarket_ids': [store['id'] for store in markets],
        'candidate_distances_km': [float(i) for i in range(len(markets))],
        'page_limit': 100, **filters,
    }).execute().data


def _ids(rows):
    return [row['id'] for row in rows if row['id']]


def test_browsing_rotates_stores_and_categories_before_paging(supabase_client, markets):
    offers = supabase_client.table('offers').insert([
        _offer(store, f'{store["name"]}-{category}-{i}', category)
        for store in markets[:2] for category in ('Dispensa', 'Bevande') for i in range(3)
    ]).execute().data
    by_id = {offer['id']: offer for offer in offers}
    rows = _page(supabase_client, markets)
    first = [by_id[offer_id] for offer_id in _ids(rows)[:4]]
    assert Counter(o['supermarket_id'] for o in first) == {s['id']: 2 for s in markets[:2]}
    for store in markets[:2]:
        assert {o['category'] for o in first if o['supermarket_id'] == store['id']} == {'Dispensa', 'Bevande'}
    assert rows[0]['total'] == 12
    assert rows[0]['supermarket_count'] == 2
    assert rows[0]['counts_by_supermarket_id'] == {s['id']: 6 for s in markets[:2]}


def test_repeated_display_signatures_are_delayed_without_removing_offers(supabase_client, markets):
    offers = supabase_client.table('offers').insert([
        _offer(store, name) for store in markets[:2] for name in ('Pasta', 'Latte', 'Riso', 'Pane')
    ]).execute().data
    by_id = {o['id']: o for o in offers}
    ordered = [by_id[i] for i in _ids(_page(supabase_client, markets))]
    assert len(ordered) == 8
    assert len({o['name'] for o in ordered[:4]}) == 4
    assert len({o['name'] for o in ordered[4:]}) == 4
    assert {o['id'] for o in ordered} == set(by_id)


def test_brand_and_format_distinguish_display_signatures(supabase_client, markets):
    offers = supabase_client.table('offers').insert([
        _offer(markets[0], 'Latte', brand=brand, format_key=format_key)
        for brand, format_key in [('A', '1L'), ('B', '1L'), ('A', '500ml')]]
        + [_offer(markets[1], '  LATTE  ', brand='a', format_key='1L')]
    ).execute().data
    by_id = {o['id']: o for o in offers}
    first = [by_id[i] for i in _ids(_page(supabase_client, markets))[:3]]
    assert len({(o['brand'].lower(), o['format_key']) for o in first}) == 3


def test_recent_start_is_preferred_only_within_its_category_lane(supabase_client, markets):
    offers = supabase_client.table('offers').insert([
        _offer(markets[0], 'Old'),
        _offer(markets[0], 'Unknown', valid_from=None),
        _offer(markets[0], 'Recent', valid_from=(TODAY - timedelta(days=6)).isoformat()),
        _offer(markets[0], 'Another category', category='Bevande'),
    ]).execute().data
    by_id = {o['id']: o for o in offers}
    ordered = [by_id[i]['name'] for i in _ids(_page(supabase_client, markets))]
    assert set(ordered[:2]) == {'Recent', 'Another category'}
    assert ordered.index('Recent') < ordered.index('Old')
    assert ordered.index('Recent') < ordered.index('Unknown')


def test_pages_reproduce_stable_full_order_and_keep_empty_page_summary(supabase_client, markets):
    supabase_client.table('offers').insert([
        _offer(store, f'Item {i}') for store in markets for i in range(5)
    ]).execute()
    whole = _page(supabase_client, markets)
    pages = [_page(supabase_client, markets, page_limit=4, page_offset=i) for i in range(0, 15, 4)]
    assert [i for page in pages for i in _ids(page)] == _ids(whole)
    assert _ids(_page(supabase_client, markets)) == _ids(whole)
    empty = _page(supabase_client, markets, page_offset=99)
    assert empty[0]['id'] is None
    assert empty[0]['total'] == 15
    assert empty[0]['counts_by_supermarket_id'] == whole[0]['counts_by_supermarket_id']
    assert _ids(_page(supabase_client, markets, filter_query='  ')) == _ids(whole)


def test_filters_and_current_visibility_precede_balancing(supabase_client, markets):
    offers = supabase_client.table('offers').insert([
        _offer(markets[0], 'Visible', subcategory='Pasta'),
        _offer(markets[0], 'Other category', category='Bevande', subcategory='Acqua'),
        _offer(markets[0], 'Expired', valid_to=(TODAY - timedelta(days=1)).isoformat()),
        _offer(markets[0], 'Future', valid_from=(TODAY + timedelta(days=1)).isoformat()),
        _offer(markets[0], 'Draft', is_confirmed=False),
        _offer(markets[1], 'Outside candidate area'),
    ]).execute().data
    rows = _page(supabase_client, markets[:1], filter_category='Dispensa', filter_subcategory='Pasta')
    assert _ids(rows) == [offers[0]['id']]
    assert rows[0]['total'] == 1
    excluded = _page(supabase_client, markets[:1], filter_supermarket_ids=[markets[1]['id']])
    assert excluded[0]['total'] == 0
    assert not _ids(excluded)


def test_search_relevance_still_precedes_alphabetical_tie_breaker(supabase_client, markets):
    offers = supabase_client.table('offers').insert([
        _offer(markets[0], 'A latte'),
        _offer(markets[1], 'Z latte latte latte latte'),
    ]).execute().data
    rows = _page(supabase_client, markets, filter_query='latte')
    assert _ids(rows) == [offers[1]['id'], offers[0]['id']]


def test_global_fallback_uses_same_balanced_order(supabase_client, markets):
    supabase_client.table('offers').insert([
        _offer(store, f'Item {i}') for store in markets for i in range(3)
    ]).execute()
    scoped = _page(supabase_client, markets)
    global_rows = _page(supabase_client, markets, candidate_supermarket_ids=None, candidate_distances_km=None)
    assert _ids(global_rows) == _ids(scoped)
    assert global_rows[0]['total'] == scoped[0]['total']
    assert all(row['distance_km'] is None for row in global_rows)
