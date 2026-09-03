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


def bridge_user_kwargs(user: Any, **extra: Any) -> dict[str, Any]:
    """Локально оставляет ``user=``, по сети ``apply_user_ids`` оставит только id."""
    payload: dict[str, Any] = dict(extra)
    if user is None:
        return payload
    user_id, user_public_id = user_ids_from_value(user)
    if is_user_like(user):
        payload['user'] = user
    if user_id is not None and 'user_id' not in payload:
        payload['user_id'] = user_id
    if user_public_id and 'user_public_id' not in payload:
        payload['user_public_id'] = user_public_id
    return payload


def resolve_incoming_user(
    *,
    user: Any = None,
    user_id: Any = None,
    user_public_id: Any = None,
) -> Any:
    """ORM-user если есть в этом процессе, иначе объект только с id."""
    if is_user_like(user):
        return user
    resolved = None
    if user_id is not None or user_public_id:
        try:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            if user_public_id:
                resolved = User.objects.filter(public_id=user_public_id).first()
            if resolved is None and user_id is not None:
                resolved = User.objects.filter(pk=user_id).first()
        except Exception:
            resolved = None
    if resolved is not None:
        return resolved
    if user_id is None and not user_public_id:
        return user
    from types import SimpleNamespace

    try:
        pk = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        pk = user_id
    return SimpleNamespace(
        pk=pk,
        id=pk,
        public_id=user_public_id or None,
        is_authenticated=True,
    )


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
