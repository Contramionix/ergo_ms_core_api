"""
Сервис регистрации и приглашений пользователей.
"""

import secrets
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

User = get_user_model()
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from src.core.cms.adp.models import RegistrationInvitation
from src.core.cms.adp.services.registration_invitation_mail import (
    send_registration_invitation_email,
)


class RegistrationService:
    MODE_OPEN = 'open'
    MODE_INVITATION = 'invitation'
    MODE_CLOSED = 'closed'

    @staticmethod
    def get_mode() -> str:
        mode = getattr(settings, 'REGISTRATION_MODE', RegistrationService.MODE_OPEN)
        if mode not in (RegistrationService.MODE_OPEN, RegistrationService.MODE_INVITATION, RegistrationService.MODE_CLOSED):
            return RegistrationService.MODE_OPEN
        return mode

    @staticmethod
    def is_registration_enabled() -> bool:
        return RegistrationService.get_mode() != RegistrationService.MODE_CLOSED

    @staticmethod
    def registration_disabled_message() -> str:
        return _('Регистрация в системе отключена.')

    @staticmethod
    def reject_if_registration_closed():
        """HTTP 403 Response, если регистрация закрыта; иначе None."""
        if RegistrationService.is_registration_enabled():
            return None
        from rest_framework import status
        from rest_framework.response import Response

        return Response(
            {'message': RegistrationService.registration_disabled_message()},
            status=status.HTTP_403_FORBIDDEN,
        )

    @staticmethod
    def ensure_registration_open() -> None:
        """Для сериализаторов: ValidationError до проверок username/email."""
        if RegistrationService.is_registration_enabled():
            return
        from rest_framework.serializers import ValidationError

        raise ValidationError(RegistrationService.registration_disabled_message())

    @staticmethod
    def requires_invitation() -> bool:
        return RegistrationService.get_mode() == RegistrationService.MODE_INVITATION

    @staticmethod
    def get_invitation_ttl_days() -> int:
        ttl = getattr(settings, 'REGISTRATION_INVITATION_TTL_DAYS', 7)
        return max(1, int(ttl))

    @staticmethod
    def get_public_settings() -> dict:
        mode = RegistrationService.get_mode()
        return {
            'mode': mode,
            'registration_enabled': mode != RegistrationService.MODE_CLOSED,
            'invitation_required': mode == RegistrationService.MODE_INVITATION,
        }

    @staticmethod
    def build_invitation_url(token: str) -> str:
        base_url = getattr(settings, 'FRONTEND_BASE_URL', 'http://localhost:8001').rstrip('/')
        return f'{base_url}/register#invite={token}'

    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def get_invitation_status(invitation: RegistrationInvitation) -> str:
        if invitation.is_revoked:
            return 'revoked'
        if invitation.used_at:
            return 'used'
        if invitation.expires_at <= timezone.now():
            return 'expired'
        return 'pending'

    @staticmethod
    def is_invitation_valid(invitation: Optional[RegistrationInvitation]) -> bool:
        if not invitation:
            return False
        return RegistrationService.get_invitation_status(invitation) == 'pending'

    @staticmethod
    def get_invitation_by_token(token: str) -> Optional[RegistrationInvitation]:
        if not token:
            return None
        try:
            return RegistrationInvitation.objects.select_related('invited_by').get(token=token.strip())
        except RegistrationInvitation.DoesNotExist:
            return None

    @staticmethod
    def get_valid_invitation(token: str) -> Optional[RegistrationInvitation]:
        invitation = RegistrationService.get_invitation_by_token(token)
        if RegistrationService.is_invitation_valid(invitation):
            return invitation
        return None

    @staticmethod
    def is_email_existence_check_enabled() -> bool:
        return bool(getattr(settings, 'REGISTRATION_CHECK_EMAIL_EXISTS', False))

    @staticmethod
    def _email_already_registered(email: str, *, exclude_user_id: Optional[int] = None) -> bool:
        normalized = (email or '').strip().lower()
        if not normalized:
            return False
        qs = User.objects.filter(email__iexact=normalized)
        if exclude_user_id is not None:
            qs = qs.exclude(pk=exclude_user_id)
        return qs.exists()

    @staticmethod
    def validate_email_uniqueness(email: str, *, exclude_user_id: Optional[int] = None) -> Optional[str]:
        if not RegistrationService.is_email_existence_check_enabled():
            return None
        if RegistrationService._email_already_registered(email, exclude_user_id=exclude_user_id):
            return _('Пользователь с таким email уже существует.')
        return None

    @staticmethod
    def validate_email_for_registration(email: str) -> Optional[str]:
        if not RegistrationService.is_email_existence_check_enabled():
            return None
        normalized = (email or '').strip().lower()
        if RegistrationService._email_already_registered(normalized):
            return _('Пользователь с таким email уже зарегистрирован')
        return None

    @staticmethod
    def has_active_invitation_for_email(email: str) -> bool:
        normalized_email = (email or '').strip().lower()
        if not normalized_email:
            return False
        now = timezone.now()
        return RegistrationInvitation.objects.filter(
            email__iexact=normalized_email,
            is_revoked=False,
            used_at__isnull=True,
            expires_at__gt=now,
        ).exists()

    @staticmethod
    def validate_email_for_invitation(email: str) -> Optional[str]:
        normalized = (email or '').strip().lower()
        if not normalized:
            return _('Email обязателен')
        try:
            validate_email(normalized)
        except DjangoValidationError:
            return _('Некорректный email')
        if RegistrationService._email_already_registered(normalized):
            return _('Пользователь с таким email уже зарегистрирован')
        if RegistrationService.has_active_invitation_for_email(normalized):
            return _('Для этого email уже есть активное приглашение')
        return None

    @staticmethod
    @transaction.atomic
    def create_invitation(*, email: str, invited_by: User, note: str = '', send_email: bool = False) -> tuple:
        normalized_email = email.strip().lower()
        if RegistrationService.has_active_invitation_for_email(normalized_email):
            return None, _('Для этого email уже есть активное приглашение')

        expires_at = timezone.now() + timedelta(days=RegistrationService.get_invitation_ttl_days())
        invitation = RegistrationInvitation.objects.create(
            email=normalized_email,
            token=RegistrationService.generate_token(),
            invited_by=invited_by,
            expires_at=expires_at,
            note=(note or '').strip(),
        )

        email_error = None
        if send_email:
            success, email_error = RegistrationService.send_invitation_email(invitation)
            if not success:
                return invitation, email_error

        return invitation, None

    @staticmethod
    def send_invitation_email(invitation: RegistrationInvitation) -> tuple:
        if not RegistrationService.is_invitation_valid(invitation):
            return False, _('Приглашение недействительно или истекло')

        invite_url = RegistrationService.build_invitation_url(invitation.token)
        ttl_days = RegistrationService.get_invitation_ttl_days()

        return send_registration_invitation_email(
            email=invitation.email,
            invite_url=invite_url,
            ttl_days=ttl_days,
        )

    @staticmethod
    @transaction.atomic
    def mark_invitation_used(invitation: RegistrationInvitation, user: User):
        invitation.used_at = timezone.now()
        invitation.used_by = user
        invitation.save(update_fields=['used_at', 'used_by'])

    @staticmethod
    def revoke_invitation(invitation: RegistrationInvitation) -> tuple:
        if invitation.used_at:
            return False, _('Нельзя отозвать уже использованное приглашение')
        if invitation.is_revoked:
            return False, _('Приглашение уже отозвано')

        invitation.is_revoked = True
        invitation.save(update_fields=['is_revoked'])
        return True, None

    @staticmethod
    def get_inactive_invitations_queryset():
        now = timezone.now()
        return RegistrationInvitation.objects.filter(
            Q(used_at__isnull=False)
            | Q(is_revoked=True)
            | Q(expires_at__lte=now)
        )

    @staticmethod
    def get_pending_invitations_queryset():
        now = timezone.now()
        return RegistrationInvitation.objects.filter(
            is_revoked=False,
            used_at__isnull=True,
            expires_at__gt=now,
        )

    @staticmethod
    def filter_invitations_queryset_by_status(queryset, status: str):
        if not status or status == 'all':
            return queryset

        now = timezone.now()
        if status == 'pending':
            return queryset.filter(
                is_revoked=False,
                used_at__isnull=True,
                expires_at__gt=now,
            )
        if status == 'used':
            return queryset.filter(used_at__isnull=False)
        if status == 'expired':
            return queryset.filter(
                is_revoked=False,
                used_at__isnull=True,
                expires_at__lte=now,
            )
        if status == 'revoked':
            return queryset.filter(is_revoked=True)
        return queryset

    @staticmethod
    def count_invitations_by_scope(scope: str = 'all') -> int:
        if scope == 'inactive':
            return RegistrationService.get_inactive_invitations_queryset().count()
        return RegistrationInvitation.objects.count()

    @staticmethod
    @transaction.atomic
    def clear_invitations(scope: str = 'inactive') -> dict:
        if scope == 'all':
            queryset = RegistrationInvitation.objects.all()
        elif scope == 'inactive':
            queryset = RegistrationService.get_inactive_invitations_queryset()
        else:
            return {'deleted': 0, 'scope': scope, 'error': _('Неизвестный режим очистки')}

        deleted_count, _ = queryset.delete()
        return {'deleted': deleted_count, 'scope': scope}

    @staticmethod
    @transaction.atomic
    def bulk_create_invitations(
        *,
        emails: list,
        invited_by: User,
        note: str = '',
        send_email: bool = False,
    ) -> dict:
        created = []
        skipped = []
        email_warnings = []
        seen = set()

        for raw_email in emails:
            normalized = (raw_email or '').strip().lower()
            if not normalized:
                continue

            if normalized in seen:
                skipped.append({'email': normalized, 'reason': _('Дубликат в списке')})
                continue
            seen.add(normalized)

            validation_error = RegistrationService.validate_email_for_invitation(normalized)
            if validation_error:
                skipped.append({'email': normalized, 'reason': validation_error})
                continue

            invitation, error = RegistrationService.create_invitation(
                email=normalized,
                invited_by=invited_by,
                note=note,
                send_email=False,
            )
            if not invitation:
                skipped.append({'email': normalized, 'reason': error or _('Не удалось создать приглашение')})
                continue

            item = {
                'id': invitation.id,
                'email': invitation.email,
                'invite_url': RegistrationService.build_invitation_url(invitation.token),
                'status': RegistrationService.get_invitation_status(invitation),
            }

            if send_email:
                success, email_error = RegistrationService.send_invitation_email(invitation)
                if not success:
                    item['email_warning'] = email_error
                    email_warnings.append({'email': normalized, 'warning': email_error})

            created.append(item)

        return {
            'created': created,
            'skipped': skipped,
            'email_warnings': email_warnings,
        }

    @staticmethod
    def bulk_send_invitation_emails(invitation_ids: list) -> dict:
        sent = []
        failed = []

        for invitation_id in invitation_ids:
            try:
                invitation = RegistrationInvitation.objects.get(pk=invitation_id)
            except RegistrationInvitation.DoesNotExist:
                failed.append({'id': invitation_id, 'error': _('Приглашение не найдено')})
                continue

            success, error = RegistrationService.send_invitation_email(invitation)
            if success:
                sent.append({'id': invitation.id, 'email': invitation.email})
            else:
                failed.append({'id': invitation.id, 'email': invitation.email, 'error': error})

        return {'sent': sent, 'failed': failed}
