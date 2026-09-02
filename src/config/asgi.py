"""
Файл содержит конфигурацию для ASGI-приложения Django.

Он устанавливает переменную окружения DJANGO_SETTINGS_MODULE на 'src.config.patterns.development',
что указывает Django, какие настройки использовать для этого окружения. Затем создает ASGI-приложение
с помощью функции get_asgi_application из django.core.asgi.
"""

import os

from django.core.asgi import get_asgi_application

# celery_app: set_default() при import — до загрузки apps (@shared_task → не amqp).
from src.config.celery import celery_app as _celery_app  # noqa: F401
from src.config.celery import ensure_django_celery_configured
from src.config.deploy import get_settings_module

os.environ.setdefault('DJANGO_SETTINGS_MODULE', get_settings_module())

django_asgi_app = get_asgi_application()
ensure_django_celery_configured()

# После полной инициализации apps (не в AppConfig.ready — иначе RuntimeWarning).
from src.core.system.runtime_warmup import warmup_runtime_connections

warmup_runtime_connections()

from asgiref.sync import ThreadSensitiveContext

from src.core.utils.module_registry import is_slim_module_process


async def http_application(scope, receive, send):
    """Каждый HTTP-запрос — свой thread-sensitive executor (параллельные sync-вьюхи)."""
    async with ThreadSensitiveContext():
        await django_asgi_app(scope, receive, send)


if is_slim_module_process():
    application = http_application
else:
    from channels.auth import AuthMiddlewareStack
    from channels.routing import ProtocolTypeRouter, URLRouter
    from channels.security.websocket import AllowedHostsOriginValidator

    from src.core.cms.adp.routing import websocket_urlpatterns as adp_ws
    from src.core.messenger.routing import websocket_urlpatterns as messenger_ws
    from src.core.notifications.routing import websocket_urlpatterns as notifications_ws
    from src.core.realtime.transport import is_websocket_transport

    _protocol_apps = {
        "http": http_application,
    }
    if is_websocket_transport():
        _protocol_apps["websocket"] = AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                URLRouter(messenger_ws + notifications_ws + adp_ws)
            )
        )

    application = ProtocolTypeRouter(_protocol_apps)
