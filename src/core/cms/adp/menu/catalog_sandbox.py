# -*- coding: utf-8 -*-
"""
Песочница MenuItem/MenuSeparator для выгрузки каталога без записи в БД.

populate-функции получают apps.get_model как в миграции; записи остаются в памяти.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from .catalog_keys import build_item_catalog_key, build_separator_catalog_key


class _Meta:
    def __init__(self, fields: tuple[str, ...]):
        self._fields = set(fields)

    def get_field(self, field_name: str):
        if field_name not in self._fields:
            raise Exception(f'no field {field_name}')
        return object()


class _Related:
    def set(self, *_args, **_kwargs):
        return None

    def values_list(self, *_args, **_kwargs):
        return []


class SandboxInstance:
    def __init__(self, store: 'MenuCatalogStore', model_name: str, pk: int, **fields):
        self._store = store
        self._model_name = model_name
        object.__setattr__(self, 'pk', pk)
        object.__setattr__(self, 'id', pk)
        object.__setattr__(self, 'allowed_roles', _Related())
        object.__setattr__(self, 'allowed_role_groups', _Related())
        for key, value in fields.items():
            object.__setattr__(self, key, value)
        if not getattr(self, 'public_id', None):
            object.__setattr__(self, 'public_id', uuid4())

    def __setattr__(self, name, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, name, value)

    @property
    def parent_id(self):
        parent = getattr(self, 'parent', None)
        return getattr(parent, 'pk', None) if parent is not None else None

    def save(self, **_kwargs):
        self._store.save_instance(self)


class SandboxQuerySet:
    def __init__(self, store: 'MenuCatalogStore', model_name: str, items: list[SandboxInstance]):
        self._store = store
        self._model_name = model_name
        self._items = list(items)

    def _clone(self, items):
        return SandboxQuerySet(self._store, self._model_name, items)

    def filter(self, **kwargs):
        return self._clone([item for item in self._items if _matches(item, kwargs)])

    def exclude(self, **kwargs):
        return self._clone([item for item in self._items if not _matches(item, kwargs)])

    def first(self):
        return self._items[0] if self._items else None

    def exists(self):
        return bool(self._items)

    def count(self):
        return len(self._items)

    def all(self):
        return self._clone(self._items)

    def distinct(self):
        return self._clone(self._items)

    def order_by(self, *fields):
        def key(item):
            values = []
            for field in fields:
                desc = field.startswith('-')
                name = field[1:] if desc else field
                value = getattr(item, name, None)
                values.append((value is None, value if not desc else _invert(value)))
            return tuple(values)

        return self._clone(sorted(self._items, key=key))

    def aggregate(self, *args, **kwargs):
        result = {}
        for expr in args:
            alias = getattr(expr, 'default_alias', None) or 'order__max'
            field_name = alias.rsplit('__', 1)[0]
            values = [
                getattr(item, field_name, None)
                for item in self._items
                if getattr(item, field_name, None) is not None
            ]
            result[alias] = max(values) if values else None
        for alias, expr in kwargs.items():
            field_name = getattr(expr, 'default_alias', alias).rsplit('__', 1)[0]
            values = [
                getattr(item, field_name, None)
                for item in self._items
                if getattr(item, field_name, None) is not None
            ]
            result[alias] = max(values) if values else None
        return result

    def update(self, **kwargs):
        for item in self._items:
            for key, value in kwargs.items():
                setattr(item, key, value)
        return len(self._items)

    def delete(self):
        count = 0
        for item in list(self._items):
            if self._store.delete_instance(item):
                count += 1
        return count, {self._model_name: count}

    def create(self, **kwargs):
        return self._store.create(self._model_name, **kwargs)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class SandboxManager:
    def __init__(self, store: 'MenuCatalogStore', model_name: str):
        self._store = store
        self._model_name = model_name

    def _qs(self):
        return SandboxQuerySet(self._store, self._model_name, self._store.rows(self._model_name))

    def filter(self, **kwargs):
        return self._qs().filter(**kwargs)

    def exclude(self, **kwargs):
        return self._qs().exclude(**kwargs)

    def all(self):
        return self._qs()

    def create(self, **kwargs):
        return self._store.create(self._model_name, **kwargs)

    def first(self):
        return self._qs().first()

    def count(self):
        return self._qs().count()


class SandboxModel:
    def __init__(self, store: 'MenuCatalogStore', model_name: str, fields: tuple[str, ...]):
        self.objects = SandboxManager(store, model_name)
        self._meta = _Meta(fields)
        self.__name__ = model_name
        for field in fields:
            setattr(self, field, field)


class _NoOpQuerySet:
    """Пустая выборка: смешанные data-миграции не падают на чужих моделях."""

    def filter(self, **_kwargs):
        return self

    def exclude(self, **_kwargs):
        return self

    def all(self):
        return self

    def distinct(self):
        return self

    def order_by(self, *_args):
        return self

    def select_related(self, *_args, **_kwargs):
        return self

    def prefetch_related(self, *_args, **_kwargs):
        return self

    def first(self):
        return None

    def last(self):
        return None

    def exists(self):
        return False

    def count(self):
        return 0

    def delete(self):
        return 0, {}

    def update(self, **_kwargs):
        return 0

    def aggregate(self, *args, **kwargs):
        result = {}
        for expr in args:
            alias = getattr(expr, 'default_alias', None) or 'order__max'
            result[alias] = None
        for alias in kwargs:
            result[alias] = None
        return result

    def create(self, **kwargs):
        return SimpleNamespace(**kwargs, save=lambda **_k: None, pk=None)

    def get_or_create(self, defaults=None, **kwargs):
        data = dict(defaults or {})
        data.update(kwargs)
        return SimpleNamespace(**data, save=lambda **_k: None, pk=None), True

    def update_or_create(self, defaults=None, **kwargs):
        return self.get_or_create(defaults=defaults, **kwargs)

    def values_list(self, *_args, **_kwargs):
        return []

    def values(self, *_args, **_kwargs):
        return []

    def get(self, **_kwargs):
        raise Exception('noop get')

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return False


class _NoOpManager:
    def filter(self, **_kwargs):
        return _NoOpQuerySet()

    def exclude(self, **_kwargs):
        return _NoOpQuerySet()

    def all(self):
        return _NoOpQuerySet()

    def none(self):
        return _NoOpQuerySet()

    def create(self, **kwargs):
        return SimpleNamespace(**kwargs, save=lambda **_k: None, pk=None)

    def get_or_create(self, defaults=None, **kwargs):
        return _NoOpQuerySet().get_or_create(defaults=defaults, **kwargs)

    def update_or_create(self, defaults=None, **kwargs):
        return _NoOpQuerySet().update_or_create(defaults=defaults, **kwargs)

    def order_by(self, *_args):
        return _NoOpQuerySet()

    def __getattr__(self, _name):
        return lambda *_a, **_k: _NoOpQuerySet()


class _NoOpModel:
    objects = _NoOpManager()
    _meta = _Meta(())


class MenuCatalogStore:
    """In-memory cms_adp menu models + apps.get_model."""

    _ITEM_FIELDS = (
        'catalog_key', 'name', 'route_name', 'icon', 'item_type', 'page',
        'external_url', 'parent', 'order', 'is_active', 'is_admin_only',
        'module_source', 'public_id',
    )
    _SEP_FIELDS = (
        'catalog_key', 'name', 'before_order', 'before_catalog_key',
        'is_active', 'is_admin_only', 'module_source', 'public_id',
    )
    _PLACEMENT_FIELDS = ('catalog_key', 'parent_catalog_key', 'order', 'is_active')
    _SEP_LAYOUT_FIELDS = (
        'catalog_key', 'name', 'before_catalog_key', 'before_order', 'is_active',
    )

    def __init__(self):
        self._rows: dict[str, list[SandboxInstance]] = {
            'MenuItem': [],
            'MenuSeparator': [],
            'MenuLayoutPlacement': [],
            'MenuSeparatorLayout': [],
        }
        self._pk = 0
        self.MenuItem = SandboxModel(self, 'MenuItem', self._ITEM_FIELDS)
        self.MenuSeparator = SandboxModel(self, 'MenuSeparator', self._SEP_FIELDS)
        self.MenuLayoutPlacement = SandboxModel(
            self, 'MenuLayoutPlacement', self._PLACEMENT_FIELDS,
        )
        self.MenuSeparatorLayout = SandboxModel(
            self, 'MenuSeparatorLayout', self._SEP_LAYOUT_FIELDS,
        )

    def as_apps(self):
        store = self

        class _Apps:
            def get_model(_self, app_label, model_name):
                if app_label == 'cms_adp' and hasattr(store, model_name):
                    return getattr(store, model_name)
                return _NoOpModel()

        return _Apps()

    def rows(self, model_name: str) -> list[SandboxInstance]:
        return list(self._rows.get(model_name, []))

    def create(self, model_name: str, **kwargs) -> SandboxInstance:
        self._pk += 1
        instance = SandboxInstance(self, model_name, self._pk, **kwargs)
        if model_name == 'MenuItem' and getattr(instance, 'order', None) is None:
            parent = getattr(instance, 'parent', None)
            siblings = [
                item for item in self._rows['MenuItem']
                if getattr(item, 'parent', None) is parent
            ]
            max_order = max(
                (getattr(item, 'order', None) or 0 for item in siblings),
                default=0,
            )
            instance.order = max_order + 10
        self._rows.setdefault(model_name, []).append(instance)
        return instance

    def save_instance(self, instance: SandboxInstance) -> None:
        bucket = self._rows.setdefault(instance._model_name, [])
        if instance not in bucket:
            bucket.append(instance)

    def delete_instance(self, instance: SandboxInstance) -> bool:
        bucket = self._rows.get(instance._model_name) or []
        if instance in bucket:
            bucket.remove(instance)
            if instance._model_name == 'MenuItem':
                children = [
                    child for child in list(self._rows['MenuItem'])
                    if getattr(child, 'parent', None) is instance
                ]
                for child in children:
                    self.delete_instance(child)
            return True
        return False

    def export(self, module_name: str, module_source: str) -> dict[str, Any]:
        items = []
        for item in self._rows['MenuItem']:
            source = getattr(item, 'module_source', None) or module_source
            if source != module_source:
                continue
            parent = getattr(item, 'parent', None)
            parent_key = getattr(parent, 'catalog_key', None) if parent is not None else None
            catalog_key = getattr(item, 'catalog_key', None) or build_item_catalog_key(
                module_source,
                item_type=getattr(item, 'item_type', None) or 'route',
                route_name=getattr(item, 'route_name', None),
                page=getattr(item, 'page', None),
                external_url=getattr(item, 'external_url', None),
                name=getattr(item, 'name', None),
                parent_catalog_key=parent_key,
            )
            items.append({
                'catalog_key': catalog_key,
                'name': getattr(item, 'name', '') or '',
                'item_type': getattr(item, 'item_type', None) or 'route',
                'route_name': getattr(item, 'route_name', None),
                'icon': getattr(item, 'icon', None),
                'page': getattr(item, 'page', None),
                'external_url': getattr(item, 'external_url', None),
                'parent_catalog_key': parent_key,
                'order': getattr(item, 'order', None),
                'is_active': bool(getattr(item, 'is_active', True)),
                'is_admin_only': bool(getattr(item, 'is_admin_only', False)),
            })

        separators = []
        for sep in self._rows['MenuSeparator']:
            source = getattr(sep, 'module_source', None) or module_source
            if source != module_source:
                continue
            name = getattr(sep, 'name', '') or ''
            catalog_key = getattr(sep, 'catalog_key', None) or build_separator_catalog_key(
                module_source, name,
            )
            separators.append({
                'catalog_key': catalog_key,
                'name': name,
                'before_order': getattr(sep, 'before_order', None),
                'before_catalog_key': getattr(sep, 'before_catalog_key', None),
                'is_active': bool(getattr(sep, 'is_active', True)),
            })

        return {
            'module': module_name,
            'module_source': module_source,
            'items': items,
            'separators': separators,
        }


def _invert(value):
    if isinstance(value, (int, float)):
        return -value
    return value


def _matches(item: SandboxInstance, kwargs: dict[str, Any]) -> bool:
    for key, expected in kwargs.items():
        if key.endswith('__isnull'):
            field = key[: -len('__isnull')]
            value = getattr(item, field, None)
            if bool(value is None) != bool(expected):
                return False
            continue
        if key.endswith('__startswith'):
            field = key[: -len('__startswith')]
            value = getattr(item, field, None) or ''
            if not str(value).startswith(str(expected or '')):
                return False
            continue
        if key.endswith('__gte'):
            field = key[: -len('__gte')]
            value = getattr(item, field, None)
            if value is None or value < expected:
                return False
            continue
        if key == 'parent':
            if getattr(item, 'parent', None) is not expected:
                return False
            continue
        if getattr(item, key, None) != expected:
            return False
    return True
