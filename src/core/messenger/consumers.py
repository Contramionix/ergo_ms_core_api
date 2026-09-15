import logging

from channels.db import database_sync_to_async

from src.core.cms.adp.consumers.base import JwtMessageAuthConsumer, WsAuthRejectedError
from src.core.messenger.access import has_messenger_access, resolve_messenger_object_pk
from src.core.messenger.message_create import (
    DELETE_MESSAGE_ERROR_TYPE,
    DELETE_MESSAGE_OK_TYPE,
    DELETE_MESSAGE_TYPE,
    EDIT_MESSAGE_ERROR_TYPE,
    EDIT_MESSAGE_OK_TYPE,
    EDIT_MESSAGE_TYPE,
    SEND_MESSAGE_ERROR_TYPE,
    SEND_MESSAGE_OK_TYPE,
    SEND_MESSAGE_TYPE,
    MessageCreateError,
    create_room_text_message,
    delete_room_message,
    edit_room_text_message,
)
from src.core.realtime.consumer_mixin import RealtimeEnvelopeConsumerMixin
from src.core.realtime.envelope import build_envelope, parse_envelope
from src.core.realtime.hub import RealtimeHub
from src.core.realtime.topics import messenger_group, messenger_topic

logger = logging.getLogger('core.messenger')

TYPING_TYPE = 'typing_indicator'


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
        if envelope is None:
            return
        event_type = envelope.get('type')
        if event_type == TYPING_TYPE:
            await self._handle_typing(envelope)
            return
        if event_type == SEND_MESSAGE_TYPE:
            await self._handle_send_message(envelope)
            return
        if event_type == EDIT_MESSAGE_TYPE:
            await self._handle_edit_message(envelope)
            return
        if event_type == DELETE_MESSAGE_TYPE:
            await self._handle_delete_message(envelope)

    async def _handle_typing(self, envelope):
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
            event_type=TYPING_TYPE,
            payload={
                'user_id': payload.get('user_id'),
                'username': payload.get('username', ''),
            },
        )

    def _room_ready(self) -> bool:
        return (
            self.room_group_name is not None
            and self._content_type_name is not None
            and self._object_id is not None
        )

    async def _require_room_payload(self, envelope, error_type: str):
        request_id = envelope.get('id') or ''
        if not self._room_ready():
            await self._send_result(
                error_type,
                request_id,
                {'detail': 'Комната недоступна.'},
            )
            return None
        payload = envelope.get('payload')
        if not isinstance(payload, dict):
            await self._send_result(
                error_type,
                request_id,
                {'detail': 'Некорректное сообщение.'},
            )
            return None
        return request_id, payload

    async def _handle_send_message(self, envelope):
        parsed = await self._require_room_payload(envelope, SEND_MESSAGE_ERROR_TYPE)
        if parsed is None:
            return
        request_id, payload = parsed

        try:
            _message, serialized = await self._create_room_message(
                self.ws_user,
                self._content_type_name,
                self._object_id,
                payload.get('text'),
                payload.get('reply_to'),
            )
        except MessageCreateError as exc:
            await self._send_result(
                SEND_MESSAGE_ERROR_TYPE,
                request_id,
                {'detail': exc.detail},
            )
            return
        except Exception:
            logger.exception('send_message failed')
            await self._send_result(
                SEND_MESSAGE_ERROR_TYPE,
                request_id,
                {'detail': 'Не удалось отправить сообщение.'},
            )
            return

        await self._send_result(
            SEND_MESSAGE_OK_TYPE,
            request_id,
            {'message': serialized},
        )
        await self._publish_event('new_message', serialized)

    async def _handle_edit_message(self, envelope):
        parsed = await self._require_room_payload(envelope, EDIT_MESSAGE_ERROR_TYPE)
        if parsed is None:
            return
        request_id, payload = parsed

        try:
            _message, serialized = await self._edit_room_message(
                self.ws_user,
                self._content_type_name,
                self._object_id,
                payload.get('id'),
                payload.get('text'),
            )
        except MessageCreateError as exc:
            await self._send_result(
                EDIT_MESSAGE_ERROR_TYPE,
                request_id,
                {'detail': exc.detail},
            )
            return
        except Exception:
            logger.exception('edit_message failed')
            await self._send_result(
                EDIT_MESSAGE_ERROR_TYPE,
                request_id,
                {'detail': 'Не удалось изменить сообщение.'},
            )
            return

        await self._send_result(
            EDIT_MESSAGE_OK_TYPE,
            request_id,
            {'message': serialized},
        )
        await self._publish_event('message_edited', serialized)

    async def _handle_delete_message(self, envelope):
        parsed = await self._require_room_payload(envelope, DELETE_MESSAGE_ERROR_TYPE)
        if parsed is None:
            return
        request_id, payload = parsed

        try:
            message_id = await self._delete_room_message(
                self.ws_user,
                self._content_type_name,
                self._object_id,
                payload.get('id'),
            )
        except MessageCreateError as exc:
            await self._send_result(
                DELETE_MESSAGE_ERROR_TYPE,
                request_id,
                {'detail': exc.detail},
            )
            return
        except Exception:
            logger.exception('delete_message failed')
            await self._send_result(
                DELETE_MESSAGE_ERROR_TYPE,
                request_id,
                {'detail': 'Не удалось удалить сообщение.'},
            )
            return

        await self._send_result(
            DELETE_MESSAGE_OK_TYPE,
            request_id,
            {'message_id': message_id},
        )
        await self._publish_event('message_deleted', message_id)

    async def _publish_event(self, event_type: str, payload):
        await RealtimeHub.publish_async(
            self.channel_layer,
            group=self.room_group_name,
            topic=messenger_topic(self._content_type_name, self._object_id),
            event_type=event_type,
            payload=payload,
        )

    async def _send_result(self, event_type: str, request_id: str, extra: dict):
        payload = {'request_id': request_id, **extra}
        await self.send_json(build_envelope(
            topic=messenger_topic(self._content_type_name or '', self._object_id or 0),
            event_type=event_type,
            payload=payload,
        ))

    @staticmethod
    @database_sync_to_async
    def _create_room_message(user, content_type_name, object_id, text, reply_to_id):
        return create_room_text_message(
            user=user,
            content_type_name=content_type_name,
            object_id=object_id,
            text=text,
            reply_to_id=reply_to_id,
        )

    @staticmethod
    @database_sync_to_async
    def _edit_room_message(user, content_type_name, object_id, message_id, text):
        return edit_room_text_message(
            user=user,
            content_type_name=content_type_name,
            object_id=object_id,
            message_id=message_id,
            text=text,
        )

    @staticmethod
    @database_sync_to_async
    def _delete_room_message(user, content_type_name, object_id, message_id):
        return delete_room_message(
            user=user,
            content_type_name=content_type_name,
            object_id=object_id,
            message_id=message_id,
        )

    @staticmethod
    @database_sync_to_async
    def _resolve_object_pk(content_type_name: str, object_ref) -> int | None:
        return resolve_messenger_object_pk(content_type_name, object_ref)

    @staticmethod
    @database_sync_to_async
    def _check_access(user, content_type_name: str, object_id: int) -> bool:
        return has_messenger_access(user, content_type_name, object_id)
