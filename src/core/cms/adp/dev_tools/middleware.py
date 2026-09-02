"""Подмешивает overlay прав админа-разработчика в ContextVar на время запроса."""

from __future__ import annotations

from src.core.cms.adp.dev_tools.preview import (
    load_preview,
    reset_active_preview,
    set_active_preview,
)
from src.core.cms.adp.dev_tools.runtime import is_dev_tools_enabled

DEV_TOOLS_PATH_PREFIX = '/api/cms/adp/dev-tools/'


def _normalize_path(path: str) -> str:
    value = (path or '').split('?', 1)[0]
    if not value.startswith('/'):
        value = f'/{value}'
    while '//' in value:
        value = value.replace('//', '/')
    return value


def _is_dev_tools_path(path: str) -> bool:
    normalized = _normalize_path(path)
    return (
        normalized == DEV_TOOLS_PATH_PREFIX.rstrip('/')
        or normalized.startswith(DEV_TOOLS_PATH_PREFIX)
    )


def _resolve_user(request):
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return user

    header = request.META.get('HTTP_AUTHORIZATION') or ''
    if not isinstance(header, str) or not header.startswith('Bearer '):
        return None

    from rest_framework.exceptions import AuthenticationFailed

    from src.core.cms.adp.authentication import (
        REQUEST_JWT_AUTH_ATTR,
        DeviceBoundJWTAuthentication,
    )

    authenticator = DeviceBoundJWTAuthentication()
    try:
        result = authenticator.authenticate(request)
    except AuthenticationFailed:
        return None
    except Exception:
        return None
    if not result:
        return None
    setattr(request, REQUEST_JWT_AUTH_ATTR, result)
    return result[0]


class DevToolsPreviewMiddleware:
    """
    Для глобального админа при включённом ERGO_DEV_TOOLS читает overlay
    из кэша и кладёт его в ContextVar. Эндпоинты /dev-tools/ overlay не видят,
    чтобы панель сама могла менять режим.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_dev_tools_enabled() or _is_dev_tools_path(getattr(request, 'path', '') or ''):
            return self.get_response(request)

        user = _resolve_user(request)
        preview = load_preview(user) if user is not None else None
        token = set_active_preview(preview)
        try:
            return self.get_response(request)
        finally:
            reset_active_preview(token)
