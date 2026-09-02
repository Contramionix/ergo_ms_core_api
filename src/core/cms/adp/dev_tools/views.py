"""API режима разработчика: overlay прав без записи ролей в БД."""

from django.contrib.auth import get_user_model
from django.db.models import Prefetch, Q
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from src.core.cms.adp.dev_tools.preview import (
    clear_preview,
    effective_permission_pairs_for_preview,
    load_preview,
    preview_from_payload,
    save_preview,
)
from src.core.cms.adp.dev_tools.runtime import is_dev_tools_enabled
from src.core.cms.adp.models import Role, UserRole
from src.core.cms.adp.services.permission_catalog import get_modules_catalog
from src.core.cms.adp.services.permissions import PermissionService
from src.core.search.core_indexes import INDEX_USERS
from src.core.search.fallback import apply_ordered_ids, fallback_search
from src.core.search.service import search_queryset
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewGlobalAdminMixin
from src.core.utils.swagger.yasg_compat import swagger_auto_schema

User = get_user_model()


class _DevToolsEnabledMixin:
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not is_dev_tools_enabled():
            raise NotFound(_('Режим разработчика выключен.'))


def _empty_preview_payload() -> dict:
    return {
        'view_as_regular': False,
        'as_user_public_id': None,
        'as_user_label': None,
        'role_name': None,
        'extra_grants': [],
        'extra_denies': [],
        'base_permissions': [],
        'effective_permissions': [],
        'effective_is_admin': False,
    }


def _preview_state(user, preview) -> dict:
    if preview is None or not preview.is_active():
        return _empty_preview_payload()
    payload = preview.to_payload()
    base, effective, is_admin = effective_permission_pairs_for_preview(user, preview)
    payload['base_permissions'] = base
    payload['effective_permissions'] = effective
    payload['effective_is_admin'] = is_admin
    return payload


def _user_label(user) -> str:
    full_name = ''
    if hasattr(user, 'get_full_name'):
        full_name = (user.get_full_name() or '').strip()
    if full_name:
        return full_name
    return user.username or str(user.public_id)


class DevToolsStatusView(_DevToolsEnabledMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Флаг режима и текущий overlay."""

    @swagger_auto_schema(operation_description='Статус режима разработчика')
    def get(self, request):
        preview = load_preview(request.user)
        return Response({
            'enabled': True,
            'preview': _preview_state(request.user, preview),
        })


class DevToolsSessionView(_DevToolsEnabledMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Сохранить / сбросить overlay прав (только кэш, не БД)."""

    @swagger_auto_schema(operation_description='Текущий overlay режима разработчика')
    def get(self, request):
        preview = load_preview(request.user)
        return Response(_preview_state(request.user, preview))

    @swagger_auto_schema(operation_description='Обновить overlay режима разработчика')
    def put(self, request):
        preview = preview_from_payload(request.data if isinstance(request.data, dict) else {})
        if preview.as_user_public_id:
            target = User.objects.filter(public_id=preview.as_user_public_id).first()
            if target is None:
                return Response(
                    {'detail': _('Пользователь не найден.')},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if PermissionService._is_global_admin(target, honor_preview=False):
                return Response(
                    {'detail': _('Нельзя подменить права другого глобального администратора.')},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            preview = preview_from_payload({
                **preview.to_payload(),
                'as_user_label': _user_label(target),
            })
        if preview.role_name and not Role.objects.filter(name=preview.role_name).exists():
            return Response(
                {'detail': _('Роль не найдена.')},
                status=status.HTTP_400_BAD_REQUEST,
            )
        save_preview(request.user, preview)
        stored = load_preview(request.user)
        return Response(_preview_state(request.user, stored))

    @swagger_auto_schema(operation_description='Сбросить overlay режима разработчика')
    def delete(self, request):
        clear_preview(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _dev_tools_users_queryset():
    admin_role = PermissionService._get_or_create_admin_role()
    return (
        User.objects.filter(is_active=True)
        .exclude(
            Q(is_superuser=True)
            | Q(user_roles__role=admin_role, user_roles__is_active=True)
        )
        .distinct()
        .order_by('last_name', 'first_name', 'username')
    )


def _dev_tools_user_item(user) -> dict | None:
    public_id = getattr(user, 'public_id', None)
    if not public_id:
        return None
    active_roles = getattr(user, '_active_roles', None) or []
    role = active_roles[0].role if active_roles else None
    return {
        'public_id': str(public_id),
        'username': user.username or '',
        'full_name': _user_label(user),
        'role_name': getattr(role, 'name', None),
    }


class DevToolsUsersView(_DevToolsEnabledMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Быстрый поиск пользователей для подмены прав."""

    @swagger_auto_schema(operation_description='Поиск пользователей для режима разработчика')
    def get(self, request):
        search = (
            request.query_params.get('q')
            or request.query_params.get('search')
            or ''
        ).strip()
        base_qs = _dev_tools_users_queryset()
        users_qs, search_result = search_queryset(
            INDEX_USERS,
            search,
            base_qs,
            page=1,
            page_size=12,
        )
        if search and not users_qs.exists():
            ids, total = fallback_search(
                INDEX_USERS,
                search,
                base_qs,
                page=1,
                page_size=12,
            )
            users_qs = apply_ordered_ids(base_qs, ids)
            search_result.total = total
        users_qs = users_qs.prefetch_related(
            Prefetch(
                'user_roles',
                queryset=(
                    UserRole.objects
                    .filter(is_active=True)
                    .select_related('role')
                ),
                to_attr='_active_roles',
            )
        )
        items = []
        for user in users_qs:
            row = _dev_tools_user_item(user)
            if row is not None:
                items.append(row)
        return Response({
            'users': items,
            'total': search_result.total,
        })


class DevToolsRolesView(_DevToolsEnabledMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Список ролей для быстрой подмены."""

    @swagger_auto_schema(operation_description='Роли для режима разработчика')
    def get(self, request):
        roles = Role.objects.all().order_by('name')
        return Response({
            'roles': [
                {
                    'name': role.name,
                    'is_system': role.is_system,
                    'role_type': role.role_type,
                }
                for role in roles
            ],
        })


class DevToolsPermissionCatalogView(_DevToolsEnabledMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Каталог прав модулей для переключателей overlay."""

    @swagger_auto_schema(operation_description='Каталог прав для режима разработчика')
    def get(self, request):
        modules = []
        for item in get_modules_catalog():
            permissions = item.get('permissions') or {}
            if not permissions:
                continue
            modules.append({
                'module_name': item.get('module_name'),
                'module_label': item.get('module_label') or item.get('module_name'),
                'permissions': [
                    {'key': key, 'label': label}
                    for key, label in permissions.items()
                ],
            })
        return Response({'modules': modules})
