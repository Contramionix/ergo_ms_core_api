"""Синтетические *_view права роли «Пользователь» без групп."""

from types import SimpleNamespace

from src.config.security_profile_runtime import adp_default_view_grants


def collect_default_view_pairs(user_role, *, default_role_name: str) -> set[tuple[str, str]]:
    if not user_role or not getattr(user_role, 'role', None):
        return set()
    if user_role.role.name != default_role_name:
        return set()
    has_groups = any(
        getattr(group, 'is_active', True)
        for group in user_role.role_groups.all()
    )
    if has_groups:
        return set()
    from src.core.cms.adp.services.permission_catalog import get_view_permission_pairs

    pairs = get_view_permission_pairs()
    if adp_default_view_grants() == 'granted':
        return pairs
    from src.core.cms.adp.services.permissions import PermissionService

    return {
        (module_name, permission_key)
        for module_name, permission_key in pairs
        if not PermissionService._api_deny_covers_module(user_role, [], module_name)
    }


def append_default_view_permissions(
    user_role,
    module_permissions: list,
    *,
    default_role_name: str,
) -> list:
    pairs = collect_default_view_pairs(user_role, default_role_name=default_role_name)
    if not pairs:
        return module_permissions
    from src.core.cms.adp.services.permission_catalog import get_all_permission_keys

    labels = get_all_permission_keys()
    existing = {
        (getattr(perm, 'module_name', None), getattr(perm, 'permission_key', None))
        for perm in module_permissions
    }
    for module_name, permission_key in pairs:
        if (module_name, permission_key) in existing:
            continue
        module_permissions.append(
            SimpleNamespace(
                id=None,
                module_name=module_name,
                permission_key=permission_key,
                permission_name=labels.get(permission_key) or permission_key,
                description='',
                role_group_id=None,
                role_group=None,
                is_granted=True,
                granted_via='default_view',
                created_at=None,
                updated_at=None,
            )
        )
    return module_permissions
