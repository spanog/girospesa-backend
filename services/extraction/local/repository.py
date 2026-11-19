"""Atomic flyer aggregate persistence; no production DB credentials leave the server."""
from contextlib import contextmanager, AbstractContextManager
from collections.abc import Callable, Iterator
from psycopg2.extensions import connection as Connection
from services.extraction.local.session import LocalSession
from psycopg2.extras import RealDictCursor, Json, execute_values
from psycopg2 import sql
from services.extraction.local.session import LocalExtractionConflict


class LocalFlyerNotFound(ValueError):
    """Requested source does not exist."""


class LocalExtractionRepository:
    def __init__(self, connect: Callable[[], Connection] | None = None,
                 connection_scope: Callable[[], AbstractContextManager] | None = None) -> None:
        self._connect = connect
        self._connection_scope = connection_scope or self._dedicated_connection

    @contextmanager
    def _dedicated_connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def locked(self, flyer_id: str) -> Iterator[tuple[RealDictCursor, dict]]:
        with self._connection_scope() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute('SELECT id, file_url, file_name, file_hash, file_type, flyer_kind, '
                               'status, extraction_metadata, supermarket_id, supermarket_name, '
                               'valid_from, valid_to FROM public.flyers WHERE id=%s FOR UPDATE', (flyer_id,))
                flyer = cursor.fetchone()
                if not flyer:
                    raise LocalFlyerNotFound('Flyer not found')
                if flyer.get('flyer_kind', 'source') != 'source' or flyer['file_type'] != 'pdf':
                    raise LocalExtractionConflict('Local extraction requires a source PDF')
                yield cursor, dict(flyer)

    def save(self, cursor: RealDictCursor, flyer: dict, session: LocalSession,
             status: str = 'processing', error: str | None = None) -> None:
        cursor.execute('UPDATE public.flyers SET extraction_metadata=%s, status=%s, '
            'error_message=%s, pages_count=%s, products_count=%s WHERE id=%s',
            (Json(session.metadata), status, error, session.metadata['pages_total'],
             session.metadata['partial_products_count'], flyer['id']))

    def insert_offers(self, cursor: RealDictCursor, rows: list[dict]) -> None:
        if not rows:
            return
        columns = list(dict.fromkeys(key for row in rows for key in row))
        values = [[Json(row.get(key)) if isinstance(row.get(key), (list, dict)) else row.get(key)
                   for key in columns] for row in rows]
        query = sql.SQL('INSERT INTO public.offers ({}) VALUES %s '
            'ON CONFLICT (flyer_id,offer_key,format_key) DO NOTHING').format(
            sql.SQL(',').join(map(sql.Identifier, columns)))
        execute_values(cursor, query, values, page_size=500)

    def draft_count(self, cursor: RealDictCursor, flyer_id: str) -> int:
        cursor.execute('SELECT count(*) AS count FROM public.offers WHERE flyer_id=%s '
                       'AND NOT is_confirmed', (flyer_id,))
        return cursor.fetchone()['count']

    def reject_published(self, cursor: RealDictCursor, flyer_id: str) -> None:
        cursor.execute('SELECT 1 FROM public.offers WHERE flyer_id=%s AND is_confirmed LIMIT 1', (flyer_id,))
        if cursor.fetchone():
            raise LocalExtractionConflict('Published flyers cannot be re-extracted')
