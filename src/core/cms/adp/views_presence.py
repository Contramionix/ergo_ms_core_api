from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from src.core.cms.adp.serializers import (
    UserPresenceBatchResponseSerializer,
    UserPresenceEntrySerializer,
)
from src.core.cms.adp.services import presence as presence_service
from src.core.cms.adp.services.permissions import PermissionService
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewAuthMixin


class UserPresenceBatchView(BaseAPIViewAuthMixin, BaseAPIView):
    """Batch-запрос онлайн-статуса пользователей для UI (аватарки, списки и т.д.)."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Получить онлайн-статус пользователей по списку public_id (UUID).',
        manual_parameters=[
            openapi.Parameter(
                'public_ids',
                openapi.IN_QUERY,
                description='public_id пользователей через запятую (максимум 100)',
                type=openapi.TYPE_STRING,
                required=True,
            ),
        ],
        responses={200: UserPresenceBatchResponseSerializer()},
    )
    def get(self, request):
        public_ids = presence_service.parse_public_ids_param(
            request.query_params.get('public_ids'),
        )
        if not public_ids:
            return Response({'presence': {}})

        presence_map = presence_service.get_presence_map_by_public_ids(public_ids)
        return Response({
            'presence': presence_service.serialize_presence_map(presence_map),
        })


class UserPresenceHeartbeatView(BaseAPIViewAuthMixin, BaseAPIView):
    """HTTP polling: heartbeat онлайн-сессии (аналог WS connect + ping)."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Поддержать онлайн-статус текущего пользователя (режим http_polling).',
        responses={200: UserPresenceEntrySerializer()},
    )
    def post(self, request):
        entry = presence_service.http_heartbeat(request.user.pk)
        return Response(presence_service.serialize_presence_entry(entry))


class UserPresenceOfflineView(BaseAPIViewAuthMixin, BaseAPIView):
    """HTTP polling: завершение онлайн-сессии (аналог WS disconnect)."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Снять онлайн-статус текущего пользователя (режим http_polling).',
        responses={200: UserPresenceEntrySerializer()},
    )
    def post(self, request):
        entry = presence_service.http_offline(request.user.pk)
        return Response(presence_service.serialize_presence_entry(entry))


class UserPresenceAdminSnapshotView(BaseAPIViewAuthMixin, BaseAPIView):
    """HTTP polling: snapshot presence для глобального админа."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description='Список онлайн-статусов всех пользователей (глобальный админ).',
        responses={200: openapi.Response(
            description='Snapshot presence',
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'users': openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_OBJECT),
                    ),
                },
            ),
        )},
    )
    def get(self, request):
        if not PermissionService.can_manage_users_as_global_admin(request.user):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'users': presence_service.build_presence_snapshot(),
        })
