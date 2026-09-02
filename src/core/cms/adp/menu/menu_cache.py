"""Кэш собранного меню пользователя (Django cache + version bump)."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

from src.core.cms.adp.menu.access import MenuAccessChecker
from src.core.cms.adp.menu.models import MenuSeparator
from src.core.cms.adp.menu.serializers import MenuSeparatorSerializer
from src.core.cms.adp.menu.user_menu_builder import build_user_menu_items
from src.core.cms.adp.middleware.permission_request_cache import get_request_permission_cache
from src.core.cms.adp.services.permissions import PermissionService

logger = logging.getLogger('core.cms.adp.menu')

MENU_VERSION_KEY = 'menu:global_version'
MENU_VERSION_TIMEOUT = None


def get_menu_cache_ttl() -> int:
    return max(0, int(getattr(settings, 'MENU_CACHE_TTL', 0) or 0))


def get_menu_cache_version() -> int:
    version = cache.get(MENU_VERSION_KEY)
    if version is None:
        cache.set(MENU_VERSION_KEY, 1, timeout=MENU_VERSION_TIMEOUT)
        return 1
    return int(version)


def bump_menu_cache_version() -> int:
    """Инвалидация меню у всех пользователей после изменения структуры меню."""
    version = get_menu_cache_version() + 1
    cache.set(MENU_VERSION_KEY, version, timeout=MENU_VERSION_TIMEOUT)
    logger.debug('menu cache version bumped to %s', version)
    return version


def invalidate_user_menu_cache() -> None:
    bump_menu_cache_version()


def _role_groups_key(user_role) -> str:
    if user_role is None:
        return 'none'
    group_ids = sorted(g.id for g in user_role.role_groups.all())
    return ','.join(str(group_id) for group_id in group_ids) or 'none'


def _menu_cache_key(user, organization_id=None) -> str:
    version = get_menu_cache_version()
    req_cache = get_request_permission_cache()
    frag_key = f'menu_key_frag:{user.pk}'
    if frag_key in req_cache:
        role_id, groups_key, is_admin = req_cache[frag_key]
    else:
        user_role = PermissionService.get_user_role(user)
        is_admin = 1 if PermissionService.is_admin(user) else 0
        role_id = user_role.role_id if user_role else 'none'
        groups_key = _role_groups_key(user_role)
        req_cache[frag_key] = (role_id, groups_key, is_admin)
    org_part = f'o{organization_id}' if organization_id is not None else 'o0'
    return (
        f'menu:v{version}:u{user.pk}:r{role_id}:g{groups_key}:a{is_admin}:{org_part}'
    )


def get_active_menu_separators(user=None, organization_id=None) -> list[dict]:
    separators = (
        MenuSeparator.objects.filter(is_active=True)
        .prefetch_related('allowed_roles', 'allowed_role_groups')
        .order_by('before_order')
    )
    if user is not None:
        checker = MenuAccessChecker(user, organization_id=organization_id)
        separators = [sep for sep in separators if checker.can_see_separator(sep)]
    return MenuSeparatorSerializer(separators, many=True).data


def _build_user_menu_payload(user, organization_id=None) -> dict:
    return {
        'menu_items': build_user_menu_items(user, organization_id=organization_id),
        'separators': get_active_menu_separators(user, organization_id=organization_id),
    }


def get_user_menu_payload(user, organization_id=None) -> dict:
    """Меню пользователя: из кэша или сборка с записью в кэш."""
    ttl = get_menu_cache_ttl()
    if ttl <= 0:
        return _build_user_menu_payload(user, organization_id=organization_id)

    cache_key = _menu_cache_key(user, organization_id=organization_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _build_user_menu_payload(user, organization_id=organization_id)
    cache.set(cache_key, payload, timeout=ttl)
    return payload
