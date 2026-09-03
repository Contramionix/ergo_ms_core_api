"""
Сервис для проверки прав доступа пользователей к ресурсам системы.
"""
import re
from typing import List, Dict, Optional
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.translation import gettext as _

User = get_user_model()
from src.config.security_profile_runtime import adp_default_view_grants
from src.core.cms.adp.middleware.permission_request_cache import get_request_permission_cache
from src.core.cms.adp.models import Role, RoleGroup, Policy, UserRole, ModulePermission
from src.core.integrations import bridge
from src.core.cms.adp.services.jwt_claims_permissions import (
    jwt_claims_on_module,
    remote_check_api_access,
    remote_check_module_permission,
    remote_is_admin,
    user_pk,
)
from src.core.integrations.module_contracts import (
    ADP_FILTER_GRANTED_ROLE_GROUP_IDS,
    ADP_PERMISSION_CHECK,
    ADP_SESSION_SCOPED_DENIED_PERMISSIONS,
    ADP_SESSION_SCOPED_MODULE_PERMISSIONS,
)
from src.core.integrations.session_context import merge_session_scope_kwargs
from src.core.integrations.transports.user_identity import bridge_user_kwargs


class RoleAssignmentError(Exception):
    """Недопустимое изменение роли пользователя."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class PermissionService:
    """
    Сервис для работы с правами доступа пользователей.
    """
    DEFAULT_ROLE_NAME = 'Пользователь'
    DEFAULT_ROLE_DESCRIPTION = 'Роль по умолчанию для всех пользователей'
    ADMIN_ROLE_NAME = 'Администратор'
    ADMIN_ROLE_DESCRIPTION = 'Системная роль с полным доступом'
    
    @staticmethod
    def get_user_role(user: User, *, honor_preview: bool = True) -> Optional[UserRole]:
        """Получить активную роль пользователя (без побочных save на read-path)."""
        pk = user_pk(user)
        if not user or pk is None:
            return None

        if honor_preview:
            from src.core.cms.adp.dev_tools.preview import try_preview_user_role

            preview_role = try_preview_user_role(
                user,
                lookup=lambda target: PermissionService.get_user_role(
                    target,
                    honor_preview=False,
                ),
            )
            if preview_role is not None:
                return preview_role

        cache = get_request_permission_cache()
        cache_key = f'user_role:{pk}'
        if cache_key in cache:
            return cache[cache_key]

        user_role = (
            UserRole.objects
            .select_related('role')
            .prefetch_related('role_groups')
            .filter(user_id=pk, is_active=True)
            .first()
        )

        if user_role:
            cache[cache_key] = user_role
            return user_role

        cache[cache_key] = None
        return None

    @staticmethod
    def _get_or_create_role_safe(name: str, description: str, is_system: bool) -> Role:
        """
        Безопасно получает или создаёт роль.
        Обрабатывает ситуацию, когда Group с таким именем уже существует,
        но Role ещё не создана.
        """
        from django.contrib.auth.models import Group
        from django.db import connection
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            # Сначала пробуем найти существующую Role
            return Role.objects.get(name=name)
        except Role.DoesNotExist:
            pass
        
        try:
            # Проверяем, есть ли Group с таким именем
            group = Group.objects.filter(name=name).first()
            
            if group:
                # Group существует, но Role нет - создаём Role через SQL
                with connection.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO cms_adp_role (group_ptr_id, description, is_system, created_at, updated_at)
                        VALUES (%s, %s, %s, NOW(), NOW())
                        ON CONFLICT (group_ptr_id) DO NOTHING
                    """, [group.id, description, is_system])
                
                # Получаем созданную Role
                return Role.objects.get(name=name)
            else:
                # Group не существует - создаём Role обычным способом
                role = Role.objects.create(
                    name=name,
                    description=description,
                    is_system=is_system
                )
                return role
        except Exception as e:
            logger.error(
                f"Не удалось получить или создать роль '{name}'. "
                f"Убедитесь, что миграции применены. Ошибка: {e}",
                exc_info=True
            )
            raise

    @staticmethod
    def _get_or_create_default_role() -> Role:
        return PermissionService._get_or_create_role_safe(
            name=PermissionService.DEFAULT_ROLE_NAME,
            description=PermissionService.DEFAULT_ROLE_DESCRIPTION,
            is_system=True
        )

    @staticmethod
    def _get_or_create_admin_role() -> Role:
        return PermissionService._get_or_create_role_safe(
            name=PermissionService.ADMIN_ROLE_NAME,
            description=PermissionService.ADMIN_ROLE_DESCRIPTION,
            is_system=True
        )

    @staticmethod
    def _is_admin_role(role: Role) -> bool:
        """Определяет, относится ли роль к административным."""
        if not role:
            return False
        return role.role_type == 'admin'

    @staticmethod
    def _sync_django_admin_flags(user: User, should_be_admin: bool):
        """
        Синхронизирует is_staff и is_superuser с ADP-ролью администратора (единая сущность).
        """
        if not getattr(user, 'pk', None):
            return

        update_fields = []
        if getattr(user, 'is_staff', False) != should_be_admin:
            user.is_staff = should_be_admin
            update_fields.append('is_staff')
        if getattr(user, 'is_superuser', False) != should_be_admin:
            user.is_superuser = should_be_admin
            update_fields.append('is_superuser')
        if update_fields:
            user.save(update_fields=update_fields)

    @staticmethod
    def count_global_admins() -> int:
        from django.db.models import Q

        admin_role = PermissionService._get_or_create_admin_role()
        return (
            User.objects.filter(
                Q(is_superuser=True)
                | Q(user_roles__role=admin_role, user_roles__is_active=True)
            )
            .distinct()
            .count()
        )

    @staticmethod
    def validate_role_change(
        user: User,
        role: Role,
        assigned_by: User = None,
    ) -> Optional[str]:
        was_admin = PermissionService._is_global_admin(user)
        will_be_admin = PermissionService._is_admin_role(role)
        if was_admin and not will_be_admin:
            if PermissionService.count_global_admins() <= 1:
                return _('Нельзя снять роль у последнего администратора системы.')
        return None

    @staticmethod
    def ensure_system_roles():
        """Гарантирует наличие базовых системных ролей."""
        # Создаём или обновляем роль "Пользователь" - делаем её системной
        default_role = PermissionService._get_or_create_default_role()
        if not default_role.is_system:
            default_role.is_system = True
            default_role.save(update_fields=['is_system'])
        
        # Создаём или обновляем роль "Администратор" - делаем её системной
        admin_role = PermissionService._get_or_create_admin_role()
        if not admin_role.is_system:
            admin_role.is_system = True
            admin_role.save(update_fields=['is_system'])

    @staticmethod
    def assign_default_role(user: User) -> UserRole:
        """Назначить пользователю роль по умолчанию"""
        default_role = PermissionService._get_or_create_default_role()
        user_role, created = UserRole.objects.get_or_create(
            user=user,
            role=default_role,
            defaults={'is_active': True}
        )
        if not created and not user_role.is_active:
            user_role.is_active = True
            user_role.save(update_fields=['is_active'])
        PermissionService._sync_django_admin_flags(user, False)
        return user_role
    
    @staticmethod
    def _is_global_admin(user: User, *, honor_preview: bool = True) -> bool:
        """
        Глобальный администратор: is_superuser и активная ADP-роль admin синхронизированы
        (_sync_django_admin_flags). Проверяем оба источника для устойчивости.
        """
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if jwt_claims_on_module():
            req_cache = get_request_permission_cache()
            cache_key = f'is_global_admin:{getattr(user, "pk", None)}'
            if cache_key in req_cache:
                return req_cache[cache_key]
            # JWT/снимок — источник правды. HTTP только для токенов без флагов.
            if getattr(user, 'is_admin', False) or getattr(user, 'is_superuser', False):
                result = True
            else:
                result = remote_is_admin(user)
            req_cache[cache_key] = result
            return result

        if honor_preview:
            from src.core.cms.adp.dev_tools.preview import preview_suppresses_admin

            if preview_suppresses_admin():
                return False

        cache = get_request_permission_cache()
        cache_key = f'is_global_admin:{user.pk}'
        if cache_key in cache:
            return cache[cache_key]

        if getattr(user, 'is_superuser', False):
            cache[cache_key] = True
            return True
        user_role = PermissionService.get_user_role(user, honor_preview=False)
        result = bool(
            user_role
            and user_role.is_active
            and PermissionService._is_admin_role(user_role.role)
        )
        cache[cache_key] = result
        return result

    @staticmethod
    def is_admin(user: User) -> bool:
        """Проверить, является ли пользователь глобальным администратором."""
        return PermissionService._is_global_admin(user)

    @staticmethod
    def can_manage_users_as_global_admin(user: User) -> bool:
        """Глобальный администратор (эквивалент is_admin)."""
        return PermissionService._is_global_admin(user)

    @staticmethod
    def can_access_admin_panel(user: User) -> bool:
        """Доступ к панели администратора — эквивалент глобального админа."""
        return PermissionService.can_manage_users_as_global_admin(user)

    @staticmethod
    def resolve_display_role(
        user: User,
        user_role: Optional[UserRole] = None,
        *,
        admin_role: Optional[Role] = None,
    ) -> Optional[Role]:
        """Эффективная роль для отображения в админ-панели."""
        if PermissionService._is_global_admin(user):
            if user_role and user_role.role:
                return user_role.role
            return admin_role or PermissionService._get_or_create_admin_role()

        if user_role and user_role.role:
            return user_role.role

        return None
    
    @staticmethod
    def _get_policies_for_user(user: User, user_role: UserRole, policy_type: str) -> list:
        cache = get_request_permission_cache()
        cache_key = f'{policy_type}_policies:{user.pk}'
        if cache_key in cache:
            return cache[cache_key]

        policies = list(
            Policy.objects.filter(
                role=user_role.role,
                policy_type=policy_type,
                is_active=True,
            )
        )
        group_ids = [
            group.id
            for group in user_role.role_groups.all()
            if group.is_active
        ]
        if group_ids:
            policies.extend(
                Policy.objects.filter(
                    role_group_id__in=group_ids,
                    policy_type=policy_type,
                    is_active=True,
                )
            )
        cache[cache_key] = policies
        return policies

    @staticmethod
    def _get_url_policies_for_user(user: User, user_role: UserRole) -> list:
        return PermissionService._get_policies_for_user(user, user_role, 'url')

    @staticmethod
    def _get_api_policies_for_user(user: User, user_role: UserRole) -> list:
        return PermissionService._get_policies_for_user(user, user_role, 'api')

    @staticmethod
    def _excluded_granted_role_group_ids(role_group_ids: list[int]) -> set[int]:
        """Id групп, исключённых модулями из глобальных ModulePermission grants."""
        if not role_group_ids:
            return set()
        excluded: set[int] = set()
        for result in bridge.emit(
            ADP_FILTER_GRANTED_ROLE_GROUP_IDS,
            role_group_ids=list(role_group_ids),
        ):
            if not result:
                continue
            if isinstance(result, (set, list, tuple, frozenset)):
                for group_id in result:
                    try:
                        excluded.add(int(group_id))
                    except (TypeError, ValueError):
                        continue
        return excluded

    @staticmethod
    def _filter_role_group_ids_for_grants(role_group_ids: list[int]) -> list[int]:
        excluded = PermissionService._excluded_granted_role_group_ids(role_group_ids)
        if not excluded:
            return role_group_ids
        return [group_id for group_id in role_group_ids if group_id not in excluded]

    @staticmethod
    def _session_scoped_module_permissions(user: User, session_claims: dict | None):
        """Доп. ModulePermission из модулей для текущего session-scope."""
        claims = dict(session_claims or {})
        if not claims or not user:
            return []
        extra = []
        for result in bridge.emit(
            ADP_SESSION_SCOPED_MODULE_PERMISSIONS,
            **bridge_user_kwargs(user, **claims),
        ):
            if not result:
                continue
            if isinstance(result, (list, tuple)):
                extra.extend(result)
        return extra

    @staticmethod
    def _session_scoped_denied_permissions(user: User, session_claims: dict | None) -> set[tuple[str, str]]:
        """Пары (module_name, permission_key), которые модули вычитают из snapshot."""
        denied: set[tuple[str, str]] = set()
        claims = dict(session_claims or {})
        if not claims or not user:
            return denied
        for result in bridge.emit(
            ADP_SESSION_SCOPED_DENIED_PERMISSIONS,
            **bridge_user_kwargs(user, **claims),
        ):
            if not result:
                continue
            if isinstance(result, (list, tuple, set, frozenset)):
                for item in result:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        denied.add((item[0], item[1]))
                    elif isinstance(item, dict):
                        module_name = item.get('module_name')
                        permission_key = item.get('permission_key')
                        if module_name and permission_key:
                            denied.add((module_name, permission_key))
        return denied

    @staticmethod
    def _default_view_permission_pairs(user_role) -> set[tuple[str, str]]:
        from src.core.cms.adp.services.default_view_permissions import collect_default_view_pairs

        return collect_default_view_pairs(
            user_role,
            default_role_name=PermissionService.DEFAULT_ROLE_NAME,
        )

    @staticmethod
    def _append_default_view_permissions(user_role, module_permissions: list) -> list:
        from src.core.cms.adp.services.default_view_permissions import append_default_view_permissions

        return append_default_view_permissions(
            user_role,
            module_permissions,
            default_role_name=PermissionService.DEFAULT_ROLE_NAME,
        )

    @staticmethod
    def _get_granted_module_permission_keys(user: User) -> set[tuple[str, str]]:
        cache = get_request_permission_cache()
        cache_key = f'module_perm_keys:{user.pk}'
        if cache_key in cache:
            return cache[cache_key]

        user_role = PermissionService.get_user_role(user)
        if not user_role:
            cache[cache_key] = set()
            return cache[cache_key]

        group_ids = PermissionService._filter_role_group_ids_for_grants([
            group.id
            for group in user_role.role_groups.all()
            if group.is_active
        ])
        if not group_ids:
            result = PermissionService._default_view_permission_pairs(user_role)
            cache[cache_key] = result
            return cache[cache_key]

        rows = ModulePermission.objects.filter(
            role_group_id__in=group_ids,
            is_granted=True,
        ).values_list('module_name', 'permission_key')
        result = {(module_name, permission_key) for module_name, permission_key in rows}
        cache[cache_key] = result
        return result

    @staticmethod
    def check_url_access(user: User, url_path: str) -> bool:
        """
        Проверить доступ пользователя к URL.
        
        Иерархия прав:
        1. Администратор → полный доступ ко всему
        2. Пользователь (базовая роль) → доступ разрешён по умолчанию
        3. Ролевые группы → могут ограничивать (deny) или явно разрешать (allow) доступ
        4. Политики применяются по приоритету (высший приоритет побеждает)
        
        Args:
            user: Пользователь
            url_path: Путь URL для проверки
            
        Returns:
            True если доступ разрешен, False если запрещен
        """
        # Администраторы имеют доступ ко всем URL
        if PermissionService.is_admin(user):
            return True
        
        # Получаем роль пользователя (автоматически назначается "Пользователь" если нет)
        user_role = PermissionService.get_user_role(user)
        if not user_role:
            return False

        policies = PermissionService._get_url_policies_for_user(user, user_role)

        for policy in sorted(policies, key=lambda p: p.priority, reverse=True):
            # API-пути не участвуют в клиентском URL-ACL (даже если попали в старые записи)
            if PermissionService._is_api_resource_path(policy.resource_path):
                continue
            if PermissionService._match_url_pattern(url_path, policy.resource_path, policy.is_pattern):
                # Найдено совпадение — возвращаем результат политики
                return policy.action == 'allow'
        
        # Если не найдено подходящих политик:
        # Для роли "Пользователь" — разрешаем доступ по умолчанию (базовый просмотр)
        # Это обеспечивает концепцию "пользователь видит всё, группы ограничивают"
        if user_role.role.name == PermissionService.DEFAULT_ROLE_NAME:
            return True
        
        # Для других ролей — запрещаем по умолчанию
        return False

    @staticmethod
    def _is_api_resource_path(path: str) -> bool:
        value = (path or '').strip()
        return (
            value == '/api'
            or value.startswith('/api/')
            or value.startswith('/api*')
        )

    @staticmethod
    def check_api_access(user: User, api_path: str) -> bool:
        """
        Проверить доступ пользователя к API path (Policy policy_type='api').

        Семантика как у check_url_access: admin → allow; match по priority;
        роль «Пользователь» без match → allow; иначе deny.
        """
        if PermissionService.is_admin(user):
            return True
        if jwt_claims_on_module():
            req_cache = get_request_permission_cache()
            cache_key = f'api_access:{getattr(user, "pk", None)}:{api_path}'
            if cache_key in req_cache:
                return req_cache[cache_key]
            result = remote_check_api_access(user, api_path)
            req_cache[cache_key] = result
            return result

        user_role = PermissionService.get_user_role(user)
        if not user_role:
            return False

        policies = PermissionService._get_api_policies_for_user(user, user_role)

        for policy in sorted(policies, key=lambda p: p.priority, reverse=True):
            # Клиентские Vue-пути не режут HTTP API
            if not PermissionService._is_api_resource_path(policy.resource_path):
                continue
            if PermissionService._match_url_pattern(api_path, policy.resource_path, policy.is_pattern):
                return policy.action == 'allow'

        if user_role.role.name == PermissionService.DEFAULT_ROLE_NAME:
            return True

        return False
    
    @staticmethod
    def _match_url_pattern(url_path: str, pattern: str, is_pattern: bool) -> bool:
        """
        Проверить соответствие URL шаблону.
        
        Args:
            url_path: Проверяемый URL
            pattern: Шаблон для сравнения
            is_pattern: Использовать ли wildcards
            
        Returns:
            True если URL соответствует шаблону
        """
        if not is_pattern:
            return url_path == pattern

        # Пресет «весь модуль» — `/path/**`. Иначе `**` → `/.*` и хаб `/path` не закрывается.
        if pattern.endswith('/**'):
            stem = PermissionService._wildcard_to_regex_body(pattern[:-3])
            regex_pattern = f'^{stem}(?:/.*)?$'
        else:
            regex_pattern = f'^{PermissionService._wildcard_to_regex_body(pattern)}$'

        return bool(re.match(regex_pattern, url_path))

    @staticmethod
    def _wildcard_to_regex_body(pattern: str) -> str:
        return (
            pattern.replace('**', '<<<DOUBLE_STAR>>>')
            .replace('*', '[^/]+')
            .replace('<<<DOUBLE_STAR>>>', '.*')
        )
    
    @staticmethod
    def _denied_api_paths_for_role(user_role, role_groups) -> list:
        group_ids = [group.id for group in role_groups]
        query = Q(is_active=True, policy_type='api', action='deny') & (
            Q(role=user_role.role) | Q(role_group_id__in=group_ids)
        )
        paths = []
        for path in Policy.objects.filter(query).values_list('resource_path', flat=True):
            if PermissionService._is_api_resource_path(path):
                paths.append(path)
        return paths

    @staticmethod
    def _api_deny_covers_module(user_role, role_groups, module_name: str) -> bool:
        """True, если deny policy_type=api закрывает /api/<module>/."""
        slug = (module_name or '').strip('/')
        if not slug:
            return False
        api_path = f'/api/{slug}/'
        for deny_path in PermissionService._denied_api_paths_for_role(user_role, role_groups):
            is_pattern = '*' in (deny_path or '')
            if PermissionService._match_url_pattern(api_path, deny_path, is_pattern):
                return True
        return False

    @staticmethod
    def _default_role_can_view_module(user_role, module_name: str) -> bool:
        """
        _view для роли «Пользователь» без групп.

        granted — все модули. denied — только те, которые ACL API не закрыл.
        """
        if adp_default_view_grants() == 'granted':
            return True
        return not PermissionService._api_deny_covers_module(user_role, [], module_name)

    @staticmethod
    def get_user_permissions(user: User, session_claims: dict | None = None) -> Dict:
        """
        Получить все права пользователя.

        session_claims — произвольный dict JWT session-claim текущего scope.
        Ядро не знает ключи: модули читают свои имена из kwargs моста.
        """
        user_role = PermissionService.get_user_role(user)
        default_view_grants = adp_default_view_grants()

        if not user_role:
            is_global_admin = PermissionService.is_admin(user)
            return {
                'user_id': user.id,
                'username': user.username,
                'role': None,
                'role_groups': [],
                'allowed_urls': [],
                'denied_urls': [],
                'denied_api': [],
                'default_view_grants': default_view_grants,
                'module_permissions': [],
                'is_global_admin': is_global_admin,
            }
        
        # Собираем разрешенные и запрещенные URL
        allowed_urls = []
        denied_urls = []
        
        # Политики роли
        role_policies = Policy.objects.filter(
            role=user_role.role,
            policy_type='url',
            is_active=True
        )
        
        for policy in role_policies:
            if PermissionService._is_api_resource_path(policy.resource_path):
                continue
            if policy.action == 'allow':
                allowed_urls.append(policy.resource_path)
            else:
                denied_urls.append(policy.resource_path)
        
        # Политики ролевых групп (URL — по всем группам пользователя)
        role_groups = list(user_role.role_groups.filter(is_active=True))
        for group in role_groups:
            group_policies = Policy.objects.filter(
                role_group=group,
                policy_type='url',
                is_active=True
            )
            
            for policy in group_policies:
                if PermissionService._is_api_resource_path(policy.resource_path):
                    continue
                if policy.action == 'allow':
                    allowed_urls.append(policy.resource_path)
                else:
                    denied_urls.append(policy.resource_path)

        grant_group_ids = set(
            PermissionService._filter_role_group_ids_for_grants(
                [group.id for group in role_groups]
            )
        )
        module_permissions = []
        for group in role_groups:
            if group.id not in grant_group_ids:
                continue
            perms = ModulePermission.objects.filter(
                role_group=group,
                is_granted=True
            )
            module_permissions.extend(perms)

        from src.core.cms.adp.dev_tools.preview import resolve_effective_user

        permission_user = resolve_effective_user(user)
        module_permissions.extend(
            PermissionService._session_scoped_module_permissions(permission_user, session_claims)
        )

        denied = PermissionService._session_scoped_denied_permissions(permission_user, session_claims)
        if denied:
            module_permissions = [
                perm for perm in module_permissions
                if (getattr(perm, 'module_name', None), getattr(perm, 'permission_key', None)) not in denied
            ]

        module_permissions = PermissionService._append_default_view_permissions(
            user_role,
            module_permissions,
        )

        from src.core.cms.adp.dev_tools.preview import apply_preview_module_permissions

        module_permissions = apply_preview_module_permissions(module_permissions)

        return {
            'user_id': user.id,
            'username': user.username,
            'role': user_role.role,
            'role_groups': role_groups,
            'allowed_urls': allowed_urls,
            'denied_urls': denied_urls,
            'denied_api': PermissionService._denied_api_paths_for_role(
                user_role, role_groups
            ),
            'default_view_grants': default_view_grants,
            'module_permissions': module_permissions,
            'is_global_admin': PermissionService.is_admin(user),
        }
    
    @staticmethod
    def check_module_permission(
        user: User, 
        module_name: str, 
        permission_key: str,
        **kwargs
    ) -> bool:
        """
        Проверить право доступа к функционалу модуля.
        
        Иерархия прав:
        1. Администратор → полный доступ
        2. Хуки модулей → контекстная проверка (например, доменные права модуля)
        3. Ролевые группы пользователя → явно настроенные права (is_granted=True);
           `_view` роли «Пользователь» при этом не отбирается у модуля без API-deny
        4. Базовая роль "Пользователь" без групп → `_view`: все модули при
           API_ADP_DEFAULT_VIEW_GRANTS=granted, иначе только модули без API-deny
        
        Args:
            user: Пользователь
            module_name: Название модуля
            permission_key: Ключ разрешения
            **kwargs: Дополнительные параметры для хуков (например, scope-id)
            
        Returns:
            True если доступ разрешен
        """
        from src.core.cms.adp.dev_tools.preview import preview_permission_override

        override = preview_permission_override(module_name, permission_key)
        if override is False:
            return False
        if override is True:
            return True

        kwargs = merge_session_scope_kwargs(kwargs)

        if PermissionService.is_admin(user):
            return True

        if jwt_claims_on_module():
            from src.core.cms.adp.services.jwt_claims_cache import extra_fingerprint

            req_cache = get_request_permission_cache()
            cache_key = (
                f'module_perm:{getattr(user, "pk", None)}:'
                f'{module_name}:{permission_key}:{extra_fingerprint(kwargs)}'
            )
            if cache_key in req_cache:
                return req_cache[cache_key]
            result = remote_check_module_permission(
                user,
                module_name,
                permission_key,
                kwargs,
            )
            req_cache[cache_key] = result
            return result

        # Вызываем подписчиков события 'adp.permission_check'.
        # Подписчики модулей добавляют контекстную логику.
        # Первый non-None результат побеждает.
        contextual = bridge.emit_first(
            ADP_PERMISSION_CHECK,
            **bridge_user_kwargs(
                user,
                module_name=module_name,
                permission_key=permission_key,
                kwargs=kwargs,
            ),
        )
        if contextual is not None:
            return contextual

        user_role = PermissionService.get_user_role(user)
        if not user_role:
            return False

        role_groups = [group for group in user_role.role_groups.all() if group.is_active]

        if role_groups:
            granted = PermissionService._get_granted_module_permission_keys(user)
            if (module_name, permission_key) in granted:
                return True
            if (
                user_role.role.name == PermissionService.DEFAULT_ROLE_NAME
                and permission_key.endswith('_view')
                and not PermissionService._api_deny_covers_module(
                    user_role, role_groups, module_name
                )
            ):
                return PermissionService._default_role_can_view_module(
                    user_role,
                    module_name,
                )
            return False

        if user_role.role.name == PermissionService.DEFAULT_ROLE_NAME:
            if permission_key.endswith('_view') and PermissionService._default_role_can_view_module(
                user_role,
                module_name,
            ):
                return True

        return False
    
    @staticmethod
    def assign_role_to_user(
        user: User, 
        role: Role, 
        role_groups: List[RoleGroup] = None,
        assigned_by: User = None
    ) -> UserRole:
        """
        Назначить роль пользователю.
        
        Args:
            user: Пользователь
            role: Роль
            role_groups: Список ролевых групп (опционально)
            assigned_by: Кто назначил роль
            
        Returns:
            Объект UserRole
        """
        validation_error = PermissionService.validate_role_change(user, role, assigned_by)
        if validation_error:
            raise RoleAssignmentError(validation_error)

        # Деактивируем все существующие роли пользователя
        UserRole.objects.filter(user=user, is_active=True).update(is_active=False)
        
        # Создаем новую роль
        user_role, created = UserRole.objects.get_or_create(
            user=user,
            role=role,
            defaults={
                'is_active': True,
                'assigned_by': assigned_by
            }
        )
        
        if not created:
            user_role.is_active = True
            user_role.assigned_by = assigned_by
            user_role.save()

        # Пустой список снимает ранее выданные группы (не оставлять старые grants).
        user_role.role_groups.set(role_groups or [])
        
        PermissionService._sync_django_admin_flags(
            user,
            PermissionService._is_admin_role(role),
        )

        from src.core.cms.adp.services.permissions_snapshot_cache import (
            invalidate_user_permissions_snapshot,
        )

        invalidate_user_permissions_snapshot(user.pk)

        return user_role
