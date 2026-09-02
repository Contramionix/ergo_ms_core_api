from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

User = get_user_model()
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.serializers import (
    BooleanField,
    CharField,
    IntegerField,
    ListField,
    ModelSerializer,
    Serializer,
    ValidationError,
)

from src.core.cms.adp.password_policy import validate_new_password_pair, validate_password_value
from src.core.cms.adp.services.registration import RegistrationService


class UserRegistrationValidationSerializer(Serializer):
    first_name = CharField(required=True)
    last_name = CharField(required=True)
    middle_name = CharField(required=False, allow_blank=True)
    username = CharField(required=True)
    email = CharField(required=True)
    password = CharField(write_only=True, style={'input_type': 'password'})
    invitation_token = CharField(required=False, allow_blank=True, write_only=True)

    def validate_username(self, value):
        RegistrationService.ensure_registration_open()
        if User.objects.filter(username=value).exists():
            raise ValidationError(_("Данный логин уже занят, попробуйте другой."))
        return value

    def validate_email(self, value):
        RegistrationService.ensure_registration_open()
        normalized = (value or '').strip().lower()
        error = RegistrationService.validate_email_for_registration(normalized)
        if error:
            raise ValidationError(error)
        return normalized

    def validate(self, attrs):
        _validate_registration_access(attrs)
        return attrs


def _validate_registration_access(attrs):
    mode = RegistrationService.get_mode()
    if mode == RegistrationService.MODE_CLOSED:
        raise ValidationError({'message': RegistrationService.registration_disabled_message()})

    if mode != RegistrationService.MODE_INVITATION:
        return

    token = (attrs.get('invitation_token') or '').strip()
    if not token:
        raise ValidationError({'invitation_token': _('Для регистрации требуется приглашение.')})

    invitation = RegistrationService.get_valid_invitation(token)
    if not invitation:
        raise ValidationError({'invitation_token': _('Приглашение недействительно или истекло.')})

    email = (attrs.get('email') or '').strip().lower()
    if invitation.email.lower() != email:
        raise ValidationError({'email': _('Email не совпадает с приглашением.')})

    attrs['_invitation'] = invitation


class UserRegistrationSerializer(ModelSerializer):
    password = CharField(
        write_only=True,
        style={'input_type': 'password'},
    )
    invitation_token = CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'middle_name', 'username', 'email',
            'password', 'invitation_token',
        ]
        # UniqueValidator иначе срабатывает до validate_* и утекает при closed.
        extra_kwargs = {
            'username': {'validators': []},
            'email': {'validators': []},
        }

    def validate_password(self, value):
        try:
            validate_password_value(value)
        except DjangoValidationError as exc:
            raise ValidationError(list(exc.messages))
        return value

    def validate_username(self, value):
        RegistrationService.ensure_registration_open()
        if User.objects.filter(username=value).exists():
            raise ValidationError(_("Данный логин уже занят, попробуйте другой."))
        return value

    def validate_email(self, value):
        RegistrationService.ensure_registration_open()
        normalized = (value or '').strip().lower()
        error = RegistrationService.validate_email_for_registration(normalized)
        if error:
            raise ValidationError(error)
        return normalized

    def validate(self, attrs):
        _validate_registration_access(attrs)
        return attrs

    def create(self, validated_data):
        invitation = validated_data.pop('_invitation', None)
        validated_data.pop('invitation_token', None)

        user = User.objects.create_user(
            username=validated_data['username'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            middle_name=validated_data.get('middle_name', ''),
            email=validated_data['email'],
            password=validated_data['password'],
        )

        if invitation:
            RegistrationService.mark_invitation_used(invitation, user)

        return user


class UserLoginSerializer(Serializer):
    username = CharField(max_length=254)
    password = CharField(write_only=True)


class ChangePasswordSerializer(Serializer):
    current_password = CharField(write_only=True, style={'input_type': 'password'})
    new_password = CharField(write_only=True, style={'input_type': 'password'})
    confirm_password = CharField(write_only=True, style={'input_type': 'password'})

    def validate(self, attrs):
        return validate_new_password_pair(attrs)

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not authenticate(username=user.username, password=value):
            raise ValidationError(_("Неверный текущий пароль."))
        return value


class AdminResetUserPasswordSerializer(Serializer):
    new_password = CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        style={'input_type': 'password'},
    )
    confirm_password = CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        style={'input_type': 'password'},
    )

    def validate(self, attrs):
        new_password = (attrs.get('new_password') or '').strip()
        confirm_password = (attrs.get('confirm_password') or '').strip()

        if not new_password and not confirm_password:
            return attrs

        if not new_password or not confirm_password:
            raise ValidationError(_('Укажите новый пароль и подтверждение.'))

        attrs['new_password'] = new_password
        attrs['confirm_password'] = confirm_password
        return validate_new_password_pair(attrs)


class AdminCreateUserSerializer(Serializer):
    username = CharField(required=True, max_length=150)
    password = CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        style={'input_type': 'password'},
    )
    confirm_password = CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        style={'input_type': 'password'},
    )
    first_name = CharField(required=False, allow_blank=True, default='')
    last_name = CharField(required=False, allow_blank=True, default='')
    middle_name = CharField(required=False, allow_blank=True, default='')
    email = CharField(required=False, allow_blank=True, default='')
    role_id = IntegerField(required=False, allow_null=True)
    role_group_ids = ListField(child=IntegerField(), required=False, default=list)
    send_password_notification = BooleanField(required=False, default=True)

    def validate_username(self, value):
        normalized = (value or '').strip()
        if not normalized:
            raise ValidationError(_('Логин обязателен.'))
        if User.objects.filter(username__iexact=normalized).exists():
            raise ValidationError(_('Данный логин уже занят, попробуйте другой.'))
        return normalized

    def validate_email(self, value):
        normalized = (value or '').strip().lower()
        if not normalized:
            return ''
        error = RegistrationService.validate_email_uniqueness(normalized)
        if error:
            raise ValidationError(error)
        return normalized

    def validate(self, attrs):
        password = (attrs.get('password') or '').strip()
        confirm_password = (attrs.get('confirm_password') or '').strip()
        if not password and not confirm_password:
            return attrs
        if not password or not confirm_password:
            raise ValidationError(_('Укажите пароль и подтверждение.'))
        validate_new_password_pair({
            'new_password': password,
            'confirm_password': confirm_password,
        })
        attrs['password'] = password
        attrs['confirm_password'] = confirm_password
        return attrs
