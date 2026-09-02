"""Централизованный ACL API по Policy(policy_type='api')."""

from __future__ import annotations

import logging

from django.http import JsonResponse
from django.utils.translation import gettext as _
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger('api')

API_PREFIX = '/api/'

# Bootstrap / auth — иначе цикл при логине и проверке прав.
_EXEMPT_PREFIXES = (
    '/api/cms/adp/authorization/',
    '/api/cms/adp/registration/',
    '/api/cms/adp/validate-registration/',
    '/api/cms/adp/token-refresh/',
    '/api/cms/adp/logout/',
    '/api/cms/adp/session-bootstrap/',
    '/api/cms/adp/my-permissions/',
    '/api/cms/adp/check-url-access/',
    '/api/cms/adp/send-code/',
    '/api/cms/adp/verify-code/',
    '/api/cms/adp/reset-password/',
    '/api/cms/adp/password-reset-settings/',
    '/api/cms/adp/registration-settings/',
    '/api/cms/adp/invitations/validate/',
    '/api/cms/adp/profile-settings/',
    '/api/cms/adp/dev-tools/',
    '/api/system/ready/',
    '/api/system/maintenance-status/',
)

_EXEMPT_EXACT = frozenset({
    '/api/cms/adp/authorization',
    '/api/cms/adp/token-refresh',
    '/api/cms/adp/session-bootstrap',
    '/api/cms/adp/my-permissions',
    '/api/cms/adp/check-url-access',
    '/api/system/ready',
    '/api/system/maintenance-status',
})


def _normalize_path(path: str) -> str:
    value = (path or '').split('?', 1)[0]
    if not value.startswith('/'):
        value = f'/{value}'
    while '//' in value:
        value = value.replace('//', '/')
    return value


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    for prefix in _EXEMPT_PREFIXES:
        if path == prefix.rstrip('/') or path.startswith(prefix):
            return True
    # Swagger / ReDoc под /api/ (если есть)
    if path.startswith('/api/swagger') or path.startswith('/api/redoc') or path.startswith('/api/schema'):
        return True
    if path.startswith('/swagger') or path.startswith('/redoc'):
        return True
    return False


def _resolve_authenticated_user(request):
    """Session-user или JWT Bearer (DRF auth на view ещё не выполнялся)."""
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return user

    header = request.META.get('HTTP_AUTHORIZATION') or ''
    if not isinstance(header, str) or not header.startswith('Bearer '):
        return None

    from src.core.cms.adp.authentication import DeviceBoundJWTAuthentication

    authenticator = DeviceBoundJWTAuthentication()
    try:
        result = authenticator.authenticate(request)
    except AuthenticationFailed:
        return None
    except Exception:
        logger.debug('ApiAccessPolicy: не удалось аутентифицировать JWT', exc_info=True)
        return None

    if not result:
        return None
    from src.core.cms.adp.authentication import REQUEST_JWT_AUTH_ATTR

    setattr(request, REQUEST_JWT_AUTH_ATTR, result)
    return result[0]


class ApiAccessPolicyMiddleware:
    """
    Применяет Policy(policy_type='api') к запросам под /api/.

    Anonymous — пропуск (решает DRF). Deny → 403 JSON.
    Allow / default-allow не обходит permission_classes view.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'OPTIONS':
            return self.get_response(request)

        path = _normalize_path(getattr(request, 'path', '') or '')
        if not path.startswith(API_PREFIX) or _is_exempt(path):
            return self.get_response(request)

        user = _resolve_authenticated_user(request)
        if user is None:
            return self.get_response(request)

        from src.core.cms.adp.services.permissions import PermissionService

        if PermissionService.check_api_access(user, path):
            return self.get_response(request)

        return JsonResponse(
            {'detail': _('Доступ к точке запроса запрещён политикой.')},
            status=403,
        )
