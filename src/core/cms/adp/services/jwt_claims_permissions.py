"""Права ADP с процесса модуля: JWT-principal, роль и политики — на ядре."""

from __future__ import annotations

from django.conf import settings

from src.core.cms.adp.services.jwt_claims_cache import extra_fingerprint, get_adp, set_adp
from src.core.integrations import bridge
from src.core.integrations.module_contracts import (
    ADP_CHECK_API_ACCESS,
    ADP_CHECK_MODULE_PERMISSION,
    ADP_IS_ADMIN,
)


def jwt_claims_on_module() -> bool:
    """Процесс модуля спрашивает роль и права на ядре, не в своей cms_adp_*.

    ``MODULE_AUTH_MODE=jwt_claims`` — всегда. ``orm`` — тоже, если задан
    ``BRIDGE_CORE_URL``: иначе модуль читает пустые таблицы и все пользователи
    выглядят одинаково. Без URL ядра оставляем локальный ORM, чтобы не
    зациклить ``adp.check_*`` на свой же процесс.
    """
    role = (getattr(settings, 'ERGO_PROCESS_ROLE', '') or '').strip().lower()
    if not role.startswith('module:'):
        return False
    mode = (getattr(settings, 'MODULE_AUTH_MODE', 'orm') or 'orm').strip().lower()
    if mode == 'jwt_claims':
        return True
    return bool((getattr(settings, 'BRIDGE_CORE_URL', '') or '').strip())


def user_pk(user) -> int | None:
    raw = getattr(user, 'pk', None)
    if raw is None:
        raw = getattr(user, 'id', None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def user_public_id_str(user) -> str:
    pid = getattr(user, 'public_id', None)
    return str(pid) if pid else ''


def _remote_kwargs(user) -> dict:
    return {
        'user_id': user_pk(user),
        'user_public_id': user_public_id_str(user),
    }


def remote_is_admin(user) -> bool:
    kwargs = _remote_kwargs(user)
    if not kwargs['user_id'] and not kwargs['user_public_id']:
        return bool(
            getattr(user, 'is_admin', False)
            or getattr(user, 'is_superuser', False)
        )
    cached = get_adp('admin', kwargs['user_id'], kwargs['user_public_id'])
    if cached is not None:
        return bool(cached)
    result = bridge.call(ADP_IS_ADMIN, default=None, **kwargs)
    if result is None:
        # Мост недоступен или пользователь на ядре не найден: не кэшируем «не админ».
        return bool(
            getattr(user, 'is_admin', False)
            or getattr(user, 'is_superuser', False)
        )
    value = bool(result)
    set_adp('admin', kwargs['user_id'], kwargs['user_public_id'], value)
    return value


def remote_check_api_access(user, api_path: str) -> bool:
    kwargs = _remote_kwargs(user)
    path = api_path or ''
    cached = get_adp('api', kwargs['user_id'], kwargs['user_public_id'], path)
    if cached is not None:
        return bool(cached)
    result = bridge.call(
        ADP_CHECK_API_ACCESS,
        default=None,
        api_path=path,
        **kwargs,
    )
    if result is None:
        return bool(getattr(user, 'is_authenticated', False))
    value = bool(result)
    set_adp('api', kwargs['user_id'], kwargs['user_public_id'], value, path)
    return value


def remote_check_module_permission(
    user,
    module_name: str,
    permission_key: str,
    extra: dict,
) -> bool:
    kwargs = _remote_kwargs(user)
    extra = extra or {}
    extra_key = f'{module_name}:{permission_key}:{extra_fingerprint(extra)}'
    cached = get_adp('perm', kwargs['user_id'], kwargs['user_public_id'], extra_key)
    if cached is not None:
        return bool(cached)
    result = bridge.call(
        ADP_CHECK_MODULE_PERMISSION,
        default=None,
        module_name=module_name,
        permission_key=permission_key,
        extra=extra,
        **kwargs,
    )
    if result is None:
        return bool(getattr(user, 'is_authenticated', False))
    value = bool(result)
    set_adp('perm', kwargs['user_id'], kwargs['user_public_id'], value, extra_key)
    return value
