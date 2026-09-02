"""Приветственные письма при массовом импорте пользователей."""

from __future__ import annotations

from typing import Optional, Tuple

from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

User = get_user_model()
from src.core.cms.adp.services.auth_mail_compose import (
    ComposedAuthEmail,
    compose_via_optional_op,
    frontend_login_url,
)
from src.core.integrations.module_contracts import CORE_COMPOSE_IMPORT_WELCOME_DEFAULTS
from src.core.utils.plain_mail import send_plain_email

DEFAULT_WELCOME_SUBJECT = 'Добро пожаловать в ERGO MS'

DEFAULT_WELCOME_BODY = (
    'Здравствуйте, {full_name}!\n\n'
    'Для вас создана учётная запись в системе ERGO MS.\n\n'
    'Логин: {username}\n'
    'E-mail: {email}\n\n'
    'Войти в систему: {login_url}\n\n'
    'Пароль будет сообщён вам администратором отдельно.'
)

WELCOME_PLACEHOLDERS = [
    {'key': 'first_name', 'label': 'Имя'},
    {'key': 'last_name', 'label': 'Фамилия'},
    {'key': 'middle_name', 'label': 'Отчество'},
    {'key': 'full_name', 'label': 'ФИО'},
    {'key': 'username', 'label': 'Логин'},
    {'key': 'email', 'label': 'E-mail'},
    {'key': 'login_url', 'label': 'Ссылка на вход'},
    {'key': 'password', 'label': 'Пароль (только при отправке письма)'},
]

MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 5000


class ImportWelcomeEmailError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def compose_import_welcome_defaults() -> ComposedAuthEmail:
    default = ComposedAuthEmail(subject=DEFAULT_WELCOME_SUBJECT, body=DEFAULT_WELCOME_BODY)
    return compose_via_optional_op(
        CORE_COMPOSE_IMPORT_WELCOME_DEFAULTS,
        default,
        default_subject=default.subject,
        default_body=default.body,
        login_url=frontend_login_url(),
    )


def get_welcome_email_defaults() -> dict:
    composed = compose_import_welcome_defaults()
    return {
        'subject': composed.subject,
        'body': composed.body,
        'placeholders': WELCOME_PLACEHOLDERS,
    }


def build_login_url() -> str:
    return frontend_login_url()


def _build_full_name(user: User) -> str:
    parts = [user.first_name]
    middle_name = getattr(user, 'middle_name', '') or ''
    if middle_name:
        parts.append(middle_name)
    if user.last_name:
        parts.append(user.last_name)
    return ' '.join(part for part in parts if part).strip() or user.username


def normalize_welcome_templates(
    subject: Optional[str],
    body: Optional[str],
) -> tuple[str, str]:
    fallback = compose_import_welcome_defaults()
    normalized_subject = (subject or '').strip() or fallback.subject
    normalized_body = (body or '').strip() or fallback.body

    if len(normalized_subject) > MAX_SUBJECT_LENGTH:
        raise ImportWelcomeEmailError(
            _('Тема письма не должна превышать %(count)d символов.') % {'count': MAX_SUBJECT_LENGTH},
        )
    if len(normalized_body) > MAX_BODY_LENGTH:
        raise ImportWelcomeEmailError(
            _('Текст письма не должен превышать %(count)d символов.') % {'count': MAX_BODY_LENGTH},
        )
    return normalized_subject, normalized_body


def render_welcome_email(
    user: User,
    *,
    subject_template: str,
    body_template: str,
    password: str = '',
) -> tuple[str, str]:
    context = {
        'first_name': user.first_name or '',
        'last_name': user.last_name or '',
        'middle_name': getattr(user, 'middle_name', '') or '',
        'full_name': _build_full_name(user),
        'username': user.username or '',
        'email': user.email or '',
        'login_url': build_login_url(),
        'password': password or '',
    }

    def _replace(template: str) -> str:
        result = template
        for key, value in context.items():
            result = result.replace(f'{{{key}}}', value)
        return result

    return _replace(subject_template), _replace(body_template)


def send_import_welcome_email(
    recipient_email: str,
    subject: str,
    body: str,
) -> Tuple[bool, Optional[str]]:
    return send_plain_email(subject=subject, body=body, recipients=[recipient_email])


def parse_send_welcome_emails_flag(raw_value) -> bool:
    if raw_value is None:
        return False
    return str(raw_value).lower() in ('true', '1', 'yes')
