"""
Базовые классы представлений API.
"""

from rest_framework.views import APIView
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.permissions import AllowAny, IsAuthenticated

from src.core.settings.permissions import IsGlobalAdmin
from src.core.utils.mixins import SwaggerSafeMixin


class AuthenticatedAPIMixin(SwaggerSafeMixin):
    """JWT обязателен, throttle как у DRF defaults, безопасный user/queryset."""

    throttle_classes = [AnonRateThrottle, UserRateThrottle]
    permission_classes = [IsAuthenticated]


class BaseAPIView(AuthenticatedAPIMixin, APIView):
    """
    Базовый класс API-представлений: токен обязателен.

    Включает ограничение частоты запросов. permission_classes —
    IsAuthenticated (как DEFAULT_PERMISSION_CLASSES). Публичный доступ
    только через BaseAPIViewPublicMixin.

    Аутентификация не переопределяется — действует DEFAULT_AUTHENTICATION_CLASSES
    (DeviceBoundJWTAuthentication), поэтому Bearer-токен с отозванной сессией
    устройства отклоняется и на анонимных по permission_classes представлениях.
    """


class BaseAPIViewPublicMixin(BaseAPIView):
    """
    Явный публичный доступ (login, health, reset, logout).

    Новый анонимный эндпоинт ядра обязан наследовать этот миксин
    и быть в core_anonymous_allowlist.yaml.
    """
    permission_classes = [AllowAny]


class BaseAPIViewAuthMixin(BaseAPIView):
    """
    Алиас BaseAPIView: токен обязателен.

    Сохранён для существующих call-site; новые представления могут
    наследовать BaseAPIView напрямую.
    """
    permission_classes = [IsAuthenticated]


class BaseAPIViewGlobalAdminMixin(BaseAPIViewAuthMixin):
    """API представления, доступные только глобальному администратору."""
    permission_classes = [IsAuthenticated, IsGlobalAdmin]
