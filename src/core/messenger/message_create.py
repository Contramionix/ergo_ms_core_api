from __future__ import annotations

import logging
from typing import Any

from src.core.messenger.access import get_content_type_name
from src.core.messenger.models import Message
from src.core.messenger.serializers import MessageSerializer
from src.core.messenger.utils import get_content_type
from src.core.realtime.hub import RealtimeHub
from src.core.realtime.topics import messenger_group, messenger_topic

logger = logging.getLogger('core.messenger')

SEND_MESSAGE_TYPE = 'send_message'
SEND_MESSAGE_OK_TYPE = 'send_message_ok'
SEND_MESSAGE_ERROR_TYPE = 'send_message_error'
EDIT_MESSAGE_TYPE = 'edit_message'
EDIT_MESSAGE_OK_TYPE = 'edit_message_ok'
EDIT_MESSAGE_ERROR_TYPE = 'edit_message_error'
DELETE_MESSAGE_TYPE = 'delete_message'
DELETE_MESSAGE_OK_TYPE = 'delete_message_ok'
DELETE_MESSAGE_ERROR_TYPE = 'delete_message_error'


class MessageCreateError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def _first_error(errors: Any) -> str:
    if isinstance(errors, dict):
        for value in errors.values():
            return _first_error(value)
    if isinstance(errors, list) and errors:
        return _first_error(errors[0])
    return str(errors)


def serialize_message(message, *, request=None) -> dict:
    context = {'request': request} if request is not None else {}
    return MessageSerializer(message, context=context).data


def broadcast_message_event(message, event_type: str, *, request=None, serialized=None):
    ct_name = get_content_type_name(message.content_type)
    if event_type == 'message_deleted':
        payload = message.id
    else:
        payload = serialized if serialized is not None else serialize_message(
            message, request=request,
        )
        serialized = payload
    try:
        RealtimeHub.publish(
            group=messenger_group(ct_name, message.object_id),
            topic=messenger_topic(ct_name, message.object_id),
            event_type=event_type,
            payload=payload,
        )
    except Exception:
        logger.exception('Broadcast %s failed', event_type)
    return serialized


def save_new_message(serializer, *, author):
    return serializer.save(author=author)


def create_room_text_message(
    *,
    user,
    content_type_name: str,
    object_id: int,
    text: str,
    reply_to_id=None,
):
    """Создать текстовое сообщение в уже проверенной комнате. Без рассылки."""
    stripped = (text or '').strip()
    if not stripped:
        raise MessageCreateError('Текст сообщения не может быть пустым.')

    data = {
        'content_type_name': content_type_name,
        'object_id': object_id,
        'text': stripped,
        'message_type': 'user',
    }
    if reply_to_id not in (None, ''):
        data['reply_to'] = reply_to_id

    serializer = MessageSerializer(data=data)
    if not serializer.is_valid():
        raise MessageCreateError(_first_error(serializer.errors))

    reply = serializer.validated_data.get('reply_to')
    if reply is not None:
        ct = get_content_type(content_type_name)
        room_pk = serializer.validated_data['object_id']
        if reply.content_type_id != ct.id or reply.object_id != room_pk:
            raise MessageCreateError('Ответ должен относиться к этому чату.')

    message = save_new_message(serializer, author=user)
    return message, serialize_message(message)


def _parse_message_id(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise MessageCreateError('Некорректный идентификатор сообщения.') from None


def _owned_room_message(*, user, content_type_name: str, object_id: int, message_id):
    pk = _parse_message_id(message_id)
    try:
        message = Message.objects.select_related(
            'author', 'content_type', 'reply_to', 'reply_to__author',
        ).prefetch_related('attachments').get(pk=pk)
    except Message.DoesNotExist:
        raise MessageCreateError('Сообщение не найдено.') from None

    try:
        ct = get_content_type(content_type_name)
    except Exception:
        raise MessageCreateError('Сообщение не относится к этому чату.') from None

    if message.content_type_id != ct.id or message.object_id != object_id:
        raise MessageCreateError('Сообщение не относится к этому чату.')
    if message.author_id != getattr(user, 'pk', None):
        raise MessageCreateError('Вы можете изменять только свои сообщения.')
    return message


def edit_room_text_message(
    *,
    user,
    content_type_name: str,
    object_id: int,
    message_id,
    text,
):
    """Правка текста своего сообщения в уже проверенной комнате. Без рассылки."""
    message = _owned_room_message(
        user=user,
        content_type_name=content_type_name,
        object_id=object_id,
        message_id=message_id,
    )
    serializer = MessageSerializer(
        message,
        data={'text': '' if text is None else text},
        partial=True,
    )
    if not serializer.is_valid():
        raise MessageCreateError(_first_error(serializer.errors))
    message = serializer.save(is_edited=True)
    return message, serialize_message(message)


def delete_room_message(
    *,
    user,
    content_type_name: str,
    object_id: int,
    message_id,
):
    """Удаление своего сообщения в уже проверенной комнате. Без рассылки."""
    message = _owned_room_message(
        user=user,
        content_type_name=content_type_name,
        object_id=object_id,
        message_id=message_id,
    )
    deleted_id = message.id
    message.delete()
    return deleted_id
