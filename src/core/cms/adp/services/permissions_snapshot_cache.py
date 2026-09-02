"""Кэш сериализованного snapshot прав пользователя (Django cache)."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()
from django.core.cache import cache

from src.core.cms.adp.serializers import UserPermissionsSerializer
from src.core.cms.adp.services.permissions import PermissionService

logger = logging.getLogger('core.cms.adp.permissions')

_SNAPSHOT_PREFIX = 'perms_snapshot:'
_VERSION_PREFIX = 'perms_snapshot:ver:'
_GLOBAL_VERSION_KEY = 'perms_snapshot:global_ver'


def get_permissions_snapshot_ttl() -> int:
    return max(0, int(getattr(settings, 'PERMISSIONS_SNAPSHOT_CACHE_TTL', 60) or 0))


def _version_key(user_id: int) -> str:
    return f'{_VERSION_PREFIX}{user_id}'


def _snapshot_key(user_id: int, organization_id: int | None = None) -> str:
    global_ver = int(cache.get(_GLOBAL_VERSION_KEY, 0) or 0)
    user_ver = int(cache.get(_version_key(user_id), 0) or 0)
    org_part = f'o{organization_id}' if organization_id is not None else 'o0'
    return f'{_SNAPSHOT_PREFIX}g{global_ver}:u{user_id}:v{user_ver}:{org_part}'


def invalidate_user_permissions_snapshot(user_id: int | None) -> None:
    if user_id is None:
        return
    key = _version_key(int(user_id))
    cache.set(key, int(cache.get(key, 0) or 0) + 1, timeout=None)


def invalidate_all_permissions_snapshots() -> None:
    cache.set(
        _GLOBAL_VERSION_KEY,
        int(cache.get(_GLOBAL_VERSION_KEY, 0) or 0) + 1,
        timeout=None,
    )


def invalidate_policy_access_caches() -> None:
    """Сброс меню и UX-snapshot прав после CRUD Policy."""
    from src.core.cms.adp.menu.menu_cache import invalidate_user_menu_cache

    invalidate_user_menu_cache()
    invalidate_all_permissions_snapshots()


def get_user_permissions_payload(
    user: User,
    organization_id: int | None = None,
) -> dict:
    """Сериализованные права; кэш в Django cache с TTL (ключ включает org)."""
    if user is None or not getattr(user, 'pk', None):
        return _build_payload(user, organization_id)

    from src.core.cms.adp.dev_tools.preview import get_active_preview

    if get_active_preview() is not None:
        return _build_payload(user, organization_id)

    ttl = get_permissions_snapshot_ttl()
    if ttl <= 0:
        return _build_payload(user, organization_id)

    cache_key = _snapshot_key(user.pk, organization_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _build_payload(user, organization_id)
    cache.set(cache_key, payload, timeout=ttl)
    return payload


def _build_payload(user: User, organization_id: int | None = None) -> dict:
    permissions = PermissionService.get_user_permissions(
        user,
        organization_id=organization_id,
    )
    return UserPermissionsSerializer(permissions).data
