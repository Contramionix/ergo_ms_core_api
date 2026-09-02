"""Сброс пароля и статус учётной записи в админ-панели."""
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.response import Response

from src.core.audit.shortcuts import audit_log
from src.core.cms.adp.admin_users_common import (
    _AdminUserTargetMixin,
    _apply_system_password_reset,
    _build_admin_user_detail,
    _is_manual_password_reset_request,
    _set_admin_user_active,
    _validate_admin_user_suspend,
)
from src.core.cms.adp.serializers import AdminResetUserPasswordSerializer, CMSUserSerializer
from src.core.cms.adp.services.user_deletion import revoke_user_auth
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewGlobalAdminMixin
from src.core.utils.methods import parse_errors_to_dict, send_admin_password_reset_notification

User = get_user_model()


class AdminUserResetPasswordView(_AdminUserTargetMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """
    Сброс пароля пользователя администратором.
    Production: случайный пароль + уведомление на email.
    Development: ручная установка пароля при передаче new_password и confirm_password.
    """

    @swagger_auto_schema(
        operation_description="Сбросить пароль пользователя (админ-панель)",
        request_body=AdminResetUserPasswordSerializer,
        responses={200: 'Пароль сброшен или установлен'},
    )
    def post(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref, select_related=False)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.user.pk == user.pk:
            return Response(
                {'error': _('Нельзя сбросить пароль собственной учётной записи через эту форму.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if _is_manual_password_reset_request(request):
            serializer = AdminResetUserPasswordSerializer(data=request.data)
            if not serializer.is_valid():
                errors = parse_errors_to_dict(serializer.errors)
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)

            user.set_password(serializer.validated_data['new_password'])
            user.save(update_fields=['password'])
            revoke_user_auth(user)

            audit_log('user.password_reset_by_admin', request=request, severity='security',
                   entity={'type': 'user', 'label': user.get_full_name() or user.username},
                   meta={'username': user.username, 'mode': 'manual'})
            return Response(
                {
                    'message': _('Пароль установлен.'),
                    'mode': 'manual',
                },
                status=status.HTTP_200_OK,
            )

        email = (user.email or '').strip()

        _apply_system_password_reset(user)
        revoke_user_auth(user)

        audit_log('user.password_reset_by_admin', request=request, severity='security',
               entity={'type': 'user', 'label': user.get_full_name() or user.username},
               meta={'username': user.username, 'mode': 'system'})

        email_sent = False
        email_error = None
        if email:
            email_sent, email_error = send_admin_password_reset_notification(email)
        else:
            email_error = _('У пользователя не указан email — уведомление не отправлено.')

        response_data = {
            'message': _('Пароль сброшен.'),
            'mode': 'system',
            'email_sent': email_sent,
        }
        if email_sent:
            response_data['message'] = _(
                'Пароль сброшен. Пользователю отправлено уведомление.'
            )
        else:
            response_data['warning'] = (
                email_error or _('Не удалось отправить уведомление на email пользователя.')
            )

        return Response(response_data, status=status.HTTP_200_OK)


class AdminUserStatusView(_AdminUserTargetMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Приостановка и возобновление учётной записи пользователя."""

    @swagger_auto_schema(
        operation_description=(
            "Установить статус аккаунта (is_active). "
            "При приостановке все сессии пользователя завершаются."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['is_active'],
            properties={
                'is_active': openapi.Schema(
                    type=openapi.TYPE_BOOLEAN,
                    description='true — возобновить, false — приостановить',
                ),
            },
        ),
        responses={200: CMSUserSerializer(), 400: 'Нельзя изменить статус'},
    )
    def post(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref, select_related=False)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        if 'is_active' not in request.data:
            return Response(
                {'error': _('Поле is_active обязательно.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw = request.data.get('is_active')
        if isinstance(raw, bool):
            is_active = raw
        elif isinstance(raw, str) and raw.strip().lower() in ('true', '1', 'false', '0'):
            is_active = raw.strip().lower() in ('true', '1')
        else:
            return Response(
                {'error': _('Поле is_active должно быть булевым значением.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not is_active:
            validation_error = _validate_admin_user_suspend(request, user)
            if validation_error:
                return validation_error

        previous = bool(user.is_active)
        _set_admin_user_active(user, is_active=is_active)
        user = User.objects.select_related('adp_profile').get(pk=user.pk)

        if previous != is_active:
            audit_log(
                'user.activated' if is_active else 'user.suspended',
                request=request,
                severity='security',
                entity={'type': 'user', 'label': user.get_full_name() or user.username},
                meta={'username': user.username, 'is_active': is_active},
            )

        return Response(_build_admin_user_detail(user), status=status.HTTP_200_OK)
