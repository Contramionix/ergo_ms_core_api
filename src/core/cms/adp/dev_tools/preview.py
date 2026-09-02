"""
Сессионный overlay прав для режима разработчика.

Не пишет роли в БД: действует только на текущий запрос админа
после PUT /dev-tools/session/.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Iterable
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.cache import cache

_preview_var: ContextVar['DevToolsPreview | None'] = ContextVar(
    'adp_dev_tools_preview',
    default=None,
)

CACHE_TTL_SECONDS = 8 * 60 * 60
_CACHE_PREFIX = 'adp:dev-tools:preview:'
_MAX_OVERRIDE_PAIRS = 400


@dataclass(frozen=True)
class DevToolsPreview:
    view_as_regular: bool = False
    as_user_public_id: str | None = None
    as_user_label: str | None = None
    role_name: str | None = None
    extra_grants: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    extra_denies: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def is_active(self) -> bool:
        return bool(
            self.view_as_regular
            or self.as_user_public_id
            or self.role_name
            or self.extra_grants
            or self.extra_denies
        )

    def to_payload(self) -> dict:
        return {
            'view_as_regular': self.view_as_regular,
            'as_user_public_id': self.as_user_public_id,
            'as_user_label': self.as_user_label,
            'role_name': self.role_name,
            'extra_grants': [
                {'module_name': module_name, 'permission_key': permission_key}
                for module_name, permission_key in self.extra_grants
            ],
            'extra_denies': [
                {'module_name': module_name, 'permission_key': permission_key}
                for module_name, permission_key in self.extra_denies
            ],
        }


class _GroupQuery:
    """Достаточно .filter(is_active=True) / .all() как у related manager."""

    def __init__(self, groups: list):
        self._groups = list(groups)

    def all(self):
        return list(self._groups)

    def filter(self, **kwargs):
        result = self._groups
        if kwargs.get('is_active') is True:
            result = [group for group in result if getattr(group, 'is_active', True)]
        return list(result)

    def __iter__(self):
        return iter(self._groups)


class PreviewUserRole:
    """Подмена UserRole без записи в БД."""

    def __init__(self, role, groups: Iterable):
        self.role = role
        self.role_id = getattr(role, 'id', None)
        self.is_active = True
        self.role_groups = _GroupQuery(list(groups))


def _cache_key(user) -> str | None:
    pk = getattr(user, 'pk', None)
    if pk is None:
        return None
    return f'{_CACHE_PREFIX}{pk}'


def _normalize_pairs(raw) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw[:_MAX_OVERRIDE_PAIRS]:
        if not isinstance(item, dict):
            continue
        module_name = str(item.get('module_name') or '').strip()
        permission_key = str(item.get('permission_key') or '').strip()
        if not module_name or not permission_key:
            continue
        pair = (module_name, permission_key)
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    return tuple(pairs)


def preview_from_payload(data: dict | None) -> DevToolsPreview:
    payload = data if isinstance(data, dict) else {}
    as_user = str(payload.get('as_user_public_id') or '').strip() or None
    if as_user:
        try:
            as_user = str(UUID(as_user))
        except (TypeError, ValueError):
            as_user = None
    role_name = str(payload.get('role_name') or '').strip() or None
    label = str(payload.get('as_user_label') or '').strip() or None
    return DevToolsPreview(
        view_as_regular=bool(payload.get('view_as_regular')),
        as_user_public_id=as_user,
        as_user_label=label,
        role_name=role_name,
        extra_grants=_normalize_pairs(payload.get('extra_grants')),
        extra_denies=_normalize_pairs(payload.get('extra_denies')),
    )


def load_preview(user) -> DevToolsPreview | None:
    key = _cache_key(user)
    if not key:
        return None
    raw = cache.get(key)
    if not isinstance(raw, dict):
        return None
    preview = preview_from_payload(raw)
    return preview if preview.is_active() else None


def save_preview(user, preview: DevToolsPreview) -> None:
    key = _cache_key(user)
    if not key:
        return
    if not preview.is_active():
        cache.delete(key)
        return
    cache.set(key, preview.to_payload(), timeout=CACHE_TTL_SECONDS)


def clear_preview(user) -> None:
    key = _cache_key(user)
    if key:
        cache.delete(key)


def get_active_preview() -> DevToolsPreview | None:
    return _preview_var.get()


def set_active_preview(preview: DevToolsPreview | None) -> Token:
    return _preview_var.set(preview)


def reset_active_preview(token: Token) -> None:
    _preview_var.reset(token)


def preview_role_is_admin(preview: DevToolsPreview | None) -> bool:
    """True, если overlay явно ставит административную роль."""
    if preview is None or not preview.role_name:
        return False
    from src.core.cms.adp.models import Role
    from src.core.cms.adp.services.permissions import PermissionService

    role = Role.objects.filter(name=preview.role_name).first()
    return bool(role and PermissionService._is_admin_role(role))


def preview_suppresses_admin() -> bool:
    preview = get_active_preview()
    if preview is None or not preview.is_active():
        return False
    if preview_role_is_admin(preview):
        return False
    return bool(
        preview.view_as_regular
        or preview.as_user_public_id
        or preview.role_name
    )


def resolve_effective_user(user):
    preview = get_active_preview()
    if preview is None or not preview.as_user_public_id:
        return user
    User = get_user_model()
    found = User.objects.filter(public_id=preview.as_user_public_id).first()
    return found or user


def try_preview_user_role(user, *, lookup: Callable) -> Any | None:
    """Вернуть подменённый UserRole или None (взять обычный lookup)."""
    preview = get_active_preview()
    if preview is None or not preview.is_active():
        return None

    from src.core.cms.adp.models import Role
    from src.core.cms.adp.services.permissions import PermissionService

    effective = resolve_effective_user(user)
    role = None
    if preview.role_name:
        role = Role.objects.filter(name=preview.role_name).first()
    elif preview.view_as_regular:
        real_role = lookup(effective)
        real_is_admin = False
        if real_role and getattr(real_role, 'role', None):
            real_is_admin = PermissionService._is_admin_role(real_role.role)
        if real_is_admin or PermissionService._is_global_admin(
            effective,
            honor_preview=False,
        ):
            role = Role.objects.filter(name=PermissionService.DEFAULT_ROLE_NAME).first()
        else:
            return real_role

    if role is not None:
        groups = list(role.role_groups.filter(is_active=True))
        return PreviewUserRole(role, groups)

    if preview.as_user_public_id and effective is not user:
        return lookup(effective)
    return None


def preview_permission_override(module_name: str, permission_key: str) -> bool | None:
    preview = get_active_preview()
    if preview is None:
        return None
    pair = (module_name, permission_key)
    if pair in preview.extra_denies:
        return False
    if pair in preview.extra_grants:
        return True
    return None


def apply_preview_module_permissions(module_permissions: list) -> list:
    preview = get_active_preview()
    if preview is None:
        return module_permissions

    deny = set(preview.extra_denies)
    result = [
        perm
        for perm in module_permissions
        if (getattr(perm, 'module_name', None), getattr(perm, 'permission_key', None))
        not in deny
    ]
    existing = {
        (getattr(perm, 'module_name', None), getattr(perm, 'permission_key', None))
        for perm in result
        if getattr(perm, 'is_granted', True)
    }
    from src.core.cms.adp.services.permission_catalog import get_all_permission_keys

    labels = get_all_permission_keys()
    for module_name, permission_key in preview.extra_grants:
        if (module_name, permission_key) in existing or (module_name, permission_key) in deny:
            continue
        result.append(
            SimpleNamespace(
                id=None,
                module_name=module_name,
                permission_key=permission_key,
                permission_name=labels.get(permission_key) or permission_key,
                description='',
                role_group_id=None,
                role_group=None,
                is_granted=True,
                granted_via='dev_tools',
                created_at=None,
                updated_at=None,
            )
        )
    return result


def permission_pairs_for_preview(user, preview: DevToolsPreview | None) -> list[dict]:
    """Выданные ключи роли/сущности без extra_grants/denies и без URL-политик."""
    if preview is None or not preview.is_active():
        return []
    if preview_role_is_admin(preview):
        return []

    active = DevToolsPreview(
        view_as_regular=preview.view_as_regular,
        as_user_public_id=preview.as_user_public_id,
        as_user_label=preview.as_user_label,
        role_name=preview.role_name,
    )
    if not active.is_active():
        return []

    token = set_active_preview(active)
    try:
        from src.core.cms.adp.services.permissions import PermissionService

        keys = PermissionService._get_granted_module_permission_keys(user)
        return [
            {'module_name': module_name, 'permission_key': permission_key}
            for module_name, permission_key in sorted(keys)
        ]
    finally:
        reset_active_preview(token)


def effective_permission_pairs_for_preview(
    user,
    preview: DevToolsPreview | None,
) -> tuple[list[dict], list[dict], bool]:
    """base, effective и флаг администратора overlay."""
    is_admin = preview_role_is_admin(preview)
    if preview is None or not preview.is_active():
        return [], [], False
    if is_admin:
        return [], [], True
    base = permission_pairs_for_preview(user, preview)
    effective = {
        (item['module_name'], item['permission_key'])
        for item in base
    }
    effective.update(preview.extra_grants)
    effective.difference_update(preview.extra_denies)
    return (
        base,
        [
            {'module_name': module_name, 'permission_key': permission_key}
            for module_name, permission_key in sorted(effective)
        ],
        False,
    )
