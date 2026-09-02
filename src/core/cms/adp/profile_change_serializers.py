from rest_framework.serializers import (
    CharField,
    EmailField,
    ModelSerializer,
    Serializer,
    SerializerMethodField,
)

from src.core.cms.adp.models import UserProfileChangeRequest
from src.core.cms.adp.services.profile_change_request import ProfileChangeRequestService


def _format_user_fio(user) -> str:
    if user is None:
        return ''
    if hasattr(user, 'get_full_name'):
        return (user.get_full_name() or '').strip()
    parts = [
        ProfileChangeRequestService.normalize_name_part(user.last_name),
        ProfileChangeRequestService.normalize_name_part(user.first_name),
        ProfileChangeRequestService.normalize_name_part(getattr(user, 'middle_name', '')),
    ]
    return ' '.join(part for part in parts if part)


class CreateUserProfileChangeRequestSerializer(Serializer):
    email = EmailField(required=False, allow_blank=True)
    first_name = CharField(required=False, allow_blank=True, max_length=150)
    last_name = CharField(required=False, allow_blank=True, max_length=150)
    middle_name = CharField(required=False, allow_blank=True, max_length=150)
    phone = CharField(required=False, allow_blank=True, max_length=20)
    comment = CharField(required=False, allow_blank=True, max_length=500)


class RejectUserProfileChangeRequestSerializer(Serializer):
    admin_comment = CharField(required=False, allow_blank=True, max_length=500)


class UserProfileChangeRequestSerializer(ModelSerializer):
    user_id = SerializerMethodField(read_only=True)
    public_id = SerializerMethodField(read_only=True)
    username = SerializerMethodField(read_only=True)
    user_email = SerializerMethodField(read_only=True)
    current_email = SerializerMethodField(read_only=True)
    current_full_name = SerializerMethodField(read_only=True)
    current_phone = SerializerMethodField(read_only=True)
    requested_full_name = SerializerMethodField(read_only=True)
    reviewed_by_name = SerializerMethodField(read_only=True)

    class Meta:
        model = UserProfileChangeRequest
        fields = [
            'id',
            'user_id',
            'public_id',
            'username',
            'user_email',
            'email',
            'current_email',
            'first_name',
            'last_name',
            'middle_name',
            'phone',
            'current_phone',
            'current_full_name',
            'requested_full_name',
            'comment',
            'status',
            'admin_comment',
            'reviewed_by_name',
            'reviewed_at',
            'created_at',
        ]
        read_only_fields = fields

    def get_user_id(self, obj):
        return obj.user_id

    def get_public_id(self, obj):
        user = getattr(obj, 'user', None)
        public_id = getattr(user, 'public_id', None)
        return str(public_id) if public_id else None

    def get_username(self, obj):
        return getattr(obj.user, 'username', '') or ''

    def get_user_email(self, obj):
        return getattr(obj.user, 'email', '') or ''

    def get_current_email(self, obj):
        return ProfileChangeRequestService.normalize_email(getattr(obj.user, 'email', ''))

    def get_current_full_name(self, obj):
        return _format_user_fio(obj.user)

    def get_current_phone(self, obj):
        profile = getattr(obj.user, 'adp_profile', None)
        if profile is None:
            return ''
        return ProfileChangeRequestService.normalize_phone(profile.phone)

    def get_requested_full_name(self, obj):
        parts = [
            ProfileChangeRequestService.normalize_name_part(obj.last_name),
            ProfileChangeRequestService.normalize_name_part(obj.first_name),
            ProfileChangeRequestService.normalize_name_part(obj.middle_name),
        ]
        return ' '.join(part for part in parts if part)

    def get_reviewed_by_name(self, obj):
        if not obj.reviewed_by:
            return ''
        return _format_user_fio(obj.reviewed_by) or obj.reviewed_by.username
