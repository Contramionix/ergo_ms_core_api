"""Список и создание пользователей в админ-панели."""
from django.contrib.auth import get_user_model
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.response import Response

from src.core.audit.shortcuts import audit_log
from src.core.cms.adp.admin_users_common import (
    _build_admin_user_detail,
    _build_admin_user_list_item,
    _get_admin_users_base_queryset,
    _parse_admin_users_list_filters,
    _parse_admin_users_pagination,
)
from src.core.search.core_indexes import INDEX_USERS
from src.core.search.service import search_queryset
from src.core.cms.adp.serializers import (
    AdminCreateUserSerializer,
    AdminUserRoleInfoSerializer,
    CMSUserSerializer,
)
from src.core.cms.adp.services.admin_user import AdminUserCreateError, create_admin_user
from src.core.cms.adp.services import presence as presence_service
from src.core.cms.adp.services.permissions import PermissionService
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewGlobalAdminMixin
from src.core.utils.methods import parse_errors_to_dict

User = get_user_model()


class AdminUserRoleListView(BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """
    Получение списка пользователей и их ролей для администраторов.
    """

    @swagger_auto_schema(
        operation_description=(
            "Получить список пользователей и их ролей. "
            "Поддерживает пагинацию (page, page_size) и поиск (search)."
        ),
        manual_parameters=[
            openapi.Parameter(
                'page',
                openapi.IN_QUERY,
                description='Номер страницы (по умолчанию 1)',
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'page_size',
                openapi.IN_QUERY,
                description='Размер страницы (по умолчанию 12, максимум 100)',
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'q',
                openapi.IN_QUERY,
                description='Поиск по username, email, имени, фамилии и отчеству (BM25)',
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'search',
                openapi.IN_QUERY,
                description='Устаревший alias для q',
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'online_only',
                openapi.IN_QUERY,
                description='Устаревший alias: только онлайн (true, 1, yes). Синоним presence=online',
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                'presence',
                openapi.IN_QUERY,
                description='Фильтр присутствия: online или offline',
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'role',
                openapi.IN_QUERY,
                description='Идентификатор активной глобальной роли',
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'joined_from',
                openapi.IN_QUERY,
                description='Дата регистрации с (YYYY-MM-DD)',
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'joined_to',
                openapi.IN_QUERY,
                description='Дата регистрации по (YYYY-MM-DD)',
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'last_seen_from',
                openapi.IN_QUERY,
                description='Последняя активность с (YYYY-MM-DD)',
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'last_seen_to',
                openapi.IN_QUERY,
                description='Последняя активность по (YYYY-MM-DD)',
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                'letter',
                openapi.IN_QUERY,
                description=(
                    'Первая буква фамилии (кириллица или латиница). '
                    'Ё учитывается в Е, Й — в И.'
                ),
                type=openapi.TYPE_STRING,
                required=False,
            ),
        ],
        responses={200: AdminUserRoleInfoSerializer(many=True)}
    )
    def get(self, request):
        page, page_size, search = _parse_admin_users_pagination(request)
        list_filters = _parse_admin_users_list_filters(request)
        base_qs = _get_admin_users_base_queryset(**list_filters)
        users_qs, search_result = search_queryset(
            INDEX_USERS,
            search,
            base_qs,
            page=page,
            page_size=page_size,
        )
        users = list(users_qs)
        total = search_result.total
        admin_role = PermissionService._get_or_create_admin_role()
        presence_map = presence_service.get_presence_map([user.id for user in users])

        items = [
            _build_admin_user_list_item(
                user,
                admin_role=admin_role,
                presence_entry=presence_map.get(user.id),
            )
            for user in users
        ]
        serializer = AdminUserRoleInfoSerializer(items, many=True)

        return Response({
            'users': serializer.data,
            'total': total,
            'page': page,
            'page_size': page_size,
        })

    @swagger_auto_schema(
        operation_description=(
            "Создать пользователя вручную (без приглашения и независимо от режима регистрации)."
        ),
        request_body=AdminCreateUserSerializer,
        responses={201: CMSUserSerializer(), 400: 'Ошибка валидации данных.'},
    )
    def post(self, request):
        serializer = AdminCreateUserSerializer(data=request.data)
        if not serializer.is_valid():
            errors = parse_errors_to_dict(serializer.errors)
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            user, meta = create_admin_user(
                username=data['username'],
                created_by=request.user,
                password=data.get('password') or '',
                first_name=data.get('first_name') or '',
                last_name=data.get('last_name') or '',
                middle_name=data.get('middle_name') or '',
                email=data.get('email') or '',
                role_id=data.get('role_id'),
                role_group_ids=data.get('role_group_ids') or [],
                send_password_notification=data.get('send_password_notification', True),
            )
        except AdminUserCreateError as exc:
            return Response({'error': exc.message}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.select_related('adp_profile').get(pk=user.pk)
        audit_log('user.created', request=request, severity='security',
               entity={'type': 'user', 'label': user.get_full_name() or user.username},
               meta={'username': user.username})
        response_data = _build_admin_user_detail(user)
        response_data.update(meta)
        return Response(response_data, status=status.HTTP_201_CREATED)
