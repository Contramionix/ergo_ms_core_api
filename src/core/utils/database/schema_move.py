"""Перенос отношений PostgreSQL между схемами (без public в целевой архитектуре)."""

from __future__ import annotations

from typing import Iterable

_RELKIND_SQL = {
    'v': 'ALTER VIEW {src}.{name} SET SCHEMA {dest}',
    'm': 'ALTER MATERIALIZED VIEW {src}.{name} SET SCHEMA {dest}',
    'S': 'ALTER SEQUENCE {src}.{name} SET SCHEMA {dest}',
}

# SERIAL — deptype 'a'; IDENTITY (Django 5+) — 'i'. PostgreSQL не даёт
# отдельно переносить такую последовательность: она уходит вместе с таблицей.
_OWNED_SEQUENCE_PREDICATE = """
    c.relkind = 'S' AND EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')
    )
"""


def quote_ident(connection, name: str) -> str:
    return connection.ops.quote_name(name)


def list_schema_relations(connection, schema: str, relkinds: Iterable[str]) -> list[tuple[str, str]]:
    kinds = tuple(relkinds)
    if not kinds:
        return []
    placeholders = ', '.join(['%s'] * len(kinds))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT c.relname, c.relkind::text
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relkind IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_depend d
                  JOIN pg_extension e ON d.refobjid = e.oid
                  WHERE d.objid = c.oid AND d.deptype = 'e'
              )
              AND NOT ({_OWNED_SEQUENCE_PREDICATE})
            """,
            [schema, *kinds],
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]


def list_public_extensions(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.extname
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE n.nspname = 'public'
            ORDER BY 1
            """
        )
        return [row[0] for row in cursor.fetchall()]


def schema_exists(connection, schema: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT 1 FROM pg_namespace WHERE nspname = %s',
            [schema],
        )
        return cursor.fetchone() is not None


def merge_django_migrations(connection, source: str, dest: str) -> int:
    """Дописать в dest.django_migrations строки из source, которых ещё нет."""
    if not relation_exists(connection, source, 'django_migrations'):
        return 0
    if not relation_exists(connection, dest, 'django_migrations'):
        return 0
    q_src = quote_ident(connection, source)
    q_dest = quote_ident(connection, dest)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {q_dest}.django_migrations (app, name, applied)
            SELECT p.app, p.name, p.applied
            FROM {q_src}.django_migrations AS p
            WHERE NOT EXISTS (
                SELECT 1 FROM {q_dest}.django_migrations AS c
                WHERE c.app = p.app AND c.name = p.name
            )
            """
        )
        return int(cursor.rowcount or 0)


def table_row_count(connection, schema: str, relname: str) -> int:
    if not relation_exists(connection, schema, relname):
        return 0
    q_schema = quote_ident(connection, schema)
    q_name = quote_ident(connection, relname)
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM {q_schema}.{q_name}')
        return int(cursor.fetchone()[0])


def _column_names(connection, schema: str, relname: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            [schema, relname],
        )
        return [row[0] for row in cursor.fetchall()]


def copy_rows_if_dest_empty(connection, source: str, dest: str, relname: str) -> int:
    """Если dest-таблица пустая — скопировать строки из source (общие колонки)."""
    if table_row_count(connection, dest, relname) > 0:
        return 0
    source_count = table_row_count(connection, source, relname)
    if source_count == 0:
        return 0
    source_cols = _column_names(connection, source, relname)
    dest_cols = set(_column_names(connection, dest, relname))
    cols = [name for name in source_cols if name in dest_cols]
    if not cols:
        return 0
    q_src = quote_ident(connection, source)
    q_dest = quote_ident(connection, dest)
    q_rel = quote_ident(connection, relname)
    q_cols = ', '.join(quote_ident(connection, name) for name in cols)
    with connection.cursor() as cursor:
        # Пустые таблицы в dest часто ссылаются друг на друга: без replica
        # порядок INSERT упирается в FK.
        cursor.execute("SET session_replication_role = 'replica'")
        try:
            cursor.execute(
                f'INSERT INTO {q_dest}.{q_rel} ({q_cols}) '
                f'SELECT {q_cols} FROM {q_src}.{q_rel}'
            )
            inserted = int(cursor.rowcount or 0)
        finally:
            cursor.execute("SET session_replication_role = 'origin'")
        if 'id' in cols:
            cursor.execute(
                'SELECT pg_get_serial_sequence(%s, %s)',
                [f'{dest}.{relname}', 'id'],
            )
            seq_row = cursor.fetchone()
            seq_name = seq_row[0] if seq_row else None
            if seq_name:
                cursor.execute(
                    f'SELECT setval(%s, COALESCE((SELECT MAX(id) FROM {q_dest}.{q_rel}), 1), true)',
                    [seq_name],
                )
        return inserted


def relation_exists(connection, schema: str, relname: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
            """,
            [schema, relname],
        )
        return cursor.fetchone() is not None


def set_relation_schema(connection, relname: str, relkind: str, source: str, dest: str) -> None:
    q_src = quote_ident(connection, source)
    q_dest = quote_ident(connection, dest)
    q_name = quote_ident(connection, relname)
    template = _RELKIND_SQL.get(relkind, 'ALTER TABLE {src}.{name} SET SCHEMA {dest}')
    with connection.cursor() as cursor:
        cursor.execute(template.format(src=q_src, name=q_name, dest=q_dest))


def move_extension_to_schema(connection, extname: str, dest: str) -> None:
    if not extname or not all(ch.isalnum() or ch == '_' for ch in extname):
        return
    q_dest = quote_ident(connection, dest)
    with connection.cursor() as cursor:
        cursor.execute(f'ALTER EXTENSION {extname} SET SCHEMA {q_dest}')


_FK_ACTION_SQL = {
    'a': '',
    'r': ' RESTRICT',
    'c': ' CASCADE',
    'n': ' SET NULL',
    'd': ' SET DEFAULT',
}


def _list_fks_to_public_auth_user(connection) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              n.nspname,
              c.relname,
              con.conname,
              con.condeferrable,
              con.condeferred,
              con.convalidated,
              con.confupdtype,
              con.confdeltype,
              ARRAY(
                SELECT a.attname
                FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a
                  ON a.attrelid = con.conrelid AND a.attnum = k.attnum
                ORDER BY k.ord
              ),
              ARRAY(
                SELECT a.attname
                FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a
                  ON a.attrelid = con.confrelid AND a.attnum = k.attnum
                ORDER BY k.ord
              )
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_class cf ON cf.oid = con.confrelid
            JOIN pg_namespace nf ON nf.oid = cf.relnamespace
            WHERE con.contype = 'f'
              AND nf.nspname = 'public'
              AND cf.relname = 'auth_user'
            ORDER BY n.nspname, c.relname, con.conname
            """
        )
        return list(cursor.fetchall())


def retarget_public_auth_user_foreign_keys(connection) -> list[str]:
    """Перенацелить FK public.auth_user на core.auth_user после выноса схемы.

    SET SCHEMA не меняет уже существующий REFERENCES public.auth_user(...).
    token_blacklist и другие таблицы третьих приложений тогда отвергают
    пользователя, которого нет в устаревшей копии public.auth_user.
    """
    if connection.vendor != 'postgresql':
        return []
    if not relation_exists(connection, 'core', 'auth_user'):
        return []
    if not relation_exists(connection, 'public', 'auth_user'):
        return []

    from src.core.utils.database.module_schema import CORE_SCHEMA

    q_core = quote_ident(connection, CORE_SCHEMA)
    q_user = quote_ident(connection, 'auth_user')
    retargeted: list[str] = []
    with connection.cursor() as cursor:
        for row in _list_fks_to_public_auth_user(connection):
            (
                child_schema,
                child_table,
                conname,
                deferrable,
                deferred,
                validated,
                upd_type,
                del_type,
                cols,
                ref_cols,
            ) = row
            if not cols or not ref_cols:
                continue
            q_schema = quote_ident(connection, child_schema)
            q_table = quote_ident(connection, child_table)
            q_con = quote_ident(connection, conname)
            q_cols = ', '.join(quote_ident(connection, name) for name in cols)
            q_refs = ', '.join(quote_ident(connection, name) for name in ref_cols)
            defer_sql = ''
            if deferrable:
                defer_sql = (
                    ' DEFERRABLE INITIALLY DEFERRED'
                    if deferred
                    else ' DEFERRABLE INITIALLY IMMEDIATE'
                )
            upd_sql = _FK_ACTION_SQL.get(upd_type, '')
            del_sql = _FK_ACTION_SQL.get(del_type, '')
            if upd_sql:
                upd_sql = f' ON UPDATE{upd_sql}'
            if del_sql:
                del_sql = f' ON DELETE{del_sql}'
            valid_sql = '' if validated else ' NOT VALID'
            cursor.execute(
                f'ALTER TABLE {q_schema}.{q_table} DROP CONSTRAINT {q_con}'
            )
            cursor.execute(
                f'ALTER TABLE {q_schema}.{q_table} '
                f'ADD CONSTRAINT {q_con} '
                f'FOREIGN KEY ({q_cols}) REFERENCES {q_core}.{q_user} ({q_refs})'
                f'{upd_sql}{del_sql}{defer_sql}{valid_sql}'
            )
            retargeted.append(f'{child_schema}.{child_table}.{conname}')
    return retargeted


def revoke_create_on_public(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        cursor.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')


def drop_schema_if_exists(connection, schema: str) -> None:
    if not schema or not all(ch.isalnum() or ch == '_' for ch in schema):
        return
    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')


def public_user_object_count(connection) -> int:
    if not schema_exists(connection, 'public'):
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'v', 'm', 'p', 'S', 'f')
            """
        )
        return int(cursor.fetchone()[0])
