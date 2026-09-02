"""Письма восстановления и сброса пароля: текст ядра и необязательная подмена модулем."""

from __future__ import annotations

import logging

from src.core.cms.adp.services.auth_mail_compose import (
    ComposedAuthEmail,
    compose_via_optional_op,
    frontend_login_url,
)
from src.core.cms.adp.services.password_reset import PasswordResetService
from src.core.integrations.module_contracts import (
    CORE_COMPOSE_ADMIN_PASSWORD_RESET,
    CORE_COMPOSE_PASSWORD_RESET_CODE,
)
from src.core.utils.plain_mail import send_plain_email

DEFAULT_RESET_CODE_SUBJECT = 'Код подтверждения ERGO MS'
DEFAULT_ADMIN_RESET_SUBJECT = 'Сброс пароля администратором — ERGO MS'


def build_default_password_reset_code(*, code: str) -> ComposedAuthEmail:
    return ComposedAuthEmail(
        subject=DEFAULT_RESET_CODE_SUBJECT,
        body=f'Ваш код подтверждения: {code}',
    )


def recovery_hint_for_admin_reset() -> str:
    if PasswordResetService.is_enabled():
        return (
            'Для восстановления доступа воспользуйтесь формой «Забыл пароль» '
            'на странице входа в систему.'
        )
    return 'Для восстановления доступа обратитесь к администратору системы.'


def build_default_admin_password_reset(*, recovery_hint: str) -> ComposedAuthEmail:
    body = (
        'Администратор системы сбросил пароль вашей учётной записи.\n\n'
        'Текущий пароль никому не известен — ни вам, ни администраторам — '
        'в целях безопасности.\n\n'
        f'{recovery_hint}'
    )
    return ComposedAuthEmail(subject=DEFAULT_ADMIN_RESET_SUBJECT, body=body)


def compose_password_reset_code_email(*, email: str, code: str) -> ComposedAuthEmail:
    default = build_default_password_reset_code(code=code)
    return compose_via_optional_op(
        CORE_COMPOSE_PASSWORD_RESET_CODE,
        default,
        email=email,
        code=code,
        ttl_minutes=PasswordResetService.get_code_ttl_minutes(),
        login_url=frontend_login_url(),
        default_subject=default.subject,
        default_body=default.body,
    )


def compose_admin_password_reset_email(*, email: str) -> ComposedAuthEmail:
    recovery_hint = recovery_hint_for_admin_reset()
    default = build_default_admin_password_reset(recovery_hint=recovery_hint)
    return compose_via_optional_op(
        CORE_COMPOSE_ADMIN_PASSWORD_RESET,
        default,
        email=email,
        recovery_hint=recovery_hint,
        login_url=frontend_login_url(),
        default_subject=default.subject,
        default_body=default.body,
    )


def send_password_reset_code_email(email: str, code: str) -> tuple[bool, str | None]:
    composed = compose_password_reset_code_email(email=email, code=code)
    return send_plain_email(
        subject=composed.subject,
        body=composed.body,
        recipients=[email],
        html_body=composed.html_body,
    )


def send_admin_password_reset_email(email: str) -> tuple[bool, str | None]:
    composed = compose_admin_password_reset_email(email=email)
    return send_plain_email(
        subject=composed.subject,
        body=composed.body,
        recipients=[email],
        html_body=composed.html_body,
        fail_log_level=logging.WARNING,
    )
