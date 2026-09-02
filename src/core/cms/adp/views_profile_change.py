"""
API заявок на изменение ФИО пользователя.
"""

from django.utils.translation import gettext as _
from src.core.search.mixins import parse_search_pagination
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from src.core.cms.adp.models import UserProfileChangeRequest
from src.core.cms.adp.profile_change_serializers import (
    CreateUserProfileChangeRequestSerializer,
    RejectUserProfileChangeRequestSerializer,
    UserProfileChangeRequestSerializer,
)
from src.core.cms.adp.services.profile_change_request import ProfileChangeRequestService
from src.core.cms.adp.services.profile_settings import ProfileSettingsService
from src.core.cms.adp.services.admin_access import require_global_admin_response
from src.core.audit.shortcuts import audit_log
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewAuthMixin, BaseAPIViewPublicMixin


def _parse_pagination(request, default_page_size=12):
    page, page_size, search = parse_search_pagination(
        request,
        default_page_size=default_page_size,
        max_page_size=100,
    )
    status_filter = (request.query_params.get('status') or '').strip().lower()
    return page, page_size, status_filter, search


def _serialize_request(request_obj):
    return UserProfileChangeRequestSerializer(request_obj).data


class ProfileSettingsView(BaseAPIViewPublicMixin):
    """Публичные настройки редактирования профиля."""

    @swagger_auto_schema(
        operation_description='Получить настройки редактирования профиля',
        responses={200: openapi.Response(description='Настройки профиля')},
    )
    def get(self, request):
        return Response(ProfileSettingsService.get_public_settings())


class UserProfileChangeRequestListCreateView(BaseAPIViewAuthMixin, BaseAPIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Список заявок текущего пользователя на изменение ФИО',
        responses={200: UserProfileChangeRequestSerializer(many=True)},
    )
    def get(self, request):
        if not ProfileChangeRequestService.is_request_flow_enabled(request.user):
            return Response({'requests': [], 'request_flow_enabled': False})

        queryset = (
            UserProfileChangeRequest.objects
            .filter(user=request.user)
            .select_related('user', 'user__adp_profile', 'reviewed_by')
            .order_by('-created_at')[:20]
        )
        return Response({
            'requests': [_serialize_request(item) for item in queryset],
            'request_flow_enabled': True,
        })

    @swagger_auto_schema(
        operation_description='Создать заявку на изменение ФИО',
        request_body=CreateUserProfileChangeRequestSerializer,
        responses={
            201: UserProfileChangeRequestSerializer(),
            400: 'Ошибка валидации',
            403: 'Заявки недоступны',
        },
    )
    def post(self, request):
        if not ProfileChangeRequestService.is_request_flow_enabled(request.user):
            return Response(
                {'error': ProfileChangeRequestService.NOT_ALLOWED_MESSAGE},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CreateUserProfileChangeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            change_request = ProfileChangeRequestService.create_request(
                request.user,
                email=serializer.validated_data.get('email', ''),
                first_name=serializer.validated_data.get('first_name', ''),
                last_name=serializer.validated_data.get('last_name', ''),
                middle_name=serializer.validated_data.get('middle_name', ''),
                phone=serializer.validated_data.get('phone', ''),
                comment=serializer.validated_data.get('comment', ''),
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        change_request = UserProfileChangeRequest.objects.select_related(
            'user',
            'reviewed_by',
        ).get(pk=change_request.pk)
        return Response(_serialize_request(change_request), status=status.HTTP_201_CREATED)


class AdminProfileChangeRequestListView(BaseAPIViewAuthMixin, BaseAPIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Реестр заявок на изменение ФИО (глобальные администраторы)',
        responses={200: openapi.Response(description='Список заявок')},
    )
    def get(self, request):
        forbidden = require_global_admin_response(request)
        if forbidden:
            return forbidden

        page, page_size, status_filter, search = _parse_pagination(request)
        queryset = UserProfileChangeRequest.objects.select_related(
            'user',
            'user__adp_profile',
            'reviewed_by',
        ).order_by('-created_at')

        valid_statuses = {
            UserProfileChangeRequest.STATUS_PENDING,
            UserProfileChangeRequest.STATUS_APPROVED,
            UserProfileChangeRequest.STATUS_REJECTED,
        }
        if status_filter in valid_statuses:
            queryset = queryset.filter(status=status_filter)

        from src.core.search.core_indexes import INDEX_PROFILE_CHANGE
        from src.core.search.service import search_queryset

        queryset, search_result = search_queryset(
            INDEX_PROFILE_CHANGE,
            search,
            queryset,
            page=page,
            page_size=page_size,
        )

        total = search_result.total
        pending_count = UserProfileChangeRequest.objects.filter(
            status=UserProfileChangeRequest.STATUS_PENDING,
        ).count()
        items = list(queryset)

        return Response({
            'requests': [_serialize_request(item) for item in items],
            'total': total,
            'pending_count': pending_count,
            'page': page,
            'page_size': page_size,
            'profile_self_edit_enabled': ProfileSettingsService.is_self_fio_edit_enabled(),
        })


class AdminProfileChangeRequestApproveView(BaseAPIViewAuthMixin, BaseAPIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Одобрить заявку на изменение ФИО',
        responses={200: UserProfileChangeRequestSerializer(), 400: 'Ошибка', 404: 'Не найдено'},
    )
    def post(self, request, request_id):
        forbidden = require_global_admin_response(request)
        if forbidden:
            return forbidden

        change_request = UserProfileChangeRequest.objects.select_related(
            'user',
            'reviewed_by',
        ).filter(pk=request_id).first()
        if not change_request:
            return Response({'error': _('Заявка не найдена')}, status=status.HTTP_404_NOT_FOUND)

        try:
            ProfileChangeRequestService.approve_request(change_request, request.user)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        audit_log('profile_change.approved', request=request,
               entity={'type': 'user', 'label': change_request.user.get_full_name() or change_request.user.username})

        change_request.refresh_from_db()
        change_request = UserProfileChangeRequest.objects.select_related(
            'user',
            'reviewed_by',
        ).get(pk=change_request.pk)
        return Response(_serialize_request(change_request))


class AdminProfileChangeRequestRejectView(BaseAPIViewAuthMixin, BaseAPIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Отклонить заявку на изменение ФИО',
        request_body=RejectUserProfileChangeRequestSerializer,
        responses={200: UserProfileChangeRequestSerializer(), 400: 'Ошибка', 404: 'Не найдено'},
    )
    def post(self, request, request_id):
        forbidden = require_global_admin_response(request)
        if forbidden:
            return forbidden

        change_request = UserProfileChangeRequest.objects.select_related(
            'user',
            'reviewed_by',
        ).filter(pk=request_id).first()
        if not change_request:
            return Response({'error': _('Заявка не найдена')}, status=status.HTTP_404_NOT_FOUND)

        serializer = RejectUserProfileChangeRequestSerializer(data=request.data or {})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            ProfileChangeRequestService.reject_request(
                change_request,
                request.user,
                admin_comment=serializer.validated_data.get('admin_comment', ''),
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        audit_log('profile_change.rejected', request=request,
               entity={'type': 'user', 'label': change_request.user.get_full_name() or change_request.user.username})

        change_request.refresh_from_db()
        change_request = UserProfileChangeRequest.objects.select_related(
            'user',
            'reviewed_by',
        ).get(pk=change_request.pk)
        return Response(_serialize_request(change_request))
