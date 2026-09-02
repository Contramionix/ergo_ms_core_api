# -*- coding: utf-8 -*-
"""
Management command: восстановление меню из populate-функций миграций.

Пересоздаёт каталог (MenuItem/MenuSeparator), сохраняя:
- layout (order/parent/is_active, якоря разделителей) в MenuLayoutPlacement /
  MenuSeparatorLayout;
- настройки доступа пунктов и разделителей (allowed_roles / allowed_role_groups);
- админские пункты с catalog_key ``admin::*``.
"""

import importlib
import inspect
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import migrations

from src.config.settings.base import DJANGO_CORE_DIR, MODULES_DIR
from src.core.cms.adp.menu.models import MenuItem, MenuSeparator

MENU_MARKERS = ('MenuMigrationHelper', 'MenuItem', 'MenuSeparator')
_RE_POPULATE_MENU_FUNC = re.compile(r'\bpopulate_\w+_menu\b')

_RE_HELPER_SOURCE = re.compile(r"MenuMigrationHelper\s*\(\s*apps\s*,\s*['\"]([^'\"]+)['\"]")
_RE_DELETE_SOURCE = re.compile(
    r"\.filter\s*\(\s*module_source\s*=\s*['\"]([^'\"]+)['\"]\s*\)\s*\.delete\s*\("
)
_RE_UPDATE_SOURCE = re.compile(
    r"\.filter\s*\(\s*module_source\s*=\s*['\"]([^'\"]+)['\"]\s*\)\s*\.update\s*\("
)


class _MigrationApps:
    """Обёртка для вызова populate-функций миграций вне контекста миграций."""

    def get_model(self, app_label, model_name):
        from django.apps import apps
        return apps.get_model(app_label, model_name)


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


def _topological_sort(items, dep_fn):
    """
    Топологическая сортировка по графу зависимостей.
    dep_fn(item) -> список элементов, от которых зависит item (должны выполниться раньше).
    """
    result = []
    visited = set()
    temp = set()

    def visit(node):
        if node in temp:
            return
        if node in visited:
            return
        temp.add(node)
        for dep in dep_fn(node):
            visit(dep)
        temp.remove(node)
        visited.add(node)
        result.append(node)

    for item in items:
        visit(item)
    return result


def _callable_source(func) -> str:
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return ''


def _discover_core_menu_migrations():
    """
    Сканирует core/cms/adp/migrations/ на data-миграции меню.

    Берёт все forward RunPython, где в теле есть маркеры меню (не только первую
    операцию файла — squash 0001 содержит populate_core_menu не первым).

    Фильтр create+delete смотрит только forward-код (не reverse_populate), иначе
    squash ошибочно пропускается и restore_menu оставляет пустой каталог.
    """
    migrations_dir = DJANGO_CORE_DIR / 'cms' / 'adp' / 'migrations'
    if not migrations_dir.exists():
        return []

    raw = []
    for path in sorted(migrations_dir.glob('*.py')):
        if path.name == '__init__.py':
            continue
        stem = path.stem
        if not stem[0].isdigit():
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if not any(marker in content for marker in MENU_MARKERS):
            continue
        try:
            mod = importlib.import_module(f'src.core.cms.adp.migrations.{stem}')
        except (ImportError, ModuleNotFoundError):
            continue

        # Оркестраторы (call restore_menu) не входят в цепочку — иначе рекурсия.
        if getattr(mod, 'MENU_RESTORE_ORCHESTRATOR', False):
            continue
        # Одноразовые schema/backfill-миграции (не populate меню).
        if getattr(mod, 'MENU_RESTORE_SKIP', False):
            continue

        migration_class = getattr(mod, 'Migration', None)
        if not migration_class or not issubclass(migration_class, migrations.Migration):
            continue

        for op in migration_class.operations:
            if not isinstance(op, migrations.RunPython):
                continue
            code = op.code
            if code is None or code is migrations.RunPython.noop:
                continue
            # remove_*/reverse_* — очистка, не seed; в squash после add_* ломают
            # restore (CASCADE на таблицы модулей, которых ещё нет в цепочке migrate).
            func_name = getattr(code, '__name__', '') or ''
            if func_name.startswith(('remove_', 'reverse_', 'delete_')):
                continue
            forward_src = _callable_source(code)
            if not any(marker in forward_src for marker in MENU_MARKERS):
                continue
            raw.append((stem, code.__name__, code, forward_src))

    # Не фильтруем create+delete на уровне stem: squash 0001 и create, и удаляет
    # module_source в разных forward-операциях одного файла — старый фильтр выкидывал
    # весь populate_core_menu и оставлял пустое меню после restore_menu.
    return [(stem, func_name, func) for stem, func_name, func, _ in raw]


def _is_module_menu_migration(content: str) -> bool:
    """Миграция данных меню модуля (в т.ч. через seed/populate-хелперы)."""
    if not any(marker in content for marker in MENU_MARKERS):
        return False
    if 'MenuMigrationHelper' in content:
        return True
    if _RE_POPULATE_MENU_FUNC.search(content):
        return True
    return 'MenuItem' in content and 'module_source' in content


def _iter_module_migration_dirs(module_dir):
    """
    Каталоги миграций модуля: api/migrations и вложенные api/*/migrations
    (например api/<подраздел>/migrations).
    """
    api_dir = module_dir / 'api'
    if not api_dir.is_dir():
        return

    main = api_dir / 'migrations'
    if main.is_dir():
        yield (), main

    for sub in sorted(api_dir.iterdir()):
        if not sub.is_dir() or sub.name in ('migrations', '__pycache__'):
            continue
        nested = sub / 'migrations'
        if nested.is_dir():
            yield (sub.name,), nested


def _module_menu_migration_import_path(module_name, subpath_parts, stem):
    base = f'modules.{module_name}.api'
    if subpath_parts:
        base += '.' + '.'.join(subpath_parts)
    return f'{base}.migrations.{stem}'


def _load_module_menu_migration_ops(module_name, subpath_parts, migrations_dir):
    """
    Все menu data-миграции каталога в порядке номера.

    Не берём «только последнюю»: после полного clear+create часто идут
    rename/update (поздние data-миграции модуля) — без предшествующих
    populate они ничего не создают.

    Фильтр create+delete из core здесь нельзя: в reverse() почти каждой
    menu-миграции модуля есть ``.filter(module_source=...).delete()``, и эвристика
    ошибочно выкидывает сами populate.
    """
    raw = []
    for path in sorted(migrations_dir.glob('*.py')):
        if path.name == '__init__.py':
            continue
        stem = path.stem
        if not stem[0].isdigit():
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if not _is_module_menu_migration(content):
            continue

        try:
            mod = importlib.import_module(
                _module_menu_migration_import_path(module_name, subpath_parts, stem)
            )
        except (ImportError, ModuleNotFoundError):
            continue

        migration_class = getattr(mod, 'Migration', None)
        if not migration_class or not issubclass(migration_class, migrations.Migration):
            continue

        populate_func = None
        for op in migration_class.operations:
            if isinstance(op, migrations.RunPython):
                populate_func = op.code
                break
        if not populate_func:
            continue

        raw.append((stem, populate_func, migration_class.dependencies))

    subpath_key = subpath_parts[0] if subpath_parts else ''
    return [
        (stem, populate_func, deps, subpath_key)
        for stem, populate_func, deps in raw
    ]


def _discover_module_menu_migrations():
    """
    Сканирует modules/*/api/migrations и modules/*/api/*/migrations на menu data migrations.
    Для каждого каталога проигрывает цепочку menu-миграций по номеру (populate + rename/update).
    Возвращает список (module_name, migration_stem, populate_func) в порядке зависимостей.
    """
    from src.core.utils.module_registry import is_module_disabled, is_valid_module_dir_name

    if not MODULES_DIR.exists():
        return []

    module_entries = {}

    for module_dir in MODULES_DIR.iterdir():
        if not module_dir.is_dir() or module_dir.name.startswith('.'):
            continue

        module_name = module_dir.name
        if not is_valid_module_dir_name(module_name) or is_module_disabled(module_name):
            continue

        entries = []
        for subpath_parts, migrations_dir in _iter_module_migration_dirs(module_dir):
            entries.extend(
                _load_module_menu_migration_ops(module_name, subpath_parts, migrations_dir)
            )

        if entries:
            module_entries[module_name] = entries

    if not module_entries:
        return []

    def get_deps(module_name):
        result = []
        for _, _, deps, _ in module_entries[module_name]:
            for dep_app, _ in deps:
                if dep_app in module_entries and dep_app != module_name:
                    result.append(dep_app)
        return result

    sorted_modules = _topological_sort(list(module_entries.keys()), get_deps)

    discovered = []
    for module_name in sorted_modules:
        entries = sorted(
            module_entries[module_name],
            key=lambda item: (item[3] != '', item[3], item[0]),
        )
        for stem, populate_func, _, _ in entries:
            discovered.append((module_name, stem, populate_func))

    return discovered


class Command(BaseCommand):
    help = 'Восстанавливает меню из populate-функций core-миграций и установленных модулей'

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

        apps = _MigrationApps()
        schema_editor = None

        core_migrations = _discover_core_menu_migrations()

        from src.core.cms.adp.menu.layout_service import (
            cleanup_orphan_layouts,
            delete_seed_catalog,
            materialize_all_layouts,
        )
        from src.core.cms.adp.menu.models import MenuLayoutPlacement, MenuSeparatorLayout

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
            discovered = _discover_module_menu_migrations()
            if self.module_filter:
                discovered = [x for x in discovered if x[0] == self.module_filter]
            for name, stem, _ in discovered:
                self.stdout.write(self.style.WARNING(f'[dry-run] Модуль {name}: {stem}'))
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
            discovered = _discover_module_menu_migrations()
            if self.module_filter:
                discovered = [x for x in discovered if x[0] == self.module_filter]
                if not discovered:
                    raise CommandError(f'Модуль "{self.module_filter}" не найден или не имеет меню-миграции')

            for module_name, migration_stem, populate_func in discovered:
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
