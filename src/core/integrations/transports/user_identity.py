"""Идентификаторы пользователя для HTTP-моста и Redis EventBus.

ORM-``user`` по сети не едет: в payload остаются ``user_id`` и ``user_public_id``.
"""

from __future__ import annotations

from typing import Any

_USER_KEYS = frozenset({'user', 'user_id', 'user_public_id'})


def is_user_like(value: Any) -> bool:
    if value is None or isinstance(value, (str, bytes, int, float, bool, dict, list, tuple)):
        return False
    if getattr(value, 'pk', None) is None and getattr(value, 'public_id', None) is None:
        return False
    return hasattr(value, 'is_authenticated')


def user_ids_from_value(user: Any) -> tuple[Any, str | None]:
    """pk и public_id из user-like объекта. pk может быть не int."""
    user_id = getattr(user, 'pk', None)
    public_id = getattr(user, 'public_id', None)
    user_public_id = str(public_id) if public_id else None
    return user_id, user_public_id


def apply_user_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Копия mapping: user-like → ``user_id`` / ``user_public_id``, ключ ``user`` убирается."""
    result: dict[str, Any] = {}
    user = payload.get('user')
    user_id = payload.get('user_id')
    user_public_id = payload.get('user_public_id')
    if is_user_like(user):
        extracted_id, extracted_public = user_ids_from_value(user)
        if user_id is None:
            user_id = extracted_id
        if not user_public_id:
            user_public_id = extracted_public
    if user_id is not None:
        try:
            result['user_id'] = int(user_id)
        except (TypeError, ValueError):
            result['user_id'] = user_id
    if user_public_id:
        result['user_public_id'] = str(user_public_id)
    for key, value in payload.items():
        if key in _USER_KEYS:
            continue
        result[str(key)] = value
    return result
