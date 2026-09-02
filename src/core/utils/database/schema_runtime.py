"""Хуки PostgreSQL-схем: CREATE SCHEMA и search_path без public."""

from __future__ import annotations

import logging

from django.db.backends.signals import connection_created
from django.db.models.signals import pre_migrate

logger = logging.getLogger('utils.database.schema')

_HOOKS_INSTALLED = False
_MAINTENANCE_DBS = frozenset({'postgres', 'template0', 'template1'})


def _is_maintenance_connection(connection) -> bool:
    """Служебные БД Django (_nodb_cursor): схемы приложения там не создаём."""
    if getattr(connection, 'alias', '') == '__no_db__':
        return True
    name = (getattr(connection, 'settings_dict', None) or {}).get('NAME')
    if name in (None, ''):
        return True
    return str(name) in _MAINTENANCE_DBS


def install_schema_hooks() -> None:
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    connection_created.connect(_on_connection_created)
    pre_migrate.connect(_on_pre_migrate)
    _patch_migration_apply()
    _HOOKS_INSTALLED = True


def _safe_ident(name: str) -> bool:
    return bool(name) and all(ch.isalnum() or ch == '_' for ch in name)


def ensure_pg_schemas(connection) -> None:
    from src.core.utils.database.module_schema import CORE_SCHEMA, installed_module_schema_names

    if getattr(connection, 'vendor', '') != 'postgresql':
        return
    if _is_maintenance_connection(connection):
        return
    if getattr(connection, '_ergo_schema_ready', False):
        return
    names = [CORE_SCHEMA, *installed_module_schema_names()]
    with connection.cursor() as cursor:
        for name in names:
            if not _safe_ident(name):
                continue
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {name}')
    connection._ergo_schema_ready = True


def apply_process_search_path(connection) -> None:
    from src.core.utils.database.module_schema import search_path_for_process

    if getattr(connection, 'vendor', '') != 'postgresql':
        return
    if _is_maintenance_connection(connection):
        return
    path = search_path_for_process()
    if not path:
        return
    with connection.cursor() as cursor:
        cursor.execute(f'SET search_path TO {path}')


def apply_app_search_path(connection, app_label: str) -> None:
    """Первая схема — приложения (CREATE TABLE), затем остальные из процесса."""
    from src.core.utils.database.module_schema import schema_for_app_label, search_path_for_app

    if getattr(connection, 'vendor', '') != 'postgresql':
        return
    own = schema_for_app_label(app_label)
    if _safe_ident(own):
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {own}')
    path = search_path_for_app(app_label)
    if not path:
        return
    with connection.cursor() as cursor:
        cursor.execute(f'SET search_path TO {path}')
    logger.debug('migration search_path app=%s path=%s', app_label, path)


def _on_connection_created(sender, connection, **_kwargs) -> None:
    if getattr(connection, 'vendor', '') != 'postgresql':
        return
    ensure_pg_schemas(connection)
    apply_process_search_path(connection)


def _on_pre_migrate(sender, app_config, using, **_kwargs) -> None:
    from django.db import connections

    connection = connections[using]
    if getattr(connection, 'vendor', '') != 'postgresql':
        return
    # Только создать схемы. search_path приложения — в Migration.apply:
    # Django шлёт pre_migrate сразу для всех app, до любой миграции.
    connection._ergo_schema_ready = False
    ensure_pg_schemas(connection)


def _patch_migration_apply() -> None:
    from django.db.migrations.migration import Migration

    if getattr(Migration.apply, '_ergo_schema_patched', False):
        return

    orig_apply = Migration.apply
    orig_unapply = Migration.unapply

    def apply(self, project_state, schema_editor, collect_sql=False):
        connection = getattr(schema_editor, 'connection', None)
        if connection is not None:
            apply_app_search_path(connection, self.app_label)
        return orig_apply(self, project_state, schema_editor, collect_sql)

    def unapply(self, project_state, schema_editor, collect_sql=False):
        connection = getattr(schema_editor, 'connection', None)
        if connection is not None:
            apply_app_search_path(connection, self.app_label)
        return orig_unapply(self, project_state, schema_editor, collect_sql)

    apply._ergo_schema_patched = True
    Migration.apply = apply
    Migration.unapply = unapply
