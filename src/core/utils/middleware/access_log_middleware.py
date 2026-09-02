"""
Однострочный HTTP access-лог (единый для development и production).

Формат: "GET /api/foo/ HTTP/1.1" 200 12ms
Логгер: django.server, уровень INFO.
Встроенный access daphne/runserver отключён — источник только этот middleware.
"""

from __future__ import annotations

import logging
import time

from src.core.utils.request_id import get_request_id

logger = logging.getLogger('django.server')


def _silence_wsgi_runserver_access() -> None:
    """Отключает встроенный access Django WSGI runserver (fallback --noasgi)."""
    try:
        from django.core.servers.basehttp import WSGIRequestHandler
    except Exception:
        return
    if getattr(WSGIRequestHandler, '_ergo_access_silenced', False):
        return
    WSGIRequestHandler.log_message = lambda self, format, *args: None  # noqa: ARG005
    WSGIRequestHandler._ergo_access_silenced = True  # type: ignore[attr-defined]


class AccessLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        _silence_wsgi_runserver_access()

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        try:
            method = request.method or '-'
            # path без query — без риска утечки токенов из query string
            path = request.path or '/'
            proto = request.META.get('SERVER_PROTOCOL', 'HTTP/1.1')
            status = getattr(response, 'status_code', '-')
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            request_id = get_request_id() or '-'
            logger.info(
                '"%s %s %s" %s %dms request_id=%s',
                method,
                path,
                proto,
                status,
                elapsed_ms,
                request_id,
            )
        except Exception:
            # access-лог не должен ломать ответ
            pass
        return response
