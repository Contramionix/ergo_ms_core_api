"""Сбор websocket-маршрутов установленных Django-приложений.

Каждое приложение может объявить ``routing.websocket_urlpatterns``.
Имена модулей здесь не перечисляются.
"""

from __future__ import annotations

import logging
from importlib import import_module

from django.apps import apps

logger = logging.getLogger(__name__)


def discover_websocket_urlpatterns() -> list:
    patterns: list = []
    for config in apps.get_app_configs():
        try:
            module = import_module(f'{config.name}.routing')
        except ModuleNotFoundError:
            continue
        except Exception:
            logger.debug('websocket routing skip %s', config.name, exc_info=True)
            continue
        extra = getattr(module, 'websocket_urlpatterns', None)
        if extra:
            patterns.extend(list(extra))
    return patterns
