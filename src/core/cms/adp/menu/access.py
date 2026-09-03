# -*- coding: utf-8 -*-
"""Проверка видимости пункта и разделителя меню для пользователя."""

from src.core.cms.adp.services.permissions import PermissionService
from src.core.integrations import bridge
from src.core.integrations.module_contracts import (
    MENU_CAN_SEE_ITEM,
    MENU_PREPARE_VISIBILITY,
)
from src.core.integrations.transports.user_identity import bridge_user_kwargs

# Синтетический хвост: deny `/module/**` скрывает вложенные пути модуля в меню.
# Корень `/module` проверяется отдельно — шаблон `/**` его тоже закрывает.
_MODULE_DENY_PROBE_SUFFIX = '/.__menu_access__'


def _collect_route_overrides(user, session_claims=None) -> dict[str, bool]:
    """Один проход bridge.prepare_visibility вместо emit_first на каждый пункт."""
    overrides: dict[str, bool] = {}
    for result in bridge.emit(
        MENU_PREPARE_VISIBILITY,
        **bridge_user_kwargs(user, **dict(session_claims or {})),
    ):
        if not isinstance(result, dict):
            continue
        for route_name, visible in result.items():
            if route_name and visible is not None:
                overrides[str(route_name)] = bool(visible)
    return overrides


class MenuAccessChecker:
    """Контекст проверки прав на пункты и разделители меню за один запрос."""

    def __init__(self, user, session_claims=None):
        self.user = user
        self.session_claims = dict(session_claims or {})
        self._is_admin = PermissionService.is_admin(user)
        self._route_overrides = _collect_route_overrides(user, self.session_claims)
        self._user_role = None
        self._user_role_loaded = False
        self._route_name_to_path = None
        self._module_url_prefixes = None
        self._denied_modules = None

    def _load_user_role(self):
        if not self._user_role_loaded:
            self._user_role = PermissionService.get_user_role(self.user)
            self._user_role_loaded = True
        return self._user_role

    def _ensure_route_maps(self) -> None:
        if self._route_name_to_path is not None:
            return
        from src.core.cms.client_routes_cache import (
            get_client_route_name_index,
            get_module_url_prefixes,
        )

        self._route_name_to_path = get_client_route_name_index()
        self._module_url_prefixes = get_module_url_prefixes()

    def _passes_role_acl(self, obj) -> bool:
        """is_admin_only / allowed_roles / allowed_role_groups (пустые M2M = всем)."""
        if self._is_admin:
            return True

        user_role = self._load_user_role()

        if getattr(obj, 'is_admin_only', False):
            if not user_role or user_role.role.role_type != 'admin':
                return False

        allowed_roles = list(obj.allowed_roles.all())
        if allowed_roles:
            if not user_role or not any(role.id == user_role.role_id for role in allowed_roles):
                return False

        allowed_groups = list(obj.allowed_role_groups.all())
        if allowed_groups:
            if not user_role:
                return False
            user_group_ids = {group.id for group in user_role.role_groups.all()}
            if not any(group.id in user_group_ids for group in allowed_groups):
                return False

        return True

    def _is_module_denied_by_url_policy(self, module_name: str) -> bool:
        """True, если URL-политика запрещает модуль целиком (шаблон prefix/**)."""
        if self._is_admin or not module_name or module_name in ('core', 'cms'):
            return False

        if self._denied_modules is None:
            self._denied_modules = {}

        if module_name in self._denied_modules:
            return self._denied_modules[module_name]

        self._ensure_route_maps()
        prefixes = self._module_url_prefixes.get(module_name) or []
        if not prefixes:
            # fallback: snake_case → kebab-case
            prefixes = [f'/{module_name.replace("_", "-")}']

        denied = False
        for prefix in prefixes:
            probe = f'{prefix.rstrip("/")}{_MODULE_DENY_PROBE_SUFFIX}'
            if not PermissionService.check_url_access(self.user, probe):
                denied = True
                break
            if not PermissionService.check_url_access(self.user, prefix):
                denied = True
                break

        self._denied_modules[module_name] = denied
        return denied

    def _is_route_denied_by_url_policy(self, route_name: str | None) -> bool:
        if self._is_admin or not route_name:
            return False

        self._ensure_route_maps()
        path = self._route_name_to_path.get(route_name)
        if not path:
            return False
        return not PermissionService.check_url_access(self.user, path)

    def can_see(self, item) -> bool:
        from src.core.utils.module_registry import (
            is_module_disabled,
            top_level_module_from_menu_source,
        )

        module_source = getattr(item, 'module_source', '') or ''
        top_level = top_level_module_from_menu_source(module_source)
        if top_level and is_module_disabled(top_level):
            return False

        route_name = getattr(item, 'route_name', None)
        if route_name and route_name in self._route_overrides:
            return self._route_overrides[route_name]

        menu_override = bridge.emit_first(
            MENU_CAN_SEE_ITEM,
            item=item,
            **bridge_user_kwargs(self.user, **self.session_claims),
        )
        if menu_override is not None:
            return bool(menu_override)

        if not self._passes_role_acl(item):
            return False

        if top_level and self._is_module_denied_by_url_policy(top_level):
            return False

        if self._is_route_denied_by_url_policy(route_name):
            return False

        return True

    def can_see_separator(self, separator) -> bool:
        from src.core.utils.module_registry import (
            is_module_disabled,
            top_level_module_from_menu_source,
        )

        module_source = getattr(separator, 'module_source', '') or ''
        top_level = top_level_module_from_menu_source(module_source)
        if top_level and is_module_disabled(top_level):
            return False

        if not self._passes_role_acl(separator):
            return False

        if top_level and self._is_module_denied_by_url_policy(top_level):
            return False

        return True


def user_can_see_menu_item(item, user, *, checker: MenuAccessChecker | None = None) -> bool:
    """
    is_admin_only, allowed_roles, allowed_role_groups.
    Ограничение по правам модуля — через M2M allowed_role_groups на пункте.
    URL deny-политики скрывают пункты по route_name / module_source.
    Модули могут переопределить видимость через событие menu.can_see_item.
    """
    if checker is not None:
        return checker.can_see(item)
    return MenuAccessChecker(user).can_see(item)


def user_can_see_menu_separator(
    separator, user, *, checker: MenuAccessChecker | None = None
) -> bool:
    """Та же семантика ACL, что у пунктов: роли / ролевые группы / is_admin_only / URL."""
    if checker is not None:
        return checker.can_see_separator(separator)
    return MenuAccessChecker(user).can_see_separator(separator)
