# -*- coding: utf-8 -*-
"""Идемпотентное создание ACL MenuSeparator (колонка + M2M-таблицы)."""

from django.apps import apps as global_apps


def _table_columns(schema_editor, table_name):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table_name)
    return {col.name for col in description}


def ensure_menuseparator_access_acl(apps, schema_editor):
    """
    Добавляет is_admin_only и through-таблицы allowed_roles / allowed_role_groups,
    если их ещё нет. Живые модели: в database_operations 0056 historical apps
    ещё без этих полей (AddField только в state_operations).
    """
    MenuSeparator = global_apps.get_model('cms_adp', 'MenuSeparator')
    connection = schema_editor.connection
    existing_tables = set(connection.introspection.table_names())
    table = MenuSeparator._meta.db_table
    if table not in existing_tables:
        return

    columns = _table_columns(schema_editor, table)
    if 'is_admin_only' not in columns:
        schema_editor.add_field(
            MenuSeparator,
            MenuSeparator._meta.get_field('is_admin_only'),
        )

    existing_tables = set(connection.introspection.table_names())
    for field_name in ('allowed_roles', 'allowed_role_groups'):
        field = MenuSeparator._meta.get_field(field_name)
        through = field.remote_field.through
        through_table = through._meta.db_table
        if through_table not in existing_tables:
            schema_editor.create_model(through)
            existing_tables.add(through_table)
