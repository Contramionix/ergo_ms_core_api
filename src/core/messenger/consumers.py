import logging

from channels.db import database_sync_to_async

from src.core.cms.adp.consumers.base import JwtMessageAuthConsumer, WsAuthRejectedError
from src.core.messenger.access import has_messenger_access, resolve_messenger_object_pk
from src.core.realtime.consumer_mixin import RealtimeEnvelopeConsumerMixin
from src.core.realtime.envelope import parse_envelope
from src.core.realtime.hub import RealtimeHub
from src.core.realtime.topics import messenger_group, messenger_topic

logger = logging.getLogger('core.messenger')


class MessengerConsumer(RealtimeEnvelopeConsumerMixin, JwtMessageAuthConsumer):
    """WebSocket consumer для мессенджера с JWT и проверкой доступа к room."""

    room_group_name: str | None = None
    _content_type_name: str | None = None
    _object_id: int | None = None

    async def on_ws_authenticated(self):
        self._content_type_name = self.scope['url_route']['kwargs']['content_type']
        object_ref = self.scope['url_route']['kwargs']['object_id']
        resolved_pk = await self._resolve_object_pk(self._content_type_name, object_ref)
        if resolved_pk is None:
            raise WsAuthRejectedError(4403)
        self._object_id = resolved_pk
        self.room_group_name = messenger_group(self._content_type_name, self._object_id)

        allowed = await self._check_access(
            self.ws_user,
            self._content_type_name,
            self._object_id,
        )
        if not allowed:
            raise WsAuthRejectedError(4403)

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

    async def on_ws_disconnect(self, close_code):
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive_authenticated_json(self, content, **kwargs):
        envelope = parse_envelope(content)
        if envelope is None or envelope.get('type') != 'typing_indicator':
            return
        if self.room_group_name is None:
            return
        if self._content_type_name is None or self._object_id is None:
            return

        payload = envelope.get('payload')
        if not isinstance(payload, dict):
            return

        await RealtimeHub.publish_async(
            self.channel_layer,
            group=self.room_group_name,
            topic=messenger_topic(self._content_type_name, self._object_id),
            event_type='typing_indicator',
            payload={
                'user_id': payload.get('user_id'),
                'username': payload.get('username', ''),
            },
        )

    @staticmethod
    @database_sync_to_async
    def _resolve_object_pk(content_type_name: str, object_ref) -> int | None:
        return resolve_messenger_object_pk(content_type_name, object_ref)

    @staticmethod
    @database_sync_to_async
    def _check_access(user, content_type_name: str, object_id: int) -> bool:
        return has_messenger_access(user, content_type_name, object_id)
