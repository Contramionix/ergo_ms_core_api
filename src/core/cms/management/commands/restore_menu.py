# -*- coding: utf-8 -*-
"""
Management command: восстановление меню из ядра и живых модулей.

Пересоздаёт каталог (MenuItem/MenuSeparator), сохраняя:
- layout (order/parent/is_active, якоря разделителей) в MenuLayoutPlacement /
  MenuSeparatorLayout;
- настройки доступа пунктов и разделителей (allowed_roles / allowed_role_groups);
- админские пункты с catalog_key ``admin::*``.

Модули: сначала каталоги с процессов (группа моста menu.catalog), затем
populate с диска ядра для тех, кого соседи не отдали.
"""

import importlib

from django.core.management.base import BaseCommand, CommandError

from src.core.cms.adp.menu.catalog_apply import apply_module_menu_catalog
from src.core.cms.adp.menu.catalog_export import collect_menu_catalogs
from src.core.cms.adp.menu.menu_discovery import (
    MigrationApps,
    discover_core_menu_migrations,
    discover_module_menu_migrations,
)
from src.core.cms.adp.menu.models import MenuItem, MenuSeparator


def _menu_access_legacy_key(item) -> tuple:
    """Устаревший ключ доступа (до catalog_key) — запасной вариант."""
    parent_ref = ''
    if item.parent_id:
        parent = item.parent
        parent_ref = parent.route_name or parent.name or ''
    return (
        item.route_name or '',
        item.module_source or '',
        item.name or '',
        parent_ref,
    )


def _access_payload(obj):
    role_ids = list(obj.allowed_roles.values_list('id', flat=True))
    group_ids = list(obj.allowed_role_groups.values_list('id', flat=True))
    if not role_ids and not group_ids:
        return None
    return {'roles': role_ids, 'groups': group_ids}


def _apply_access_payload(obj, mapping):
    if mapping.get('roles'):
        obj.allowed_roles.set(mapping['roles'])
    if mapping.get('groups'):
        obj.allowed_role_groups.set(mapping['groups'])


def _save_access_map():
    """Сохраняет доступ: catalog_key → {roles, groups}; плюс legacy-ключ для пунктов."""
    access_map = {}
    for item in MenuItem.objects.select_related('parent').prefetch_related(
        'allowed_roles', 'allowed_role_groups',
    ).all():
        payload = _access_payload(item)
        if payload is None:
            continue
        if getattr(item, 'catalog_key', None):
            access_map[('catalog', item.catalog_key)] = payload
        access_map[('legacy', _menu_access_legacy_key(item))] = payload

    for separator in MenuSeparator.objects.prefetch_related(
        'allowed_roles', 'allowed_role_groups',
    ).all():
        payload = _access_payload(separator)
        if payload is None:
            continue
        if getattr(separator, 'catalog_key', None):
            access_map[('catalog', separator.catalog_key)] = payload
        access_map[('separator', separator.public_id)] = payload
    return access_map


def _restore_access_map(access_map):
    """Восстанавливает allowed_roles и allowed_role_groups из access_map."""
    for item in MenuItem.objects.select_related('parent').all():
        mapping = None
        if getattr(item, 'catalog_key', None):
            mapping = access_map.get(('catalog', item.catalog_key))
        if mapping is None:
            mapping = access_map.get(('legacy', _menu_access_legacy_key(item)))
        if mapping is None:
            continue
        _apply_access_payload(item, mapping)

    for separator in MenuSeparator.objects.all():
        mapping = None
        if getattr(separator, 'catalog_key', None):
            mapping = access_map.get(('catalog', separator.catalog_key))
        if mapping is None:
            mapping = access_map.get(('separator', separator.public_id))
        if mapping is None:
            continue
        _apply_access_payload(separator, mapping)


def _reapply_module_sidebar_role_groups(module_names):
    """
    Повторно применяет allowed_role_groups из menu_sidebar модулей после restore.

    populate вызывает apply до _restore_access_map; сохранённые группы могут
    перезаписать сброс на папках — модульная apply-функция выравнивает состояние.
    """
    for module_name in module_names:
        try:
            sidebar_mod = importlib.import_module(
                f'modules.{module_name}.api.menu_sidebar'
            )
        except (ImportError, ModuleNotFoundError):
            continue
        apply_fn = getattr(sidebar_mod, 'apply_sidebar_allowed_role_groups', None)
        if callable(apply_fn):
            apply_fn()


class Command(BaseCommand):
    help = (
        'Восстанавливает меню: ядро из своих миграций, модули с живых процессов '
        'и с диска ядра, если папка модуля здесь есть'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--core-only',
            action='store_true',
            help='Восстановить только core-меню (без модулей)',
        )
        parser.add_argument(
            '--module',
            type=str,
            metavar='NAME',
            help='Восстановить только указанный модуль (core + этот модуль)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет сделано без записи в БД',
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.core_only = options['core_only']
        self.module_filter = options.get('module')

        apps = MigrationApps()
        schema_editor = None

        core_migrations = discover_core_menu_migrations()

        from src.core.cms.adp.menu.layout_service import (
            cleanup_orphan_layouts,
            delete_seed_catalog,
            materialize_all_layouts,
        )
        from src.core.cms.adp.menu.models import MenuLayoutPlacement, MenuSeparatorLayout

        remote_catalogs = {}
        if not self.core_only:
            remote_catalogs = collect_menu_catalogs(module_filter=self.module_filter)

        if self.dry_run:
            access_map = _save_access_map()
            seed_items = MenuItem.objects.exclude(catalog_key__startswith='admin::').count()
            seed_seps = MenuSeparator.objects.exclude(catalog_key__startswith='admin::').count()
            admin_items = MenuItem.objects.filter(catalog_key__startswith='admin::').count()
            layout_n = MenuLayoutPlacement.objects.count()
            sep_layout_n = MenuSeparatorLayout.objects.count()
            self.stdout.write(self.style.WARNING(f'[dry-run] Сохранено настроек доступа: {len(access_map)}'))
            self.stdout.write(self.style.WARNING(
                f'[dry-run] Будет пересоздан seed-каталог: {seed_items} MenuItem, {seed_seps} MenuSeparator'
            ))
            self.stdout.write(self.style.WARNING(
                f'[dry-run] Сохранятся: {admin_items} admin::* пунктов, '
                f'{layout_n} layout placements, {sep_layout_n} separator layouts'
            ))
            for stem, func_name, _ in core_migrations:
                self.stdout.write(self.style.WARNING(f'[dry-run] core: {func_name} ({stem})'))
            for name, catalog in remote_catalogs.items():
                n_items = len(catalog.get('items') or [])
                self.stdout.write(self.style.WARNING(
                    f'[dry-run] Процесс {name}: {n_items} пунктов (menu.catalog)'
                ))
            discovered = discover_module_menu_migrations()
            if self.module_filter:
                discovered = [x for x in discovered if x[0] == self.module_filter]
            for name, stem, _ in discovered:
                if name in remote_catalogs:
                    continue
                self.stdout.write(self.style.WARNING(f'[dry-run] Диск {name}: {stem}'))
            self.stdout.write(self.style.WARNING('[dry-run] БД не изменяется'))
            return

        access_map = _save_access_map()
        self.stdout.write(f'Сохранено настроек доступа: {len(access_map)}')

        deleted_items, deleted_seps = delete_seed_catalog(keep_admin=True)
        self.stdout.write(
            f'Очищен seed-каталог ({deleted_items} MenuItem, {deleted_seps} MenuSeparator); '
            f'layout и admin::* сохранены'
        )

        for stem, func_name, func in core_migrations:
            func(apps, schema_editor)
            self.stdout.write(f'  core: {func_name} ({stem})')

        populated_modules = []
        if not self.core_only:
            for module_name, catalog in remote_catalogs.items():
                item_n, sep_n = apply_module_menu_catalog(catalog)
                populated_modules.append(module_name)
                self.stdout.write(self.style.SUCCESS(
                    f'  {module_name}: menu.catalog ({item_n} пунктов, {sep_n} разделителей)'
                ))

            discovered = discover_module_menu_migrations()
            if self.module_filter:
                discovered = [x for x in discovered if x[0] == self.module_filter]
                if not discovered and self.module_filter not in remote_catalogs:
                    raise CommandError(
                        f'Модуль "{self.module_filter}" не найден: нет процесса '
                        f'menu.catalog и нет меню-миграции на диске ядра'
                    )

            for module_name, migration_stem, populate_func in discovered:
                if module_name in remote_catalogs:
                    continue
                try:
                    populate_func(apps, schema_editor)
                    populated_modules.append(module_name)
                    self.stdout.write(self.style.SUCCESS(f'  {module_name}: {migration_stem}'))
                except Exception as e:
                    self.stderr.write(
                        self.style.ERROR(f'  {module_name}: ошибка — {e}')
                    )
                    raise

        layout_stats = materialize_all_layouts()
        self.stdout.write(
            f'Применён layout: {layout_stats["items"]} пунктов, '
            f'{layout_stats["separators"]} разделителей'
        )
        orphan_stats = cleanup_orphan_layouts()
        if orphan_stats['placements'] or orphan_stats['separator_layouts']:
            self.stdout.write(
                f'Удалены устаревшие layout: {orphan_stats["placements"]} placements, '
                f'{orphan_stats["separator_layouts"]} separator layouts'
            )

        _restore_access_map(access_map)
        self.stdout.write(f'Восстановлено настроек доступа: {len(access_map)}')
        if populated_modules:
            _reapply_module_sidebar_role_groups(populated_modules)
            self.stdout.write('Повторно применены allowed_role_groups модулей с menu_sidebar')

        from src.core.cms.adp.menu.menu_cache import invalidate_user_menu_cache

        invalidate_user_menu_cache()
        self.stdout.write('Сброшен серверный кэш меню')

        self.stdout.write(self.style.SUCCESS('Готово. Обновите страницу (F5) для отображения меню.'))
