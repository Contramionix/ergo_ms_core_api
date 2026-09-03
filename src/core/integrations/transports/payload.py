"""JSON-поля для HTTP-моста и Redis EventBus.

Локальный вызов может оставить ORM. По сети уходит только то, что здесь
приведено к примитивам. На приёме dict пункта меню снова становится
объектом с теми же атрибутами, что читают обработчики.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

from .user_identity import apply_user_ids, resolve_incoming_user


def jsonable_menu_item(item: Any) -> dict[str, Any] | None:
    """Плоский пункт меню: route_name, name, module_source и родитель."""
    if item is None:
        return None
    if isinstance(item, dict):
        return dict(item)
    parent = getattr(item, 'parent', None)
    return {
        'route_name': getattr(item, 'route_name', None),
        'name': getattr(item, 'name', None),
        'module_source': getattr(item, 'module_source', None) or '',
        'parent_name': getattr(parent, 'name', None) if parent is not None else None,
        'parent_route_name': (
            getattr(parent, 'route_name', None) if parent is not None else None
        ),
    }


def menu_item_from_payload(raw: Any) -> Any:
    """Dict с Redis/HTTP → объект с атрибутами пункта меню."""
    if raw is None or not isinstance(raw, dict):
        return raw
    parent = None
    parent_name = raw.get('parent_name')
    parent_route = raw.get('parent_route_name')
    if parent_name or parent_route:
        parent = SimpleNamespace(name=parent_name, route_name=parent_route)
    return SimpleNamespace(
        route_name=raw.get('route_name'),
        name=raw.get('name'),
        module_source=raw.get('module_source') or '',
        parent=parent,
    )


def jsonable_notification(notification: Any) -> dict[str, Any] | None:
    """Поля письма без ORM-модели."""
    if notification is None:
        return None
    if isinstance(notification, dict):
        return dict(notification)
    public_id = getattr(notification, 'public_id', None)
    recipient = getattr(notification, 'recipient', None)
    recipient_id = getattr(recipient, 'pk', None) if recipient is not None else None
    return {
        'id': getattr(notification, 'pk', None),
        'public_id': str(public_id) if public_id else '',
        'title': getattr(notification, 'title', None) or '',
        'body': getattr(notification, 'body', None) or '',
        'level': getattr(notification, 'level', None) or '',
        'icon': getattr(notification, 'icon', None) or '',
        'source_module': getattr(notification, 'source_module', None) or '',
        'event_key': getattr(notification, 'event_key', None) or '',
        'link_url': getattr(notification, 'link_url', None) or '',
        'route': getattr(notification, 'route', None),
        'meta': getattr(notification, 'meta', None) or {},
        'recipient_id': recipient_id,
    }


def prepare_outgoing_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Сеть: item и notification → dict, user → идентификаторы."""
    outgoing = dict(kwargs)
    item = outgoing.get('item')
    if item is not None and not isinstance(item, dict):
        outgoing['item'] = jsonable_menu_item(item)
    notification = outgoing.get('notification')
    if notification is not None and not isinstance(notification, dict):
        outgoing['notification'] = jsonable_notification(notification)
    return apply_user_ids(outgoing)


def prepare_incoming_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """Восстанавливает user и item после JSON, не подменяя уже живой ORM."""
    result = dict(payload)
    item = result.get('item')
    if isinstance(item, dict):
        result['item'] = menu_item_from_payload(item)
    notification = result.get('notification')
    if isinstance(notification, dict):
        result['notification'] = SimpleNamespace(**notification)
    result['user'] = resolve_incoming_user(
        user=result.get('user'),
        user_id=result.get('user_id'),
        user_public_id=result.get('user_public_id'),
    )
    return result


def jsonable_value(value: Any) -> Any:
    """Примитив для Redis; UUID — строка. Иначе TypeError."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [jsonable_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable_value(item) for key, item in value.items()}
    raise TypeError(
        f'Bridge payload: значение типа {type(value).__name__} нельзя сериализовать'
    )
