"""pg_trgm в схеме core: Django search_path без public (как vector)."""

from __future__ import annotations

from src.core.utils.database.module_schema import CORE_SCHEMA


def ensure_pg_trgm_extension(*, connection=None) -> None:
    """CREATE EXTENSION pg_trgm SCHEMA core или перенос из другой схемы."""
    from django.db import connection as default_connection
    from django.db.utils import OperationalError, ProgrammingError

    conn = connection or default_connection
    if getattr(conn, 'vendor', '') != 'postgresql':
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {CORE_SCHEMA}')
            cursor.execute(
                'SELECT n.nspname FROM pg_extension e '
                'JOIN pg_namespace n ON n.oid = e.extnamespace '
                'WHERE e.extname = %s LIMIT 1',
                ['pg_trgm'],
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(f'CREATE EXTENSION pg_trgm SCHEMA {CORE_SCHEMA}')
            elif row[0] != CORE_SCHEMA:
                cursor.execute(f'ALTER EXTENSION pg_trgm SET SCHEMA {CORE_SCHEMA}')
    except (OperationalError, ProgrammingError) as exc:
        raise RuntimeError(
            'Расширение pg_trgm недоступно. '
            'Для portable: ergoms install-postgres (contrib). '
            'Для внешней БД: CREATE EXTENSION pg_trgm SCHEMA core '
            'от суперпользователя.'
        ) from exc


def ensure_pg_trgm_forward(apps, schema_editor) -> None:
    ensure_pg_trgm_extension(connection=schema_editor.connection)


def ensure_pg_trgm_backward(apps, schema_editor) -> None:
    conn = schema_editor.connection
    if getattr(conn, 'vendor', '') != 'postgresql':
        return
    with conn.cursor() as cursor:
        cursor.execute('DROP EXTENSION IF EXISTS pg_trgm')
