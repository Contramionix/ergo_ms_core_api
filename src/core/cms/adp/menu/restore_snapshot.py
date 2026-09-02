# -*- coding: utf-8 -*-
"""
Снимок меню для отмены restore_menu.

Хранится в Django cache с коротким TTL; ключ привязан к user_id.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from django.core.cache import cache
from django.db import transaction

from .menu_cache import invalidate_user_menu_cache
from .models import (
    MenuItem,
    MenuLayoutPlacement,
    MenuSeparator,
    MenuSeparatorLayout,
)

SNAPSHOT_VERSION = 1
UNDO_CACHE_PREFIX = 'menu:restore_undo:'
# Toast undo ~10 с; запас на медленный клик / сеть.
UNDO_CACHE_TTL = 15 * 60


def _item_payload(item: MenuItem) -> dict[str, Any]:
    return {
        'public_id': str(item.public_id),
        'catalog_key': item.catalog_key,
        'name': item.name,
        'route_name': item.route_name,
        'icon': item.icon,
        'item_type': item.item_type,
        'page': item.page,
        'external_url': item.external_url,
        'parent_public_id': str(item.parent.public_id) if item.parent_id else None,
        'order': item.order,
        'is_active': item.is_active,
        'is_admin_only': item.is_admin_only,
        'module_source': item.module_source,
        'allowed_role_ids': list(item.allowed_roles.values_list('id', flat=True)),
        'allowed_role_group_ids': list(item.allowed_role_groups.values_list('id', flat=True)),
    }


def _separator_payload(sep: MenuSeparator) -> dict[str, Any]:
    return {
        'public_id': str(sep.public_id),
        'catalog_key': sep.catalog_key,
        'module_source': sep.module_source,
        'name': sep.name,
        'before_order': sep.before_order,
        'before_catalog_key': sep.before_catalog_key,
        'is_active': sep.is_active,
        'is_admin_only': sep.is_admin_only,
        'allowed_role_ids': list(sep.allowed_roles.values_list('id', flat=True)),
        'allowed_role_group_ids': list(sep.allowed_role_groups.values_list('id', flat=True)),
    }


def capture_menu_snapshot() -> dict[str, Any]:
    """Сериализует текущий каталог и layout меню."""
    items = (
        MenuItem.objects.select_related('parent')
        .prefetch_related('allowed_roles', 'allowed_role_groups')
        .all()
    )
    separators = MenuSeparator.objects.prefetch_related(
        'allowed_roles',
        'allowed_role_groups',
    ).all()

    return {
        'version': SNAPSHOT_VERSION,
        'items': [_item_payload(item) for item in items],
        'separators': [_separator_payload(sep) for sep in separators],
        'placements': list(
            MenuLayoutPlacement.objects.values(
                'catalog_key',
                'parent_catalog_key',
                'order',
                'is_active',
            )
        ),
        'separator_layouts': list(
            MenuSeparatorLayout.objects.values(
                'catalog_key',
                'name',
                'before_catalog_key',
                'before_order',
                'is_active',
            )
        ),
    }


def store_undo_snapshot(*, user_id: int, snapshot: dict[str, Any]) -> str:
    """Сохраняет снимок в cache; возвращает opaque undo_token."""
    token = uuid.uuid4().hex
    cache.set(
        f'{UNDO_CACHE_PREFIX}{int(user_id)}:{token}',
        snapshot,
        timeout=UNDO_CACHE_TTL,
    )
    return token


def pop_undo_snapshot(*, user_id: int, token: str) -> Optional[dict[str, Any]]:
    """Достаёт и удаляет снимок (одноразовый токен)."""
    if not token or not isinstance(token, str):
        return None
    key = f'{UNDO_CACHE_PREFIX}{int(user_id)}:{token.strip()}'
    snapshot = cache.get(key)
    if snapshot is None:
        return None
    cache.delete(key)
    if not isinstance(snapshot, dict) or snapshot.get('version') != SNAPSHOT_VERSION:
        return None
    return snapshot


def _apply_m2m(obj, role_ids, group_ids) -> None:
    if role_ids:
        obj.allowed_roles.set(role_ids)
    if group_ids:
        obj.allowed_role_groups.set(group_ids)


@transaction.atomic
def apply_menu_snapshot(snapshot: dict[str, Any]) -> None:
    """
    Полностью заменяет каталог и layout состоянием из снимка.
    """
    items_data = snapshot.get('items') or []
    separators_data = snapshot.get('separators') or []
    placements_data = snapshot.get('placements') or []
    sep_layouts_data = snapshot.get('separator_layouts') or []

    MenuItem.objects.all().delete()
    MenuSeparator.objects.all().delete()
    MenuLayoutPlacement.objects.all().delete()
    MenuSeparatorLayout.objects.all().delete()

    by_public_id: dict[str, MenuItem] = {}
    for data in items_data:
        public_id = data.get('public_id')
        if not public_id:
            continue
        item = MenuItem(
            public_id=uuid.UUID(str(public_id)),
            catalog_key=data.get('catalog_key'),
            name=data.get('name') or '',
            route_name=data.get('route_name'),
            icon=data.get('icon'),
            item_type=data.get('item_type') or 'route',
            page=data.get('page'),
            external_url=data.get('external_url'),
            parent=None,
            order=data.get('order'),
            is_active=bool(data.get('is_active', True)),
            is_admin_only=bool(data.get('is_admin_only', False)),
            module_source=data.get('module_source'),
        )
        item.save()
        by_public_id[str(public_id)] = item

    for data in items_data:
        public_id = str(data.get('public_id') or '')
        parent_public_id = data.get('parent_public_id')
        if not public_id or not parent_public_id:
            continue
        item = by_public_id.get(public_id)
        parent = by_public_id.get(str(parent_public_id))
        if item is None or parent is None:
            continue
        item.parent = parent
        item.save(update_fields=['parent'])

    for data in items_data:
        public_id = str(data.get('public_id') or '')
        item = by_public_id.get(public_id)
        if item is None:
            continue
        _apply_m2m(
            item,
            data.get('allowed_role_ids') or [],
            data.get('allowed_role_group_ids') or [],
        )

    for data in separators_data:
        public_id = data.get('public_id')
        if not public_id:
            continue
        sep = MenuSeparator(
            public_id=uuid.UUID(str(public_id)),
            catalog_key=data.get('catalog_key'),
            module_source=data.get('module_source'),
            name=data.get('name') or '',
            before_order=data.get('before_order') or 0,
            before_catalog_key=data.get('before_catalog_key'),
            is_active=bool(data.get('is_active', True)),
            is_admin_only=bool(data.get('is_admin_only', False)),
        )
        sep.save()
        _apply_m2m(
            sep,
            data.get('allowed_role_ids') or [],
            data.get('allowed_role_group_ids') or [],
        )

    for data in placements_data:
        catalog_key = data.get('catalog_key')
        if not catalog_key:
            continue
        MenuLayoutPlacement.objects.create(
            catalog_key=catalog_key,
            parent_catalog_key=data.get('parent_catalog_key'),
            order=data.get('order') or 0,
            is_active=bool(data.get('is_active', True)),
        )

    for data in sep_layouts_data:
        catalog_key = data.get('catalog_key')
        if not catalog_key:
            continue
        MenuSeparatorLayout.objects.create(
            catalog_key=catalog_key,
            name=data.get('name') or '',
            before_catalog_key=data.get('before_catalog_key'),
            before_order=data.get('before_order') or 0,
            is_active=bool(data.get('is_active', True)),
        )

    invalidate_user_menu_cache()
