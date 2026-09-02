"""Сессии и устройства пользователя в админ-панели."""
from django.utils.translation import gettext as _
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.response import Response

from src.core.audit.shortcuts import audit_log
from src.core.cms.adp.admin_users_common import _AdminUserTargetMixin
from src.core.cms.adp.models import UserDevice
from src.core.cms.adp.serializers import UserDeviceSerializer
from src.core.cms.adp.services.session_devices import (
    is_current_device,
    revoke_user_device_session,
)
from src.core.cms.adp.services.user_deletion import revoke_user_auth
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewGlobalAdminMixin


class AdminUserDevicesView(_AdminUserTargetMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Список активных сессий (устройств) пользователя для админ-панели."""

    @swagger_auto_schema(
        operation_description="Получить активные сессии пользователя (админ-панель)",
        responses={200: UserDeviceSerializer(many=True)},
    )
    def get(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref, select_related=False)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        devices = (
            UserDevice.objects
            .filter(user=user, is_active=True)
            .order_by('-last_activity')
        )
        serializer = UserDeviceSerializer(
            devices,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminUserDeviceDetailView(_AdminUserTargetMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Отзыв одной сессии пользователя администратором."""

    @swagger_auto_schema(
        operation_description="Отозвать сессию пользователя (админ-панель)",
        responses={200: 'Сессия завершена', 404: 'Не найдено'},
    )
    def delete(self, request, ref=None, device_id=None):
        user = self._resolve_target_user(request, ref=ref, select_related=False)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        device = UserDevice.objects.filter(id=device_id, user=user).first()
        if device is None:
            return Response(
                {'error': _('Сессия не найдена.')},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.user.pk == user.pk and is_current_device(request, device):
            return Response(
                {'error': _('Нельзя завершить текущую сессию')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        revoke_user_device_session(device)
        audit_log(
            'user.session_revoked',
            request=request,
            severity='security',
            entity={'type': 'user', 'label': user.get_full_name() or user.username},
            meta={'username': user.username, 'device_id': device_id},
        )
        return Response({'message': _('Сессия завершена.')}, status=status.HTTP_200_OK)


class AdminUserRevokeSessionsView(_AdminUserTargetMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Отзыв всех сессий пользователя администратором."""

    @swagger_auto_schema(
        operation_description=(
            "Отозвать все сессии пользователя. "
            "Для собственной учётной записи текущая сессия сохраняется."
        ),
        responses={200: 'Сессии отозваны', 404: 'Пользователь не найден'},
    )
    def post(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref, select_related=False)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.user.pk == user.pk:
            current_device_id = None
            devices = list(
                UserDevice.objects.filter(user=user, is_active=True).order_by('-last_activity')
            )
            revoked = 0
            for device in devices:
                if is_current_device(request, device):
                    current_device_id = device.id
                    continue
                revoke_user_device_session(device)
                revoked += 1
            audit_log(
                'user.sessions_revoked',
                request=request,
                severity='security',
                entity={'type': 'user', 'label': user.get_full_name() or user.username},
                meta={
                    'username': user.username,
                    'revoked_count': revoked,
                    'kept_current': current_device_id is not None,
                },
            )
            return Response(
                {
                    'message': _('Остальные сессии завершены.'),
                    'revoked_count': revoked,
                },
                status=status.HTTP_200_OK,
            )

        active_count = UserDevice.objects.filter(user=user, is_active=True).count()
        revoke_user_auth(user)
        audit_log(
            'user.sessions_revoked',
            request=request,
            severity='security',
            entity={'type': 'user', 'label': user.get_full_name() or user.username},
            meta={'username': user.username, 'revoked_count': active_count},
        )
        return Response(
            {
                'message': _('Все сессии завершены.'),
                'revoked_count': active_count,
            },
            status=status.HTTP_200_OK,
        )
