from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from src.core.utils.database.module_schema import (
    django_app_labels_for_module,
    module_schema_name,
)


class Command(BaseCommand):
    help = 'Применить миграции Django-приложений модуля (опционально на alias БД).'

    def add_arguments(self, parser):
        parser.add_argument('--module', default='', help='Имя папки modules/<name>')
        parser.add_argument('--database', default='default', help='Alias из DATABASES')
        parser.add_argument(
            '--app-label',
            default='',
            help='Только этот app_label (по умолчанию все приложения модуля)',
        )
        parser.add_argument(
            '--grant-role',
            default='',
            help='Роль PostgreSQL: USAGE на core, ALL на схему модуля',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            dest='migrate_all',
            help='Все модули с api/schema.yaml',
        )

    def handle(self, *args, **options):
        from django.core.management import call_command
        from src.core.utils.database.module_schema import discovered_module_schemas

        database = (options['database'] or 'default').strip()
        if database not in connections:
            raise CommandError(f'unknown database alias {database}')
        explicit_label = (options['app_label'] or '').strip()
        grant_role = ''.join(
            ch for ch in (options.get('grant_role') or '') if ch.isalnum() or ch == '_'
        )
        names = self._module_names(options, discovered_module_schemas)
        for name in names:
            labels = [explicit_label] if explicit_label else list(
                django_app_labels_for_module(name)
            )
            if not labels:
                labels = [name]
            self._ensure_schema(name, database, grant_role)
            for app_label in labels:
                schema = module_schema_name(name)
                self.stdout.write(f'migrate {app_label} database={database} schema={schema}')
                call_command(
                    'migrate',
                    app_label,
                    database=database,
                    verbosity=options.get('verbosity', 1),
                )

    def _module_names(self, options, discovered_module_schemas) -> list[str]:
        name = (options['module'] or '').strip()
        migrate_all = bool(options.get('migrate_all'))
        if migrate_all and name:
            raise CommandError('укажите либо --module, либо --all')
        if migrate_all:
            names = [item[0] for item in discovered_module_schemas()]
            if not names:
                raise CommandError('нет модулей с api/schema.yaml')
            return names
        if not name:
            raise CommandError('нужен --module=<name> или --all')
        return [name]

    def _ensure_schema(self, name: str, database: str, grant_role: str) -> None:
        schema = module_schema_name(name)
        connection = connections[database]
        if connection.vendor != 'postgresql':
            return
        quoted_schema = connection.ops.quote_name(schema)
        quoted_core = connection.ops.quote_name('core')
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {quoted_core}')
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {quoted_schema}')
            cursor.execute(f'SET search_path TO {quoted_schema}, {quoted_core}')
            if grant_role:
                quoted_role = connection.ops.quote_name(grant_role)
                cursor.execute(f'GRANT USAGE ON SCHEMA {quoted_core} TO {quoted_role}')
                cursor.execute(
                    f'GRANT USAGE, CREATE ON SCHEMA {quoted_schema} TO {quoted_role}'
                )
                cursor.execute(
                    f'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA {quoted_schema} '
                    f'TO {quoted_role}'
                )
                cursor.execute(
                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} '
                    f'GRANT ALL ON TABLES TO {quoted_role}'
                )
