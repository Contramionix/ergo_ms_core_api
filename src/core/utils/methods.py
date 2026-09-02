"""
Файл с вспомогательными методами.

Этот файл содержит различные вспомогательные методы, которые используются в других частях модуля и приложения.
"""

import secrets
import string
from typing import Dict, Tuple, Optional

from django.contrib.auth import password_validation
from django.conf import settings


def parse_errors_to_dict(error_dict: Dict[str, list]) -> Dict[str, str]:
    """
    Преобразует словарь ошибок DRF в плоский строковый формат для UI.
    """
    parsed_errors = {}

    def flatten(prefix: str, details) -> None:
        if isinstance(details, dict):
            for field, value in details.items():
                next_prefix = f'{prefix}.{field}' if prefix else field
                flatten(next_prefix, value)
            return

        if isinstance(details, list):
            message = ', '.join(str(detail) for detail in details)
            field_key = prefix.split('.')[-1] if prefix else 'non_field_errors'
            parsed_errors[field_key] = message
            return

        if details is not None:
            field_key = prefix.split('.')[-1] if prefix else 'non_field_errors'
            parsed_errors[field_key] = str(details)

    flatten('', error_dict)
    return parsed_errors


def send_confirmation_email(email: str, code: str) -> Tuple[bool, Optional[str]]:
    """Отправляет email с кодом подтверждения."""
    from src.core.cms.adp.services.password_reset_mail import send_password_reset_code_email

    return send_password_reset_code_email(email, code)


def generate_secure_random_password(length: int = 16, max_attempts: int = 50) -> str:
    """Генерирует пароль, проходящий AUTH_PASSWORD_VALIDATORS и правила ADP."""
    min_length = getattr(settings, 'PASSWORD_MIN_LENGTH', 8)
    if length < min_length:
        length = min_length

    alphabet = string.ascii_letters + string.digits + '!@#$%&*-_=+'
    rng = secrets.SystemRandom()

    for _ in range(max_attempts):
        chars = []
        if getattr(settings, 'PASSWORD_REQUIRE_LOWERCASE', True):
            chars.append(rng.choice(string.ascii_lowercase))
        if getattr(settings, 'PASSWORD_REQUIRE_UPPERCASE', False):
            chars.append(rng.choice(string.ascii_uppercase))
        if getattr(settings, 'PASSWORD_REQUIRE_DIGIT', True):
            chars.append(rng.choice(string.digits))
        if getattr(settings, 'PASSWORD_REQUIRE_SPECIAL', False):
            chars.append(rng.choice('!@#$%&*-_=+'))
        if not chars:
            chars.append(rng.choice(string.ascii_lowercase))
        chars.extend(rng.choice(alphabet) for _ in range(length - len(chars)))
        rng.shuffle(chars)
        candidate = ''.join(chars)
        try:
            password_validation.validate_password(candidate)
            from src.core.cms.adp.password_policy import validate_new_password_pair

            validate_new_password_pair({
                'new_password': candidate,
                'confirm_password': candidate,
            })
            return candidate
        except Exception:
            continue

    raise RuntimeError('Не удалось сгенерировать допустимый пароль')


def send_admin_password_reset_notification(email: str) -> Tuple[bool, Optional[str]]:
    """Уведомление о сбросе пароля администратором (без пароля и без кода)."""
    from src.core.cms.adp.services.password_reset_mail import send_admin_password_reset_email

    return send_admin_password_reset_email(email)

