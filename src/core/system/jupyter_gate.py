"""Шлюз nginx auth_request для Jupyter: только глобальный администратор."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.settings import api_settings

from src.core.cms.adp.auth_cookies import get_refresh_token_from_request
from src.core.cms.adp.services.permissions import PermissionService
from src.core.cms.adp.services.session_devices import (
    _payload_from_refresh_string,
    is_device_session_active,
)
from src.core.utils.base.base_views import BaseAPIViewPublicMixin


def resolve_jupyter_gate_user(request):
    """Пользователь для auth_request: Bearer или подписанный refresh-cookie."""
    User = get_user_model()
    request_user = getattr(request, 'user', None)
    if request_user is not None and getattr(request_user, 'is_authenticated', False):
        if isinstance(request_user, AbstractBaseUser):
            return request_user if request_user.is_active else None
        pk = getattr(request_user, 'pk', None) or getattr(request_user, 'id', None)
        public_id = getattr(request_user, 'public_id', None)
        user = User.objects.filter(pk=pk).first() if pk else None
        if user is None and public_id:
            user = User.objects.filter(public_id=public_id).first()
        if user is None or not user.is_active:
            return None
        return user

    payload = _payload_from_refresh_string(get_refresh_token_from_request(request))
    if not payload:
        return None
    user_id = payload.get(api_settings.USER_ID_CLAIM)
    if user_id is None:
        return None
    user = User.objects.filter(pk=user_id).first()
    if user is None or not user.is_active:
        return None
    if not is_device_session_active(user, payload.get('device_id')):
        return None
    return user


class JupyterAccessView(BaseAPIViewPublicMixin):
    """GET 200/401 для nginx auth_request. Прямой браузерный заход режет nginx (internal)."""

    # Jupyter тянет много ассетов; лимит гостя на шлюзе сломал бы сессию.
    throttle_classes = []

    def get(self, request):
        user = resolve_jupyter_gate_user(request)
        if user is None or not PermissionService.is_admin(user):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        return Response(status=status.HTTP_200_OK)
