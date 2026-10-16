"""Atomic flyer aggregate persistence; no production DB credentials leave the server."""
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from psycopg2.extensions import connection as Connection
from services.extraction.local.session import LocalSession
from psycopg2.extras import RealDictCursor, Json
from psycopg2 import sql
from services.extraction.local.session import LocalExtractionConflict


class LocalFlyerNotFound(ValueError):
    """Requested source does not exist."""


class LocalExtractionRepository:
    def __init__(self, connect: Callable[[], Connection]) -> None:
        self._connect = connect

    @contextmanager
    def locked(self, flyer_id: str) -> Iterator[tuple[RealDictCursor, dict]]:
        connection = self._connect()
        try:
            with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute('SELECT * FROM public.flyers WHERE id=%s FOR UPDATE', (flyer_id,))
                flyer = cursor.fetchone()
                if not flyer:
                    raise LocalFlyerNotFound('Flyer not found')
                if flyer.get('flyer_kind', 'source') != 'source' or flyer['file_type'] != 'pdf':
                    raise LocalExtractionConflict('Local extraction requires a source PDF')
                yield cursor, dict(flyer)
        finally:
            connection.close()

    def save(self, cursor: RealDictCursor, flyer: dict, session: LocalSession,
             status: str = 'processing', error: str | None = None) -> None:
        cursor.execute('UPDATE public.flyers SET extraction_metadata=%s, status=%s, '
            'error_message=%s, pages_count=%s, products_count=%s WHERE id=%s',
            (Json(session.metadata), status, error, session.metadata['pages_total'],
             session.metadata['partial_products_count'], flyer['id']))

    def insert_offers(self, cursor: RealDictCursor, rows: list[dict]) -> None:
        for row in rows:
            columns = list(row)
            values = [Json(v) if isinstance(v, (list, dict)) else v for v in row.values()]
            query = sql.SQL('INSERT INTO public.offers ({}) VALUES ({}) '
                'ON CONFLICT (flyer_id,offer_key,format_key) DO NOTHING').format(
                sql.SQL(',').join(map(sql.Identifier, columns)),
                sql.SQL(',').join(sql.Placeholder() for _ in columns))
            cursor.execute(query, values)

    def draft_count(self, cursor: RealDictCursor, flyer_id: str) -> int:
        cursor.execute('SELECT count(*) AS count FROM public.offers WHERE flyer_id=%s '
                       'AND NOT is_confirmed', (flyer_id,))
        return cursor.fetchone()['count']

    def reject_published(self, cursor: RealDictCursor, flyer_id: str) -> None:
        cursor.execute('SELECT 1 FROM public.offers WHERE flyer_id=%s AND is_confirmed LIMIT 1', (flyer_id,))
        if cursor.fetchone():
            raise LocalExtractionConflict('Published flyers cannot be re-extracted')
