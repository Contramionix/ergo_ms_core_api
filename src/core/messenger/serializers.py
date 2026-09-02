from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.utils.translation import gettext as _
from rest_framework import serializers

from src.core.utils.media_signing import get_signed_media_url

from .media_paths import validate_messenger_attachment_path
from .models import Message, MessageAttachment
from .utils import get_content_type

User = get_user_model()

MAX_ATTACHMENT_SIZE_MB = 25
MAX_ATTACHMENT_SIZE_BYTES = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024


class MessengerObjectIdField(serializers.Field):
    """pk или public_id снаружи; в БД хранится integer pk."""

    def to_internal_value(self, data):
        if isinstance(data, bool) or data in (None, ''):
            raise serializers.ValidationError(_('Некорректный идентификатор.'))
        return str(data).strip()

    def to_representation(self, value):
        return value


class MessageAttachmentSerializer(serializers.ModelSerializer):
    file_path = serializers.CharField(write_only=True, max_length=512)
    file_url = serializers.SerializerMethodField()
    original_filename = serializers.CharField(required=False, allow_blank=True, max_length=255)
    file_size = serializers.IntegerField(required=False, min_value=0)
    mime_type = serializers.CharField(required=False, allow_blank=True, max_length=128)

    class Meta:
        model = MessageAttachment
        fields = (
            'id',
            'message',
            'file_path',
            'file_url',
            'original_filename',
            'file_size',
            'mime_type',
            'created_at',
        )
        read_only_fields = ('id', 'file_url', 'created_at')

    def validate(self, attrs):
        attrs['file_path'] = validate_messenger_attachment_path(attrs['file_path'])
        name = (attrs.get('original_filename') or '').strip()
        if not name:
            name = attrs['file_path'].replace('\\', '/').split('/')[-1]
        attrs['original_filename'] = name

        size = int(attrs.get('file_size') or 0)
        if not size:
            path = attrs['file_path']
            size = default_storage.size(path) if default_storage.exists(path) else 0
        if size > MAX_ATTACHMENT_SIZE_BYTES:
            raise serializers.ValidationError(
                _('Размер файла не должен превышать %(count)d МБ')
                % {'count': MAX_ATTACHMENT_SIZE_MB}
            )
        attrs['file_size'] = size
        attrs['mime_type'] = (attrs.get('mime_type') or '')[:128]
        return attrs

    def get_file_url(self, obj):
        if not obj.file or not obj.file.name:
            return None
        return get_signed_media_url(obj.file.name)

    def create(self, validated_data):
        file_path = validated_data.pop('file_path')
        instance = MessageAttachment(
            message=validated_data['message'],
            original_filename=validated_data['original_filename'],
            file_size=validated_data['file_size'],
            mime_type=validated_data.get('mime_type') or '',
        )
        instance.file.name = file_path
        instance.save()
        return instance


class AuthorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    full_name = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)


class ReplyToSerializer(serializers.ModelSerializer):
    author_data = serializers.SerializerMethodField()
    text_preview = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ('id', 'text_preview', 'author_data')

    def get_text_preview(self, obj):
        t = (obj.text or '').strip()
        if not t:
            if obj.attachments.exists():
                return 'Вложение'
            return ''
        return t[:100] + ('...' if len(t) > 100 else '')

    def get_author_data(self, obj):
        if not obj.author:
            return None
        user = obj.author
        full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
        return {
            'id': user.id,
            'username': getattr(user, 'username', ''),
            'full_name': full_name or getattr(user, 'username', ''),
        }


class MessageSerializer(serializers.ModelSerializer):
    author_data = serializers.SerializerMethodField()
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    content_type_name = serializers.CharField(write_only=True, required=False)
    object_id = MessengerObjectIdField(required=False)
    reply_to = serializers.PrimaryKeyRelatedField(
        queryset=Message.objects.all(), required=False, allow_null=True,
    )
    reply_to_data = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = (
            'id',
            'content_type_name',
            'object_id',
            'author',
            'author_data',
            'text',
            'message_type',
            'is_edited',
            'reply_to',
            'reply_to_data',
            'attachments',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'author', 'author_data', 'is_edited', 'reply_to_data', 'attachments', 'created_at', 'updated_at')

    def validate_content_type_name(self, value):
        get_content_type(value)
        return value

    def validate(self, attrs):
        if not self.instance and not attrs.get('content_type_name'):
            raise serializers.ValidationError({'content_type_name': 'Обязательное поле при создании.'})
        if not self.instance and attrs.get('object_id') in (None, ''):
            raise serializers.ValidationError({'object_id': 'Обязательное поле при создании.'})
        if not self.instance:
            from src.core.messenger.access import resolve_messenger_object_pk

            object_pk = resolve_messenger_object_pk(
                attrs.get('content_type_name'),
                attrs.get('object_id'),
            )
            if object_pk is None:
                raise serializers.ValidationError({'object_id': _('Объект не найден.')})
            attrs['object_id'] = object_pk
        return attrs

    def get_author_data(self, obj):
        if not obj.author:
            return None
        user = obj.author
        full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
        avatar_url = None
        request = self.context.get('request')
        try:
            if hasattr(user, 'avatar') and user.avatar and user.avatar.image:
                avatar_url = user.avatar.image.url
        except Exception:
            pass
        return {
            'id': user.id,
            'public_id': str(user.public_id) if getattr(user, 'public_id', None) else None,
            'username': getattr(user, 'username', ''),
            'full_name': full_name or getattr(user, 'username', ''),
            'avatar_url': avatar_url,
        }

    def get_reply_to_data(self, obj):
        if not obj.reply_to_id:
            return None
        return ReplyToSerializer(obj.reply_to).data

    def create(self, validated_data):
        content_type_name = validated_data.pop('content_type_name')
        ct = get_content_type(content_type_name)
        validated_data['content_type'] = ct
        return super().create(validated_data)
