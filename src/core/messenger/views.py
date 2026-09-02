import logging

from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from src.core.realtime.hub import RealtimeHub
from src.core.realtime.polling import apply_after_id
from src.core.realtime.topics import messenger_group, messenger_topic
from src.core.utils.mixins import SwaggerSafeMixin
from src.core.utils.permissions import ObjectPermissionMixin

from .models import Message, MessageAttachment
from .serializers import MessageAttachmentSerializer, MessageSerializer
from .utils import get_content_type

logger = logging.getLogger('core.messenger')


class MessageViewSet(ObjectPermissionMixin, SwaggerSafeMixin, viewsets.ModelViewSet):
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        if self.is_swagger_fake_view():
            return Message.objects.none()

        queryset = Message.objects.select_related(
            'author', 'content_type', 'reply_to', 'reply_to__author',
        ).prefetch_related('attachments')

        if self.kwargs.get('pk'):
            return queryset

        content_type_name = self.request.query_params.get('content_type')
        object_id = self.request.query_params.get('object_id')

        if content_type_name and object_id:
            from src.core.messenger.access import resolve_messenger_object_pk

            ct = get_content_type(content_type_name)
            if ct is None:
                return Message.objects.none()
            object_pk = resolve_messenger_object_pk(content_type_name, object_id)
            if object_pk is None:
                return Message.objects.none()
            if not self._has_messenger_access(ct, object_pk):
                return Message.objects.none()
            filtered = queryset.filter(content_type=ct, object_id=object_pk).order_by('created_at')
            return apply_after_id(filtered, self.request)

        return Message.objects.none()

    def _has_messenger_access(self, content_type, object_id):
        from src.core.messenger.access import has_messenger_access

        ct_name = self._get_ct_name_for_group(content_type)
        return has_messenger_access(self.request.user, ct_name, object_id)

    def check_object_permission(self, request, obj):
        return self._has_messenger_access(obj.content_type, obj.object_id)

    def get_object(self):
        """Проверяем доступ к родительскому объекту и при retrieve по pk (защита от IDOR)."""
        instance = super().get_object()
        if not self.check_object_permission(self.request, instance):
            raise PermissionDenied('Нет доступа к сообщению.')
        return instance

    def _check_author(self, instance):
        if instance.author != self.request.user:
            raise PermissionDenied('Вы можете изменять только свои сообщения.')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ct_name = serializer.validated_data.get('content_type_name')
        object_id = serializer.validated_data.get('object_id')
        if ct_name is not None and object_id is not None:
            ct = get_content_type(ct_name)
            if not self._has_messenger_access(ct, object_id):
                raise PermissionDenied('Нет доступа к чату.')
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        message = serializer.save(author=self.request.user)
        self._broadcast(message, 'new_message')

    def perform_update(self, serializer):
        self._check_author(serializer.instance)
        message = serializer.save(is_edited=True)
        self._broadcast(message, 'message_edited')

    def _get_ct_name_for_group(self, content_type):
        """Имя content_type для группы WebSocket (app_label.model)."""
        if not content_type:
            return ''
        return f'{content_type.app_label}.{content_type.model}'

    def perform_destroy(self, instance):
        self._check_author(instance)
        message_id = instance.id
        ct_name = self._get_ct_name_for_group(instance.content_type)
        object_id = instance.object_id
        instance.delete()
        self._broadcast_deleted(ct_name, object_id, message_id)

    def _broadcast(self, message, event_type):
        ct_name = self._get_ct_name_for_group(message.content_type)
        serialized = MessageSerializer(message, context={'request': self.request}).data
        group = messenger_group(ct_name, message.object_id)
        topic = messenger_topic(ct_name, message.object_id)
        payload = serialized if event_type != 'message_deleted' else message.id
        try:
            RealtimeHub.publish(
                group=group,
                topic=topic,
                event_type=event_type,
                payload=payload,
            )
        except Exception:
            logger.exception('Broadcast %s failed', event_type)

    def _broadcast_deleted(self, ct_name, object_id, message_id):
        group = messenger_group(ct_name, object_id)
        topic = messenger_topic(ct_name, object_id)
        try:
            RealtimeHub.publish(
                group=group,
                topic=topic,
                event_type='message_deleted',
                payload=message_id,
            )
        except Exception:
            logger.exception('Broadcast message_deleted failed')


class MessageAttachmentViewSet(ObjectPermissionMixin, SwaggerSafeMixin, viewsets.ModelViewSet):
    serializer_class = MessageAttachmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (JSONParser,)
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_queryset(self):
        if self.is_swagger_fake_view():
            return MessageAttachment.objects.none()

        queryset = MessageAttachment.objects.select_related('message').filter(
            message__author=self.request.user,
        )

        message_id = self.request.query_params.get('message')
        if message_id:
            queryset = queryset.filter(message_id=message_id)

        return queryset.order_by('created_at')

    def check_object_permission(self, request, obj):
        message = getattr(obj, 'message', None)
        if message is None:
            return False
        return message.author_id == getattr(request.user, 'pk', None)
