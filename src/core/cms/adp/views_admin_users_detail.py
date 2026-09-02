"""Профиль и аватар пользователя в админ-панели."""
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from src.core.audit.shortcuts import audit_log
from src.core.cms.adp.admin_users_common import (
    _AdminUserTargetMixin,
    _build_admin_user_detail,
    _get_user_avatar_url,
    _perform_admin_user_deletion,
    _validate_admin_user_deletion,
)
from src.core.cms.adp.models import UserProfile
from src.core.cms.adp.serializers import CMSUserSerializer, UpdateUserProfileSerializer
from src.core.settings.models import UserAvatar
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewGlobalAdminMixin
from src.core.utils.methods import parse_errors_to_dict
from src.core.utils.mixins import MediaApiFileMixin

User = get_user_model()


class AdminUserDetailView(_AdminUserTargetMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """
    Получение и обновление профиля пользователя администратором.
    """

    @swagger_auto_schema(
        operation_description="Получить полный профиль пользователя (для админ-панели)",
        responses={200: CMSUserSerializer()},
    )
    def get(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        UserProfile.objects.get_or_create(user=user)
        return Response(_build_admin_user_detail(user), status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_description="Обновить профиль пользователя (для админ-панели)",
        request_body=UpdateUserProfileSerializer,
        responses={200: CMSUserSerializer(), 400: 'Ошибка валидации данных.'},
    )
    def put(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        profile, _ = UserProfile.objects.get_or_create(user=user)
        serializer = UpdateUserProfileSerializer(profile, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            user = User.objects.select_related('adp_profile').get(pk=user.pk)
            audit_log('user.updated', request=request,
                   entity={'type': 'user', 'label': user.get_full_name() or user.username},
                   meta={'username': user.username})
            return Response(_build_admin_user_detail(user), status=status.HTTP_200_OK)

        errors = parse_errors_to_dict(serializer.errors)
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        operation_description="Удалить пользователя (для админ-панели)",
        responses={
            204: 'Пользователь удалён',
            400: 'Нельзя удалить пользователя',
            404: 'Пользователь не найден',
        },
    )
    def delete(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        validation_error = _validate_admin_user_deletion(request, user)
        if validation_error:
            return validation_error

        target_label = user.get_full_name() or user.username
        target_username = user.username
        deletion_error = _perform_admin_user_deletion(user)
        if deletion_error:
            return deletion_error

        audit_log('user.deleted', request=request, severity='security',
               entity={'type': 'user', 'label': target_label},
               meta={'username': target_username})
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminUserAvatarView(_AdminUserTargetMixin, MediaApiFileMixin, BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """
    Загрузка и удаление аватара пользователя администратором.
    """
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Загрузить или заменить аватар пользователя (для админ-панели)",
        responses={200: 'Аватар обновлён', 404: 'Пользователь не найден'},
    )
    def post(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref, select_related=False)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        file, file_path = self.get_file_or_path('image')
        if not file and not file_path:
            return Response(
                {'error': _('Файл изображения не передан')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        UserAvatar.objects.filter(user=user).delete()
        avatar = UserAvatar.objects.create(user=user)
        if file:
            avatar.image.save(file.name, file, save=False)
        elif file_path:
            self.assign_file_field(avatar, 'image', file_path=file_path)
        avatar.save()

        return Response(
            {'avatar_url': _get_user_avatar_url(user)},
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_description="Удалить аватар пользователя (для админ-панели)",
        responses={204: 'Аватар удалён', 404: 'Пользователь или аватар не найден'},
    )
    def delete(self, request, ref=None):
        user = self._resolve_target_user(request, ref=ref, select_related=False)
        if not user:
            return Response(
                {'error': _('Пользователь не найден')},
                status=status.HTTP_404_NOT_FOUND,
            )

        avatar = UserAvatar.objects.filter(user=user).first()
        if avatar:
            avatar.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(
            {'detail': _('Аватар не найден')},
            status=status.HTTP_404_NOT_FOUND,
        )
