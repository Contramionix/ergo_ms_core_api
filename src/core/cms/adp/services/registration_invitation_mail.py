"""Письмо-приглашение на регистрацию: текст ядра и необязательная подмена модулем."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from django.conf import settings

from src.core.cms.adp.services.auth_mail_compose import (
    ComposedAuthEmail,
    compose_via_optional_op,
)
from src.core.integrations.module_contracts import CORE_COMPOSE_REGISTRATION_INVITATION
from src.core.utils.plain_mail import send_plain_email

DEFAULT_INVITATION_SUBJECT = 'Приглашение к регистрации в ERGOMS'

ComposedInvitationEmail = ComposedAuthEmail


def _site_host() -> str:
    base_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/')
    return base_url.removeprefix('https://').removeprefix('http://') or 'ERGOMS'


def _register_url() -> str:
    base_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/')
    return f'{base_url}/register' if base_url else '/register'


def _ttl_label(ttl_days: int) -> str:
    return '1 день' if ttl_days == 1 else f'{ttl_days} дн.'


def extract_invitation_token(invite_url: str) -> str:
    parsed = urlparse(invite_url or '')
    token = (parse_qs(parsed.query).get('invite') or [''])[0].strip()
    if not token and parsed.fragment:
        token = (parse_qs(parsed.fragment).get('invite') or [''])[0].strip()
    return token


def build_default_registration_invitation(
    *,
    invite_url: str,
    ttl_days: int,
) -> ComposedInvitationEmail:
    """Текущий текст ядра. Его же получает модуль в default_subject / default_body."""
    site_host = _site_host()
    register_url = _register_url()
    token = extract_invitation_token(invite_url)
    ttl_label = _ttl_label(ttl_days)

    # Без URL вида /register?invite=<длинный_токен>: Mail.ru часто режет как phishing/spam,
    # хотя обычное письмо с того же ящика проходит.
    if token:
        body = (
            'Здравствуйте!\n\n'
            f'Вас пригласили создать учётную запись в системе ERGOMS ({site_host}).\n\n'
            'Как зарегистрироваться:\n'
            f'1. Откройте страницу: {register_url}\n'
            f'2. Введите код приглашения:\n{token}\n\n'
            f'Код действует {ttl_label}.\n\n'
            'Если вы не ожидали это письмо, просто проигнорируйте его — '
            'доступ без кода не будет создан.\n\n'
            'С уважением,\n'
            'Команда ERGOMS\n'
        )
    else:
        body = (
            'Здравствуйте!\n\n'
            f'Вас пригласили создать учётную запись в системе ERGOMS ({site_host}).\n\n'
            f'Откройте страницу регистрации: {register_url}\n\n'
            f'Ссылка действительна {ttl_label}.\n\n'
            'С уважением,\n'
            'Команда ERGOMS\n'
        )
    return ComposedInvitationEmail(subject=DEFAULT_INVITATION_SUBJECT, body=body)


def compose_registration_invitation_email(
    *,
    email: str,
    invite_url: str,
    ttl_days: int,
) -> ComposedInvitationEmail:
    """
    Собирает тему и тело письма.

    Модуль может дать ``core.compose_registration_invitation``. Если провайдера нет,
    он вернул None / неполный dict или упал — остаётся текст ядра.
    """
    default = build_default_registration_invitation(
        invite_url=invite_url,
        ttl_days=ttl_days,
    )
    return compose_via_optional_op(
        CORE_COMPOSE_REGISTRATION_INVITATION,
        default,
        email=email,
        invite_url=invite_url or '',
        token=extract_invitation_token(invite_url),
        ttl_days=ttl_days,
        ttl_label=_ttl_label(ttl_days),
        register_url=_register_url(),
        site_host=_site_host(),
        default_subject=default.subject,
        default_body=default.body,
    )


def send_registration_invitation_email(
    email: str,
    invite_url: str,
    ttl_days: int,
) -> tuple[bool, str | None]:
    """Отправляет email с ссылкой-приглашением на регистрацию."""
    composed = compose_registration_invitation_email(
        email=email,
        invite_url=invite_url,
        ttl_days=ttl_days,
    )
    return send_plain_email(
        subject=composed.subject,
        body=composed.body,
        recipients=[email],
        html_body=composed.html_body,
    )
