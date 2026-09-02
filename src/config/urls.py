"""
Файл для определения маршрутов URL для Django-API-приложения.

Он использует функцию `path` из `django.urls` для определения маршрутов и функцию `include`
для включения URL-конфигураций из других модулей. Список маршрутов ядра и модулей
берётся из файлового кэша (как discovered_apps); include() остаётся ленивым.
"""

from django.urls import include, path

from src.core.utils.auto_api.discovered_urls_cache import (
    get_discovered_url_entries,
    iter_top_level_module_prefixes,
)
from src.core.utils.media_views import MediaUploadTokenView
from src.core.utils.module_registry import is_slim_module_process

urlpatterns = []
if not is_slim_module_process():
    from src.core.system.jupyter_gate import JupyterAccessView

    urlpatterns.append(
        path('internal/jupyter-access/', JupyterAccessView.as_view(), name='jupyter-access'),
    )

discovered = get_discovered_url_entries()
for route, dotted_module in discovered:
    urlpatterns.append(path(route, include(dotted_module)))

# Токен загрузки на процессе модуля: nginx /api/<name>/ остаётся на этом хосте,
# upload_url и HMAC берутся из местного media_api, а не с ядра.
for module_name in iter_top_level_module_prefixes(discovered):
    urlpatterns.append(
        path(
            f'{module_name}/media/upload-token/',
            MediaUploadTokenView.as_view(),
            name=f'{module_name}-media-upload-token',
        )
    )
