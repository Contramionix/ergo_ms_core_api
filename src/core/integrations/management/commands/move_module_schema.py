from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from src.core.utils.database.module_schema import CORE_SCHEMA


class Command(BaseCommand):
    help = (
        'Перенести таблицы Django-приложений модуля из public или core '
        'в схему модуля (PostgreSQL). На SQLite команда ничего не меняет.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--module',
            default='',
            help='Имя папки modules/<name>. Пусто вместе с --all.',
        )
        parser.add_argument('--app-label', default='', help='Только этот app_label')
        parser.add_argument('--database', default='default')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--all',
            action='store_true',
            dest='move_all',
            help='Все модули с api/schema.yaml',
        )

    def handle(self, *args, **options):
        database = options['database']
        dry = bool(options['dry_run'])
        if database not in connections:
            raise CommandError(f'unknown database alias {database}')

        names = self._module_names(options)
        moved_total = 0
        for name in names:
            moved_total += self._move_module(
                name=name,
                app_label=(options['app_label'] or '').strip(),
                database=database,
                dry=dry,
            )
        if options.get('move_all'):
            from django.core.management import call_command

            call_command(
                'move_core_schema',
                database=database,
                dry_run=dry,
                verbosity=options.get('verbosity', 1),
            )
        self.stdout.write(self.style.SUCCESS(f'moved {moved_total} module relation(s)'))

    def _module_names(self, options) -> list[str]:
        from src.core.utils.database.module_schema import discovered_module_schemas

        name = (options['module'] or '').strip()
        move_all = bool(options.get('move_all'))
        if move_all and name:
            raise CommandError('укажите либо --module, либо --all')
        if move_all:
            names = [item[0] for item in discovered_module_schemas()]
            if not names:
                raise CommandError('нет модулей с api/schema.yaml')
            return names
        if not name:
            raise CommandError('нужен --module=<name> или --all')
        return [name]

    def _owned_tables(self, labels: list[str]) -> set[str]:
        from src.core.utils.database.module_schema import model_tables_for_app_label

        owned: set[str] = set()
        for label in labels:
            owned.update(model_tables_for_app_label(label))
        return owned

    def _move_module(self, *, name: str, app_label: str, database: str, dry: bool) -> int:
        from src.core.utils.database.module_schema import (
            django_app_labels_for_module,
            module_schema_name,
            owner_app_label,
        )
        from src.core.utils.database.schema_move import (
            schema_exists,
            set_relation_schema,
        )

        connection = connections[database]
        if connection.vendor != 'postgresql':
            self.stdout.write(f'{name}: skip (not PostgreSQL)')
            return 0

        sources = [
            schema
            for schema in ('public', CORE_SCHEMA)
            if schema_exists(connection, schema)
        ]
        if not sources:
            return 0

        dest = module_schema_name(name)
        labels = list(django_app_labels_for_module(name))
        if app_label:
            labels = [app_label]
        elif not labels:
            labels = [name]
        owned = self._owned_tables(labels)

        planned: dict[str, str] = {}
        for relname, relkind in self._collect_relations(connection, labels, sources):
            planned[relname] = relkind
        for table in owned:
            planned.setdefault(table, 'r')

        moved = 0
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {connection.ops.quote_name(dest)}')
            for relname, relkind in sorted(planned.items()):
                if not owner_app_label(relname, labels) and relname not in owned:
                    continue
                cursor.execute(
                    'SELECT n.nspname FROM pg_class c '
                    'JOIN pg_namespace n ON n.oid = c.relnamespace '
                    'WHERE c.relname = %s AND n.nspname = ANY(%s)',
                    [relname, [dest, *sources]],
                )
                rows = {row[0] for row in cursor.fetchall()}
                if dest in rows:
                    continue
                if relkind == 'S':
                    # Принадлежащие колонке последовательности уходят вместе с таблицей.
                    continue
                source = 'public' if 'public' in rows else (
                    CORE_SCHEMA if CORE_SCHEMA in rows else None
                )
                if source is None:
                    continue
                self.stdout.write(f'{source}.{relname} -> {dest}')
                moved += 1
                if dry:
                    continue
                with transaction.atomic(using=database):
                    set_relation_schema(connection, relname, relkind, source, dest)
        return moved

    def _collect_relations(
        self,
        connection,
        labels: list[str],
        sources: list[str],
    ) -> list[tuple[str, str]]:
        from src.core.utils.database.module_schema import owner_app_label
        from src.core.utils.database.schema_move import _OWNED_SEQUENCE_PREDICATE

        if not labels or not sources:
            return []
        placeholders = ', '.join(['%s'] * len(sources))
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT c.relname, c.relkind::text
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname IN ({placeholders})
                  AND c.relkind IN ('r', 'v', 'm', 'p', 'S')
                  AND NOT ({_OWNED_SEQUENCE_PREDICATE})
                """,
                sources,
            )
            rows = cursor.fetchall()
        return [
            (relname, relkind)
            for relname, relkind in rows
            if owner_app_label(relname, labels)
        ]
