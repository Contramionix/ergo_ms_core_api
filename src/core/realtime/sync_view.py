from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi

from src.core.cms.adp.services import presence as presence_service
from src.core.notifications.models import Notification
from src.core.notifications.serializers import NotificationSerializer
from src.core.notifications.services import NotificationService
from src.core.cms.adp.services.permissions import PermissionService
from src.core.realtime.polling import parse_after_id_value
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewAuthMixin


class RealtimeSyncView(BaseAPIViewAuthMixin, BaseAPIView):
    """Сводный polling-эндпоинт: один запрос вместо нескольких."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Сводный sync для http_polling: уведомления + опционально presence heartbeat.',
        manual_parameters=[
            openapi.Parameter(
                'notifications_after_id',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                'presence_heartbeat',
                openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                'presence_admin_snapshot',
                openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
        ],
        responses={200: openapi.Response('Sync payload')},
    )
    def get(self, request):
        user = request.user
        unread_count = NotificationService.unread_count(user)

        notifications_after_id = request.query_params.get('notifications_after_id')
        notifications = []
        latest_notification_id = None
        visible_qs = Notification.objects.filter(recipient=user, in_app_visible=True)
        if notifications_after_id is not None:
            after_id = parse_after_id_value(notifications_after_id)
            if after_id is not None:
                qs = visible_qs.filter(id__gt=after_id).order_by('id')[:50]
                notifications = NotificationSerializer(qs, many=True).data
        else:
            latest_notification_id = visible_qs.order_by('-id').values_list('id', flat=True).first() or 0

        presence_ok = None
        if str(request.query_params.get('presence_heartbeat', '')).lower() in ('1', 'true', 'yes'):
            entry = presence_service.http_heartbeat(user.pk)
            presence_ok = {
                'is_online': entry.is_online,
                'last_seen': entry.last_seen.isoformat() if entry.last_seen else None,
            }

        admin_presence = None
        if str(request.query_params.get('presence_admin_snapshot', '')).lower() in ('1', 'true', 'yes'):
            if PermissionService.can_manage_users_as_global_admin(user):
                admin_presence = {'users': presence_service.build_presence_snapshot()}

        changed = bool(notifications) or unread_count > 0 or admin_presence is not None
        payload = {
            'changed': changed,
            'unread_count': unread_count,
            'notifications': notifications,
            'presence': presence_ok,
        }
        if latest_notification_id is not None:
            payload['latest_notification_id'] = latest_notification_id
        if admin_presence is not None:
            payload['admin_presence'] = admin_presence
        return Response(payload)
