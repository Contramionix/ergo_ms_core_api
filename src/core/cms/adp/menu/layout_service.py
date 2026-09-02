# -*- coding: utf-8 -*-
"""
Layout меню — источник истины для order / parent / is_active / якорей разделителей.

Каталог (MenuItem / MenuSeparator) пересоздаётся миграциями и restore;
layout-таблицы переживают wipe и накатываются обратно на каталог.
"""

from __future__ import annotations

from typing import Optional

from django.db import transaction

from .catalog_keys import (
    build_item_catalog_key,
    build_separator_catalog_key,
    is_admin_catalog_key,
)
from .models import MenuItem, MenuLayoutPlacement, MenuSeparator, MenuSeparatorLayout


def ensure_item_catalog_key(item: MenuItem) -> str:
    """Гарантирует catalog_key у пункта (для админских — admin::{public_id})."""
    if item.catalog_key:
        return item.catalog_key

    parent_key = None
    if item.parent_id:
        parent = item.parent
        parent_key = parent.catalog_key or ensure_item_catalog_key(parent)

    key = build_item_catalog_key(
        item.module_source,
        item_type=item.item_type or 'route',
        route_name=item.route_name,
        page=item.page,
        external_url=item.external_url,
        name=item.name,
        parent_catalog_key=parent_key,
        public_id=str(item.public_id),
    )
    item.catalog_key = key
    item.save(update_fields=['catalog_key'])
    return key


def ensure_separator_catalog_key(sep: MenuSeparator, module_source: Optional[str] = None) -> str:
    if sep.catalog_key:
        return sep.catalog_key
    key = build_separator_catalog_key(
        module_source or sep.module_source,
        sep.name,
        public_id=str(sep.public_id),
    )
    sep.catalog_key = key
    update_fields = ['catalog_key']
    if module_source and not sep.module_source:
        sep.module_source = module_source
        update_fields.append('module_source')
    sep.save(update_fields=update_fields)
    return key


def upsert_item_placement(
    catalog_key: str,
    *,
    parent_catalog_key: Optional[str],
    order: int,
    is_active: bool,
) -> MenuLayoutPlacement:
    placement, _ = MenuLayoutPlacement.objects.update_or_create(
        catalog_key=catalog_key,
        defaults={
            'parent_catalog_key': parent_catalog_key or None,
            'order': order if order is not None else 0,
            'is_active': is_active,
        },
    )
    return placement


def sync_placement_from_item(item: MenuItem) -> MenuLayoutPlacement:
    """Записать эффективный layout пункта в таблицу размещений."""
    key = ensure_item_catalog_key(item)
    parent_key = None
    if item.parent_id:
        parent_key = item.parent.catalog_key or ensure_item_catalog_key(item.parent)
    return upsert_item_placement(
        key,
        parent_catalog_key=parent_key,
        order=item.order if item.order is not None else 0,
        is_active=item.is_active,
    )


def ensure_placement_for_item(item: MenuItem, *, seed_if_missing: bool = True) -> MenuLayoutPlacement:
    """
    Вернуть placement для пункта.
    Если записи нет и seed_if_missing — создать из текущих полей MenuItem.
    """
    key = ensure_item_catalog_key(item)
    existing = MenuLayoutPlacement.objects.filter(catalog_key=key).first()
    if existing:
        return existing
    if not seed_if_missing:
        raise MenuLayoutPlacement.DoesNotExist
    return sync_placement_from_item(item)


def apply_placement_to_item(
    item: MenuItem,
    placement: MenuLayoutPlacement,
    key_to_item: dict[str, MenuItem],
) -> None:
    """Материализовать placement на MenuItem (для runtime, читающего поля модели)."""
    parent = None
    if placement.parent_catalog_key:
        parent = key_to_item.get(placement.parent_catalog_key)
    item.parent = parent
    item.order = placement.order
    item.is_active = placement.is_active
    item.save(update_fields=['parent', 'order', 'is_active'])


@transaction.atomic
def apply_all_item_layouts() -> int:
    """Накатить все MenuLayoutPlacement на существующие MenuItem. Возвращает число применений."""
    items = list(MenuItem.objects.select_related('parent').all())
    key_to_item = {}
    for item in items:
        key = item.catalog_key or ensure_item_catalog_key(item)
        key_to_item[key] = item

    applied = 0
    placements = list(MenuLayoutPlacement.objects.all())
    for placement in placements:
        item = key_to_item.get(placement.catalog_key)
        if item is None:
            continue
        apply_placement_to_item(item, placement, key_to_item)
        applied += 1
    return applied


def upsert_separator_layout(
    catalog_key: str,
    *,
    before_catalog_key: Optional[str],
    before_order: int,
    is_active: bool,
    name: Optional[str] = None,
) -> MenuSeparatorLayout:
    defaults = {
        'before_catalog_key': before_catalog_key or None,
        'before_order': before_order if before_order is not None else 0,
        'is_active': is_active,
    }
    if name is not None:
        defaults['name'] = name
    layout, _ = MenuSeparatorLayout.objects.update_or_create(
        catalog_key=catalog_key,
        defaults=defaults,
    )
    return layout


def sync_layout_from_separator(sep: MenuSeparator) -> MenuSeparatorLayout:
    key = ensure_separator_catalog_key(sep)
    return upsert_separator_layout(
        key,
        before_catalog_key=sep.before_catalog_key,
        before_order=sep.before_order or 0,
        is_active=sep.is_active,
        name=sep.name,
    )


def apply_separator_layout(sep: MenuSeparator, layout: MenuSeparatorLayout) -> None:
    sep.before_catalog_key = layout.before_catalog_key
    sep.before_order = layout.before_order
    sep.is_active = layout.is_active
    if layout.name:
        sep.name = layout.name
    sep.save(update_fields=['before_catalog_key', 'before_order', 'is_active', 'name'])


@transaction.atomic
def apply_all_separator_layouts(key_to_item: Optional[dict[str, MenuItem]] = None) -> int:
    """Накатить MenuSeparatorLayout; before_order выровнять по якорю, если есть."""
    if key_to_item is None:
        key_to_item = {
            (item.catalog_key or ensure_item_catalog_key(item)): item
            for item in MenuItem.objects.all()
        }

    applied = 0
    for sep in MenuSeparator.objects.all():
        key = sep.catalog_key or ensure_separator_catalog_key(sep)
        layout = MenuSeparatorLayout.objects.filter(catalog_key=key).first()
        if layout is None:
            sync_layout_from_separator(sep)
            continue
        apply_separator_layout(sep, layout)
        if sep.before_catalog_key and sep.before_catalog_key in key_to_item:
            anchor = key_to_item[sep.before_catalog_key]
            if anchor.order is not None and sep.before_order != anchor.order:
                sep.before_order = anchor.order
                sep.save(update_fields=['before_order'])
                layout.before_order = anchor.order
                layout.save(update_fields=['before_order'])
        applied += 1
    return applied


@transaction.atomic
def materialize_all_layouts() -> dict:
    """Полная материализация layout → каталог после restore / sync."""
    items_n = apply_all_item_layouts()
    key_to_item = {
        (item.catalog_key or ensure_item_catalog_key(item)): item
        for item in MenuItem.objects.all()
    }
    seps_n = apply_all_separator_layouts(key_to_item)

    # Корневые modules/* → секция «Модули» (уникальный order + якорь разделителя).
    from .migration_utils import ensure_modules_section_layout

    ensure_modules_section_layout()
    return {'items': items_n, 'separators': seps_n}


def resolve_before_catalog_key_from_order(
    before_order: int,
    root_items: Optional[list[MenuItem]] = None,
) -> Optional[str]:
    """Подобрать catalog_key корневого пункта с order >= before_order (как в клиенте)."""
    if root_items is None:
        root_items = list(
            MenuItem.objects.filter(parent__isnull=True).order_by('order', 'name')
        )
    for item in root_items:
        order = item.order if item.order is not None else 0
        if order >= before_order:
            return item.catalog_key or ensure_item_catalog_key(item)
    return None


def delete_seed_catalog(*, keep_admin: bool = True) -> tuple[int, int]:
    """
    Удалить seed-пункты и seed-разделители. Layout-таблицы не трогает.
    Админские (catalog_key admin::*) сохраняются при keep_admin=True.
    """
    items_qs = MenuItem.objects.all()
    seps_qs = MenuSeparator.objects.all()
    if keep_admin:
        items_qs = items_qs.exclude(catalog_key__startswith='admin::')
        seps_qs = seps_qs.exclude(catalog_key__startswith='admin::')
    items_n, _ = items_qs.delete()
    seps_n, _ = seps_qs.delete()
    return items_n, seps_n


def cleanup_orphan_layouts() -> dict:
    """Удалить placement/layout без соответствующего пункта/разделителя в каталоге."""
    item_keys = set(MenuItem.objects.exclude(catalog_key__isnull=True).values_list('catalog_key', flat=True))
    item_keys |= set(MenuItem.objects.filter(catalog_key='').values_list('catalog_key', flat=True))
    # пересчитать после ensure — для orphan cleanup достаточно существующих ключей
    item_keys = set(
        k for k in MenuItem.objects.values_list('catalog_key', flat=True) if k
    )
    sep_keys = set(
        k for k in MenuSeparator.objects.values_list('catalog_key', flat=True) if k
    )

    # Не удаляем layout для ключей, которых временно нет (между wipe и populate).
    # Вызывать только после полного populate.
    orphan_placements = MenuLayoutPlacement.objects.exclude(catalog_key__in=item_keys)
    # Сохраняем admin layout даже если пункт ещё не создан — не чистим admin::
    orphan_placements = orphan_placements.exclude(catalog_key__startswith='admin::')
    p_n, _ = orphan_placements.delete()

    orphan_sep = MenuSeparatorLayout.objects.exclude(catalog_key__in=sep_keys)
    orphan_sep = orphan_sep.exclude(catalog_key__startswith='admin::')
    s_n, _ = orphan_sep.delete()
    return {'placements': p_n, 'separator_layouts': s_n}


def is_admin_item(item: MenuItem) -> bool:
    return is_admin_catalog_key(item.catalog_key)
