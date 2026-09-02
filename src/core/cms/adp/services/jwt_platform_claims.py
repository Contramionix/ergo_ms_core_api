"""Платформенные флаги входа в JWT — единый источник для процесса модуля.

Ядро кладёт ``is_admin`` при логине, refresh и scoped-перевыпуске.
Процесс модуля читает флаг с principal и не ходит в свою ``cms_adp_*``.
"""

from __future__ import annotations

from typing import Any

PLATFORM_AUTH_CLAIM_KEYS = (
    'is_admin',
    'is_superuser',
    'is_staff',
    'username',
    'user_public_id',
)


def build_platform_auth_claims(user) -> dict[str, Any]:
    """Реальная роль на момент выдачи токена, без preview «как пользователь»."""
    from src.core.cms.adp.services.permissions import PermissionService

    public_id = getattr(user, 'public_id', None)
    if hasattr(user, 'get_username'):
        username = user.get_username() or ''
    else:
        username = str(getattr(user, 'username', '') or '')
    return {
        'is_admin': bool(PermissionService._is_global_admin(user, honor_preview=False)),
        'is_superuser': bool(getattr(user, 'is_superuser', False)),
        'is_staff': bool(getattr(user, 'is_staff', False)),
        'username': username,
        'user_public_id': str(public_id) if public_id else '',
    }


def attach_platform_auth_claims(token, user) -> None:
    """Записывает платформенные флаги в access или refresh."""
    claims = build_platform_auth_claims(user)
    for key, value in claims.items():
        if key == 'user_public_id' and not value:
            continue
        token[key] = value


def copy_platform_auth_claims(source, dest) -> None:
    """Копирует уже выданные флаги (refresh → access), если User недоступен."""
    for key in PLATFORM_AUTH_CLAIM_KEYS:
        try:
            present = key in source
        except TypeError:
            present = False
        if not present:
            continue
        value = source[key]
        if key == 'user_public_id' and not value:
            continue
        if value is None:
            continue
        dest[key] = value
