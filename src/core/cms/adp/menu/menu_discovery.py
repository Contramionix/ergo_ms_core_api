# -*- coding: utf-8 -*-
"""Поиск data-миграций меню ядра и модулей (restore_menu и menu.catalog)."""

from __future__ import annotations

import importlib
import inspect
import re

from django.db import migrations

from src.config.settings.base import DJANGO_CORE_DIR, MODULES_DIR

MENU_MARKERS = ('MenuMigrationHelper', 'MenuItem', 'MenuSeparator')
_RE_POPULATE_MENU_FUNC = re.compile(r'\bpopulate_\w+_menu\b')


class MigrationApps:
    """Обёртка для вызова populate-функций миграций вне контекста миграций."""

    def get_model(self, app_label, model_name):
        from django.apps import apps
        return apps.get_model(app_label, model_name)


def topological_sort(items, dep_fn):
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


def callable_source(func) -> str:
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return ''


def discover_core_menu_migrations():
    """
    Сканирует core/cms/adp/migrations/ на data-миграции меню.

    Берёт все forward RunPython, где в теле есть маркеры меню (не только первую
    операцию файла — squash 0001 содержит populate_core_menu не первым).
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

        if getattr(mod, 'MENU_RESTORE_ORCHESTRATOR', False):
            continue
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
            func_name = getattr(code, '__name__', '') or ''
            if func_name.startswith(('remove_', 'reverse_', 'delete_')):
                continue
            forward_src = callable_source(code)
            if not any(marker in forward_src for marker in MENU_MARKERS):
                continue
            raw.append((stem, code.__name__, code, forward_src))

    return [(stem, func_name, func) for stem, func_name, func, _ in raw]


def is_module_menu_migration(content: str) -> bool:
    """Миграция данных меню модуля (в т.ч. через seed/populate-хелперы)."""
    if not any(marker in content for marker in MENU_MARKERS):
        return False
    if 'MenuMigrationHelper' in content:
        return True
    if _RE_POPULATE_MENU_FUNC.search(content):
        return True
    return 'MenuItem' in content and 'module_source' in content


def iter_module_migration_dirs(module_dir):
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


def module_menu_migration_import_path(module_name, subpath_parts, stem):
    base = f'modules.{module_name}.api'
    if subpath_parts:
        base += '.' + '.'.join(subpath_parts)
    return f'{base}.migrations.{stem}'


def load_module_menu_migration_ops(module_name, subpath_parts, migrations_dir):
    """
    Все menu data-миграции каталога в порядке номера.

    Не берём «только последнюю»: после полного clear+create часто идут
    rename/update (поздние data-миграции модуля).
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
        if not is_module_menu_migration(content):
            continue

        try:
            mod = importlib.import_module(
                module_menu_migration_import_path(module_name, subpath_parts, stem)
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


def discover_one_module_menu_migrations(module_name: str):
    """Цепочка menu-миграций одного модуля: (stem, populate_func)."""
    from src.core.utils.module_registry import is_module_disabled, is_valid_module_dir_name

    if not is_valid_module_dir_name(module_name) or is_module_disabled(module_name):
        return []
    if not MODULES_DIR.exists():
        return []
    module_dir = MODULES_DIR / module_name
    if not module_dir.is_dir():
        return []

    entries = []
    for subpath_parts, migrations_dir in iter_module_migration_dirs(module_dir):
        entries.extend(
            load_module_menu_migration_ops(module_name, subpath_parts, migrations_dir)
        )
    entries = sorted(entries, key=lambda item: (item[3] != '', item[3], item[0]))
    return [(stem, populate_func) for stem, populate_func, _, _ in entries]


def discover_module_menu_migrations():
    """
    Сканирует modules/*/api/migrations и modules/*/api/*/migrations.
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
        for subpath_parts, migrations_dir in iter_module_migration_dirs(module_dir):
            entries.extend(
                load_module_menu_migration_ops(module_name, subpath_parts, migrations_dir)
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

    sorted_modules = topological_sort(list(module_entries.keys()), get_deps)

    discovered = []
    for module_name in sorted_modules:
        entries = sorted(
            module_entries[module_name],
            key=lambda item: (item[3] != '', item[3], item[0]),
        )
        for stem, populate_func, _, _ in entries:
            discovered.append((module_name, stem, populate_func))

    return discovered
