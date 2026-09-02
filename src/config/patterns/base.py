"""
Файл определяющий базовые маршруты URL для Django-приложения и включает маршруты для документации API.

Он использует функцию `path` из `django.urls` для определения маршрутов и функцию `include`
для включения URL-конфигураций из других модулей. Также включает маршруты для документации API
с использованием библиотеки drf-spectacular и маршруты для статических файлов в режиме отладки.
"""

from django.conf.urls.static import static
from django.urls import (
    path,
    include
)
from django.conf import settings

from src.config.yasg import urlpatterns as yasg_pattern

urlpatterns = [
    path("api/", include("src.config.urls")),
    path("internal/", include("src.core.integrations.internal_urls")),
]

urlpatterns += yasg_pattern

# Если приложение запущено в режиме отладки, добавляем маршруты для статических файлов
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)