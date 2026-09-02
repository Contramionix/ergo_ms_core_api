from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from src.core.utils.database.module_schema import CORE_SCHEMA


class Command(BaseCommand):
    help = (
        'Перенести оставшиеся объекты из public в схему core и убрать public '
        '(PostgreSQL). На SQLite — no-op.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--keep-public',
            action='store_true',
            help='Не удалять схему public (только перенос и REVOKE CREATE)',
        )

    def handle(self, *args, **options):
        database = options['database']
        dry = bool(options['dry_run'])
        keep_public = bool(options['keep_public'])
        if database not in connections:
            raise CommandError(f'unknown database alias {database}')

        connection = connections[database]
        if connection.vendor != 'postgresql':
            self.stdout.write('skip: not PostgreSQL')
            return

        from django.db.utils import DatabaseError, ProgrammingError

        from src.core.utils.database.schema_move import (
            copy_rows_if_dest_empty,
            drop_schema_if_exists,
            list_public_extensions,
            list_schema_relations,
            merge_django_migrations,
            move_extension_to_schema,
            public_user_object_count,
            relation_exists,
            revoke_create_on_public,
            schema_exists,
            set_relation_schema,
            table_row_count,
        )
        from src.core.utils.database.schema_runtime import ensure_pg_schemas

        ensure_pg_schemas(connection)
        quoted_core = connection.ops.quote_name(CORE_SCHEMA)
        moved = 0

        if not schema_exists(connection, 'public'):
            self.stdout.write('public already absent')
            self.stdout.write(self.style.SUCCESS(f'moved {moved} relation(s)'))
            return

        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {quoted_core}')

        for extname in list_public_extensions(connection):
            self.stdout.write(f'extension {extname} -> {CORE_SCHEMA}')
            moved += 1
            if not dry:
                try:
                    move_extension_to_schema(connection, extname, CORE_SCHEMA)
                except DatabaseError as exc:
                    self.stderr.write(f'extension {extname}: {exc}')
                    continue

        relations = list_schema_relations(
            connection, 'public', ('r', 'v', 'm', 'p', 'S'),
        )
        # auth_user раньше дочерних таблиц: иначе INSERT упрётся в пустой FK.
        relations.sort(key=lambda item: (item[0] != 'auth_user', item[0]))
        skipped_existing = 0
        for relname, relkind in relations:
            if (
                relname == 'django_migrations'
                and relation_exists(connection, CORE_SCHEMA, relname)
            ):
                if dry:
                    self.stdout.write(
                        f'would merge django_migrations public -> {CORE_SCHEMA}'
                    )
                    continue
                merged = merge_django_migrations(connection, 'public', CORE_SCHEMA)
                if merged:
                    self.stdout.write(
                        f'merged {merged} django_migrations row(s) '
                        f'public -> {CORE_SCHEMA}'
                    )
                with connection.cursor() as cursor:
                    cursor.execute('DROP TABLE IF EXISTS public.django_migrations')
                continue
            if relation_exists(connection, CORE_SCHEMA, relname):
                if relkind == 'r':
                    source_n = table_row_count(connection, 'public', relname)
                    dest_n = table_row_count(connection, CORE_SCHEMA, relname)
                    if dest_n == 0 and source_n > 0:
                        if dry:
                            self.stdout.write(
                                f'would copy {source_n} row(s) into empty '
                                f'{CORE_SCHEMA}.{relname}'
                            )
                            continue
                        copied = copy_rows_if_dest_empty(
                            connection, 'public', CORE_SCHEMA, relname,
                        )
                        if copied:
                            self.stdout.write(
                                f'copied {copied} row(s) into empty '
                                f'{CORE_SCHEMA}.{relname}'
                            )
                            moved += 1
                            continue
                skipped_existing += 1
                continue
            self.stdout.write(f'{relname} -> {CORE_SCHEMA}')
            moved += 1
            if dry:
                continue
            try:
                with transaction.atomic(using=database):
                    set_relation_schema(
                        connection, relname, relkind, 'public', CORE_SCHEMA,
                    )
            except DatabaseError as exc:
                self.stderr.write(f'{relname}: {exc}')
                continue
        if skipped_existing:
            self.stdout.write(
                f'skipped {skipped_existing} relation(s) already in {CORE_SCHEMA}'
            )

        if dry:
            self.stdout.write(self.style.SUCCESS(f'moved {moved} relation(s)'))
            return

        leftover = public_user_object_count(connection)
        if leftover:
            self.stdout.write(
                self.style.WARNING(f'public still has {leftover} object(s)')
            )
            revoke_create_on_public(connection)
        elif keep_public:
            revoke_create_on_public(connection)
            self.stdout.write('public kept, CREATE revoked')
        else:
            try:
                drop_schema_if_exists(connection, 'public')
                self.stdout.write('dropped schema public')
            except ProgrammingError as exc:
                self.stderr.write(f'drop public: {exc}')
                revoke_create_on_public(connection)
                self.stdout.write('public kept, CREATE revoked')

        self.stdout.write(self.style.SUCCESS(f'moved {moved} relation(s)'))
