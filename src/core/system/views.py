from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.response import Response

from src.core.utils.base.base_views import BaseAPIViewPublicMixin
from src.core.utils.maintenance import is_maintenance_enabled, MAINTENANCE_DETAIL


class ReadyView(BaseAPIViewPublicMixin):
    @swagger_auto_schema(
        operation_description='Готовность API: PostgreSQL и cache (healthcheck оркестрации).',
        responses={
            200: openapi.Response('Ready'),
            503: openapi.Response('Not ready'),
        },
    )
    def get(self, request):
        checks = {'database': False, 'cache': False}

        try:
            from django.db import connections

            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT 1')
            checks['database'] = True
        except Exception:
            pass

        try:
            from django.core.cache import cache

            cache.set('__ergo_ready_check__', 1, timeout=1)
            checks['cache'] = cache.get('__ergo_ready_check__') == 1
        except Exception:
            pass

        ready = all(checks.values())
        payload = {'ready': ready}
        if not ready:
            payload['checks'] = checks
            return Response(payload, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(payload)


class MaintenanceStatusView(BaseAPIViewPublicMixin):
    @swagger_auto_schema(
        operation_description='Статус режима технических works (только чтение).',
        responses={200: openapi.Response('Maintenance status')},
    )
    def get(self, request):
        enabled = is_maintenance_enabled()
        payload = {'maintenance': enabled}
        if enabled:
            payload['detail'] = MAINTENANCE_DETAIL
        return Response(payload)
