"""
Сервис настроек и кодов восстановления пароля пользователями.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _

from src.core.cms.adp.models import EmailConfirmationCode


class PasswordResetService:
    PURPOSE_PASSWORD_RESET = 'password_reset'

    DISABLED_MESSAGE = (
        'Восстановление пароля отключено администратором. '
        'Обратитесь к администратору системы.'
    )
    UNAVAILABLE_MESSAGE = (
        'Восстановление пароля временно недоступно. '
        'Обратитесь к администратору системы.'
    )
    INVALID_CODE_MESSAGE = 'Неверный или просроченный код'

    @staticmethod
    def is_enabled() -> bool:
        return bool(getattr(settings, 'PASSWORD_RESET_ENABLED', True))

    @staticmethod
    def is_email_delivery_ready() -> bool:
        """True, если исходящая почта включена и SMTP-конфиг резолвится без ошибок."""
        from src.core.utils.smtp_resolver import (
            is_email_enabled,
            resolve_smtp_config,
            validate_config,
        )

        if not is_email_enabled():
            return False
        config = resolve_smtp_config()
        return not validate_config(config)

    @staticmethod
    def is_available() -> bool:
        return (
            PasswordResetService.is_enabled()
            and PasswordResetService.is_email_delivery_ready()
        )

    @staticmethod
    def get_public_settings() -> dict:
        enabled = PasswordResetService.is_enabled()
        email_ready = PasswordResetService.is_email_delivery_ready()
        return {
            'password_reset_enabled': enabled,
            'email_delivery_ready': email_ready,
            'password_reset_available': enabled and email_ready,
        }

    @staticmethod
    def get_disabled_message() -> str:
        return _(
            'Восстановление пароля отключено администратором. '
            'Обратитесь к администратору системы.'
        )

    @staticmethod
    def get_unavailable_message() -> str:
        return _(
            'Восстановление пароля временно недоступно. '
            'Обратитесь к администратору системы.'
        )

    @staticmethod
    def get_invalid_code_message() -> str:
        return _(PasswordResetService.INVALID_CODE_MESSAGE)

    @staticmethod
    def is_password_reset_purpose(purpose: str) -> bool:
        return (purpose or '').strip().lower() == PasswordResetService.PURPOSE_PASSWORD_RESET

    @staticmethod
    def get_code_ttl_minutes() -> int:
        minutes = int(getattr(settings, 'PASSWORD_RESET_CODE_TTL_MINUTES', 15) or 15)
        return max(1, minutes)

    @staticmethod
    def _code_ttl() -> timedelta:
        return timedelta(minutes=PasswordResetService.get_code_ttl_minutes())

    @staticmethod
    def _max_attempts() -> int:
        return max(1, int(getattr(settings, 'PASSWORD_RESET_CODE_MAX_ATTEMPTS', 5) or 5))

    @staticmethod
    def issue_code(email: str) -> str:
        code = get_random_string(length=6, allowed_chars='0123456789')
        now = timezone.now()
        EmailConfirmationCode.objects.update_or_create(
            email=email,
            defaults={
                'code': code,
                'created_at': now,
                'failed_attempts': 0,
            },
        )
        return code

    @staticmethod
    def verify_code(email: str, code: str, *, consume: bool) -> tuple[bool, str]:
        """
        Проверяет код подтверждения.

        Returns:
            (ok, error_message) — при ok error_message пустой.
        """
        invalid = PasswordResetService.get_invalid_code_message()
        try:
            confirmation = EmailConfirmationCode.objects.get(email=email)
        except EmailConfirmationCode.DoesNotExist:
            return False, invalid

        max_attempts = PasswordResetService._max_attempts()
        expired = confirmation.created_at < timezone.now() - PasswordResetService._code_ttl()
        blocked = confirmation.failed_attempts >= max_attempts

        if expired or blocked:
            confirmation.delete()
            return False, invalid

        if confirmation.code != (code or '').strip():
            confirmation.failed_attempts += 1
            if confirmation.failed_attempts >= max_attempts:
                confirmation.delete()
            else:
                confirmation.save(update_fields=['failed_attempts'])
            return False, invalid

        if consume:
            confirmation.delete()

        return True, ''
