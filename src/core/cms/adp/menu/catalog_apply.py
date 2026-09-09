# -*- coding: utf-8 -*-
"""Применение JSON-каталога меню модуля к таблицам ядра."""

from __future__ import annotations

from typing import Any

from .catalog_export import normalize_menu_catalog
from .migration_utils import MenuMigrationHelper


def _parent_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {
        item['catalog_key']: item
        for item in items
        if item.get('catalog_key')
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item: dict[str, Any]) -> None:
        key = item.get('catalog_key') or ''
        marker = key or id(item)
        if marker in seen:
            return
        parent_key = item.get('parent_catalog_key')
        if parent_key and parent_key in by_key:
            visit(by_key[parent_key])
        seen.add(marker)
        result.append(item)

    for item in items:
        visit(item)
    return result


def apply_module_menu_catalog(raw: dict[str, Any] | None) -> tuple[int, int]:
    """
    Пересоздаёт seed-каталог модуля из снимка.

    Layout (MenuLayoutPlacement) не трогает: helper накатывает его на catalog_key.
    Возвращает (число пунктов, число разделителей).
    """
    catalog = normalize_menu_catalog(raw)
    if catalog is None:
        return 0, 0

    from django.apps import apps

    helper = MenuMigrationHelper(apps, catalog['module_source'])
    helper.clear_module_items()

    created: dict[str, Any] = {}
    item_count = 0
    for spec in _parent_first(list(catalog['items'])):
        parent = None
        parent_key = spec.get('parent_catalog_key')
        if parent_key:
            parent = created.get(parent_key)
        order = spec.get('order')
        if order is not None:
            try:
                order = int(order)
            except (TypeError, ValueError):
                order = None
        item = helper.create_item(
            name=spec['name'],
            item_type=spec.get('item_type') or 'route',
            route_name=spec.get('route_name'),
            icon=spec.get('icon'),
            parent=parent,
            page=spec.get('page'),
            external_url=spec.get('external_url'),
            is_active=spec.get('is_active', True),
            is_admin_only=spec.get('is_admin_only', False),
            order=order,
        )
        item_count += 1
        key = getattr(item, 'catalog_key', None) or spec.get('catalog_key')
        if key:
            created[key] = item

    sep_count = 0
    for spec in catalog.get('separators') or []:
        before_order = spec.get('before_order')
        if before_order is None:
            before_order = 0
        try:
            before_order = int(before_order)
        except (TypeError, ValueError):
            before_order = 0
        helper.create_separator(
            spec['name'],
            before_order,
            is_active=spec.get('is_active', True),
            before_catalog_key=spec.get('before_catalog_key'),
        )
        sep_count += 1
    return item_count, sep_count
