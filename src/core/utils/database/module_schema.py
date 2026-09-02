"""
Имена PostgreSQL-схем для ядра и модулей (уровень 2 модульности).

На SQLite схемы не используются: search_path не задаётся, таблицы остаются
в одной файловой БД. Имена модулей ядро не хардкодит — читает hook
``modules/<name>/api/schema.yaml``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

CORE_SCHEMA = 'core'
MODULE_SCHEMA_PREFIX = 'm_'
SCHEMA_HOOK_REL = Path('api') / 'schema.yaml'


def module_schema_name(module_dir_name: str) -> str:
    """Имя PG-схемы модуля: m_<folder>, без хардкода списка модулей."""
    safe = ''.join(
        ch if ch.isalnum() or ch == '_' else '_'
        for ch in (module_dir_name or '').strip().lower()
    )
    return f'{MODULE_SCHEMA_PREFIX}{safe}'


def project_root() -> Path:
    """Корень мета-репозитория (рядом с ``modules/`` и ``core/``)."""
    return Path(__file__).resolve().parents[6]


def _modules_dir() -> Path:
    return project_root() / 'modules'


def _read_schema_hook(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        import yaml
    except ImportError:
        return None
    data = yaml.safe_load(text) or {}
    return data if isinstance(data, dict) else None


@lru_cache(maxsize=1)
def discovered_module_schemas() -> tuple[tuple[str, str, bool], ...]:
    """
    (имя_папки, имя_схемы, isolated).

    isolated=True — CI запрещает FK наружу. Схема есть и при isolated=False.
    """
    root = _modules_dir()
    if not root.is_dir():
        return ()
    found: list[tuple[str, str, bool]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith('.'):
            continue
        hook = child / SCHEMA_HOOK_REL
        if not hook.is_file():
            continue
        data = _read_schema_hook(hook) or {}
        schema = str(data.get('schema') or module_schema_name(child.name)).strip()
        isolated = bool(data.get('isolated', True))
        found.append((child.name, schema, isolated))
    return tuple(found)


def installed_module_schema_names() -> list[str]:
    """Схемы m_* из schema.yaml и установленных Django-приложений modules.*."""
    seen: list[str] = []
    for _name, schema, _flag in discovered_module_schemas():
        if schema and schema not in seen:
            seen.append(schema)
    try:
        from django.apps import apps
    except ImportError:
        return seen
    if not apps.ready:
        return seen
    for config in apps.get_app_configs():
        name = config.name or ''
        if not name.startswith('modules.'):
            continue
        schema = module_schema_name(name.split('.')[1])
        if schema and schema not in seen:
            seen.append(schema)
    return seen


def search_path_for_process(
    *,
    process_role: str = '',
    process_modules: str = '',
) -> str:
    """
    search_path чтения процесса.

    Монолит: core, затем все m_*. Процесс модуля: своя схема, затем core.
    public в search_path нет. CREATE TABLE идёт в схему приложения
    (Migration.apply), не в первую схему этого пути.
    """
    role = (process_role or os.environ.get('ERGO_PROCESS_ROLE', '') or '').strip()
    explicit = (process_modules or os.environ.get('PROCESS_MODULES', '') or '').strip()
    extra = installed_module_schema_names()

    if role.startswith('module:'):
        name = role.split(':', 1)[1].strip()
        own = module_schema_name(name)
        parts = [own, CORE_SCHEMA]
        # Без пробелов: libpq режет `-c search_path=` по запятой.
        return ','.join(parts)

    if explicit:
        names = [n.strip() for n in explicit.split(',') if n.strip()]
        own = [module_schema_name(n) for n in names]
        parts = [*own, CORE_SCHEMA]
        return ','.join(parts)

    parts = [CORE_SCHEMA, *extra]
    seen: list[str] = []
    for item in parts:
        if item and item not in seen:
            seen.append(item)
    return ','.join(seen)


def apply_search_path_options(databases: Mapping[str, dict]) -> dict:
    """Вешает хуки схем. search_path задаётся на connect, не через OPTIONS.

    OPTIONS ``-c search_path=core`` падает на свежей БД, пока схемы ещё нет.
    """
    from src.core.utils.database.schema_runtime import install_schema_hooks

    install_schema_hooks()
    return dict(databases)


def schema_for_app_label(app_label: str) -> str:
    """Схема PostgreSQL для Django app_label: m_<модуль> или core."""
    from django.apps import apps

    try:
        config = apps.get_app_config(app_label)
    except LookupError:
        return CORE_SCHEMA
    name = config.name or ''
    if name.startswith('modules.'):
        folder = name.split('.')[1]
        return module_schema_name(folder)
    return CORE_SCHEMA


def search_path_for_app(app_label: str) -> str:
    """search_path миграции: схема приложения первой (CREATE TABLE)."""
    own = schema_for_app_label(app_label)
    rest = [part for part in search_path_for_process().split(',') if part and part != own]
    return ','.join([own, *rest])


def django_app_labels_for_module(module_dir_name: str) -> tuple[str, ...]:
    """app_label всех Django-приложений в ``modules/<name>/``."""
    from django.apps import apps

    name = (module_dir_name or '').strip()
    if not name:
        return ()
    exact = f'modules.{name}.api'
    prefix = f'modules.{name}.'
    labels: list[str] = []
    for config in apps.get_app_configs():
        pkg = config.name or ''
        if pkg == exact or pkg.startswith(prefix):
            labels.append(config.label)
    return tuple(sorted(set(labels)))


def owner_app_label(relname: str, labels: Sequence[str]) -> str | None:
    """Самый длинный app_label, которому принадлежит имя таблицы/view."""
    matches = [
        label for label in labels
        if relname == label or relname.startswith(f'{label}_')
    ]
    if not matches:
        return None
    return max(matches, key=len)


def model_tables_for_app_label(app_label: str) -> tuple[str, ...]:
    from django.apps import apps

    try:
        config = apps.get_app_config(app_label)
    except LookupError:
        return ()
    seen: list[str] = []
    for model in config.get_models():
        if model._meta.proxy:
            continue
        table = model._meta.db_table
        if table and table not in seen:
            seen.append(table)
        for field in model._meta.local_many_to_many:
            through = field.remote_field.through
            m2m_table = through._meta.db_table
            if not m2m_table or m2m_table in seen:
                continue
            if through._meta.app_label == app_label or through._meta.auto_created:
                seen.append(m2m_table)
    return tuple(seen)


def is_postgres_connection(connection) -> bool:
    vendor = getattr(connection, 'vendor', '') or ''
    return vendor == 'postgresql'
