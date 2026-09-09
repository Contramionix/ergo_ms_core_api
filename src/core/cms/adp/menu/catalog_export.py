# -*- coding: utf-8 -*-
"""
Выгрузка каталога меню модуля и регистрация в группе ``menu.catalog``.

Процесс ``module:<name>`` отдаёт дерево пунктов по HTTP-мосту.
Ядро забирает его через ``bridge.all`` и пишет в свои таблицы MenuItem.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from src.core.integrations import bridge
from src.core.integrations.module_contracts import MENU_CATALOG_GROUP

from .catalog_sandbox import MenuCatalogStore
from .menu_discovery import discover_one_module_menu_migrations

logger = logging.getLogger('cms.adp.menu.catalog')


def module_source_for(module_name: str) -> str:
    return f'modules/{module_name}'


def normalize_menu_catalog(raw: Any) -> dict[str, Any] | None:
    """Приводит ответ провайдера ``menu.catalog`` к JSON-снимку."""
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            logger.exception('menu.catalog: не удалось вызвать провайдер')
            return None
    if not isinstance(raw, dict):
        return None
    module_name = str(raw.get('module') or '').strip()
    module_source = str(raw.get('module_source') or '').strip()
    if not module_name and module_source.startswith('modules/'):
        module_name = module_source.split('/', 1)[1].split('/', 1)[0]
    if not module_source and module_name:
        module_source = module_source_for(module_name)
    if not module_name or not module_source:
        return None

    items = []
    for entry in raw.get('items') or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or '').strip()
        if not name:
            continue
        items.append({
            'catalog_key': (entry.get('catalog_key') or None),
            'name': name,
            'item_type': str(entry.get('item_type') or 'route'),
            'route_name': entry.get('route_name') or None,
            'icon': entry.get('icon') or None,
            'page': entry.get('page') or None,
            'external_url': entry.get('external_url') or None,
            'parent_catalog_key': entry.get('parent_catalog_key') or None,
            'order': entry.get('order'),
            'is_active': bool(entry.get('is_active', True)),
            'is_admin_only': bool(entry.get('is_admin_only', False)),
        })

    separators = []
    for entry in raw.get('separators') or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or '').strip()
        if not name:
            continue
        separators.append({
            'catalog_key': (entry.get('catalog_key') or None),
            'name': name,
            'before_order': entry.get('before_order'),
            'before_catalog_key': entry.get('before_catalog_key') or None,
            'is_active': bool(entry.get('is_active', True)),
        })

    if not items and not separators:
        return None
    return {
        'module': module_name,
        'module_source': module_source,
        'items': items,
        'separators': separators,
    }


def _export_via_hook(module_name: str) -> dict[str, Any] | None:
    try:
        mod = importlib.import_module(f'modules.{module_name}.api.menu_catalog')
    except (ImportError, ModuleNotFoundError):
        return None
    fn = getattr(mod, 'export_menu_catalog', None)
    if not callable(fn):
        return None
    return normalize_menu_catalog(fn())


def _export_via_sandbox(module_name: str) -> dict[str, Any] | None:
    discovered = discover_one_module_menu_migrations(module_name)
    if not discovered:
        return None
    store = MenuCatalogStore()
    apps = store.as_apps()
    for stem, populate_func in discovered:
        try:
            populate_func(apps, None)
        except Exception as exc:
            logger.warning(
                'menu.catalog: песочница %s (%s) пропустила populate: %s',
                module_name,
                stem,
                exc,
            )
    return normalize_menu_catalog(store.export(module_name, module_source_for(module_name)))


def _serialize_live_module(module_name: str) -> dict[str, Any] | None:
    from src.core.cms.adp.menu.models import MenuItem, MenuSeparator

    module_source = module_source_for(module_name)
    items = []
    for item in MenuItem.objects.filter(module_source=module_source).select_related('parent'):
        parent_key = None
        if item.parent_id:
            parent_key = getattr(item.parent, 'catalog_key', None)
        items.append({
            'catalog_key': item.catalog_key,
            'name': item.name,
            'item_type': item.item_type,
            'route_name': item.route_name,
            'icon': item.icon,
            'page': item.page,
            'external_url': item.external_url,
            'parent_catalog_key': parent_key,
            'order': item.order,
            'is_active': item.is_active,
            'is_admin_only': item.is_admin_only,
        })
    separators = []
    qs = MenuSeparator.objects.all()
    if hasattr(MenuSeparator, 'module_source'):
        qs = qs.filter(module_source=module_source)
    else:
        prefix = f'{module_source}::'
        qs = qs.filter(catalog_key__startswith=prefix)
    for sep in qs:
        separators.append({
            'catalog_key': sep.catalog_key,
            'name': sep.name,
            'before_order': sep.before_order,
            'before_catalog_key': getattr(sep, 'before_catalog_key', None),
            'is_active': sep.is_active,
        })
    return normalize_menu_catalog({
        'module': module_name,
        'module_source': module_source,
        'items': items,
        'separators': separators,
    })


def _export_via_atomic_replay(module_name: str) -> dict[str, Any] | None:
    """
    Проигрывает populate на живых моделях внутри транзакции и откатывает запись.

    Нужен модулям вроде тех, что вызывают MenuItem.objects напрямую,
    минуя apps.get_model. На процессе модуля это безопасно: ядро читает
    снимок, строки в БД не меняются.
    """
    from django.db import transaction

    from .menu_discovery import MigrationApps

    discovered = discover_one_module_menu_migrations(module_name)
    if not discovered:
        return None
    apps = MigrationApps()
    try:
        with transaction.atomic():
            for _stem, populate_func in discovered:
                populate_func(apps, None)
            snapshot = _serialize_live_module(module_name)
            transaction.set_rollback(True)
    except Exception:
        logger.exception('menu.catalog: atomic replay %s не удался', module_name)
        return None
    return snapshot


def export_module_menu_catalog(
    module_name: str,
    *,
    allow_live: bool = False,
) -> dict[str, Any] | None:
    """Собирает JSON-каталог меню модуля с диска этого процесса."""
    hook = _export_via_hook(module_name)
    if hook:
        return hook
    sandbox = _export_via_sandbox(module_name)
    if sandbox:
        return sandbox
    if allow_live:
        return _export_via_atomic_replay(module_name)
    return None


def collect_menu_catalogs(*, module_filter: str | None = None) -> dict[str, dict[str, Any]]:
    """Каталоги с локального реестра и с HTTP-соседей (``bridge.all``)."""
    catalogs: dict[str, dict[str, Any]] = {}
    raw = bridge.all(MENU_CATALOG_GROUP) or {}
    for key, obj in raw.items():
        catalog = normalize_menu_catalog(obj)
        if catalog is None:
            continue
        name = catalog['module']
        if module_filter and name != module_filter:
            continue
        catalogs[name] = catalog
    return catalogs


def _local_module_names_to_export() -> list[str]:
    from src.core.utils.module_registry import (
        get_process_role,
        is_module_disabled,
        is_valid_module_dir_name,
    )

    role = (get_process_role() or '').strip().lower()
    if role.startswith('module:'):
        name = role.split(':', 1)[1].strip()
        if name and is_valid_module_dir_name(name) and not is_module_disabled(name):
            return [name]
    return []


def register_process_menu_catalog() -> None:
    """
    Регистрирует ``menu.catalog`` для модуля этого процесса.

    На ядре не регистрируем: restore читает соседей по HTTP и локальные
    миграции на диске ядра, без atomic replay в том же процессе.
    """
    names = _local_module_names_to_export()
    for name in names:
        exporter: Callable[[], dict[str, Any] | None] = (
            lambda module=name: export_module_menu_catalog(module, allow_live=True)
        )
        bridge.provide_many(MENU_CATALOG_GROUP, name, exporter)
        logger.info('menu.catalog: процесс отдаёт меню модуля %s', name)
