import json

from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils.translation import gettext as _
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.response import Response

from src.core.realtime.renderers import EventStreamRenderer
from src.core.realtime.stream import sse_event_stream
from src.core.realtime.subscriptions import subscribe_topic, unsubscribe_topic
from src.core.utils.base.base_views import BaseAPIViewAuthMixin

SSE_HEADERS = {
    'Cache-Control': 'no-cache',
    'X-Accel-Buffering': 'no',
    'Connection': 'keep-alive',
}


class RealtimeConfigView(BaseAPIViewAuthMixin):
    @swagger_auto_schema(
        operation_description='Конфигурация realtime-транспорта и capabilities.',
        responses={200: openapi.Response('Realtime config')},
    )
    def get(self, request):
        return Response({
            'transport': getattr(settings, 'REALTIME_TRANSPORT', 'websocket'),
            'capabilities': getattr(settings, 'REALTIME_CAPABILITIES', {}),
            'sse_keepalive_interval': getattr(settings, 'REALTIME_SSE_KEEPALIVE_INTERVAL', 25),
            'poll_intervals': {
                'presence': getattr(settings, 'REALTIME_POLL_PRESENCE_INTERVAL', 45),
                'notifications': getattr(settings, 'REALTIME_POLL_NOTIFICATIONS_INTERVAL', 15),
                'admin_presence': getattr(settings, 'REALTIME_POLL_ADMIN_PRESENCE_INTERVAL', 10),
                'messenger': getattr(settings, 'REALTIME_POLL_MESSENGER_INTERVAL', 5),
            },
        })


class RealtimeStreamView(BaseAPIViewAuthMixin):
    renderer_classes = [EventStreamRenderer]

    @swagger_auto_schema(
        operation_description='SSE-поток realtime-событий (server → client).',
        responses={200: 'text/event-stream'},
    )
    def get(self, request):
        if getattr(settings, 'REALTIME_TRANSPORT', 'websocket') != 'sse':
            return Response(
                {'detail': 'SSE stream доступен только при REALTIME_TRANSPORT=sse.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        async def async_iterator():
            async for chunk in sse_event_stream(request.user):
                yield chunk.encode('utf-8')

        response = StreamingHttpResponse(
            streaming_content=async_iterator(),
            content_type='text/event-stream; charset=utf-8',
        )
        for key, value in SSE_HEADERS.items():
            response[key] = value
        return response


class RealtimeSubscriptionView(BaseAPIViewAuthMixin):
    @swagger_auto_schema(
        operation_description='Подписка или отписка SSE-потока от topic.',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['action', 'topic'],
            properties={
                'action': openapi.Schema(type=openapi.TYPE_STRING, enum=['subscribe', 'unsubscribe']),
                'topic': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={200: openapi.Response('Subscription result')},
    )
    def post(self, request):
        action = str(request.data.get('action', '')).strip().lower()
        topic = str(request.data.get('topic', '')).strip()
        if action not in ('subscribe', 'unsubscribe') or not topic:
            return Response(
                {'detail': _('Укажите action (subscribe|unsubscribe) и topic.')},
                status=400,
            )

        if action == 'subscribe':
            ok, group = subscribe_topic(request.user, topic)
        else:
            ok, group = unsubscribe_topic(request.user, topic)

        if not ok or group is None:
            return Response(
                {'detail': _('Нет доступа к topic или topic не распознан.')},
                status=403,
            )

        return Response({
            'action': action,
            'topic': topic,
            'group': group,
            'reconnect_stream': False,
        })
