"""Блокировка API при включённом режиме технических works."""

from django.http import JsonResponse

from src.core.utils.maintenance import (
    MAINTENANCE_DETAIL,
    is_maintenance_enabled,
    is_maintenance_exempt_request,
)


class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_maintenance_enabled() and not is_maintenance_exempt_request(request.path):
            response = JsonResponse(
                {'code': 'maintenance', 'detail': MAINTENANCE_DETAIL},
                status=503,
            )
            response['X-Maintenance-Mode'] = '1'
            response['Retry-After'] = '3600'
            return response
        return self.get_response(request)
