"""
MailService — единая точка отправки писем уведомлений.

Конфигурация SMTP: приоритет — запись EmailSettings из БД (CMS-настройки),
fallback — env-переменные (src/config/settings/smtp.py). Кроссплатформенно:
только Django mail backend, без OS-зависимостей.
"""

import logging
from dataclasses import dataclass

from django.core.mail import EmailMultiAlternatives

from src.core.utils.smtp_errors import format_smtp_error
from src.core.utils.smtp_local_hostname import apply_django_smtp_helo_override
from src.core.utils.smtp_resolver import (
    EMAIL_DISABLED_MESSAGE,
    is_email_enabled,
    resolve_connection_and_from,
)
from src.core.utils.transactional_email_headers import apply_transactional_email_headers

from .email_templates import EmailTemplateResolver, RenderedEmail, resolve_unsubscribe_url

logger = logging.getLogger('core.notifications')


@dataclass
class SendResult:
    success: bool
    error: str = ''


class MailService:
    """Отправка письма по уведомлению. Идемпотентность — на уровне Celery-задачи."""

    @staticmethod
    def send_notification_email(*, notification, recipient_email: str) -> SendResult:
        if not is_email_enabled():
            logger.debug('MailService: %s', EMAIL_DISABLED_MESSAGE)
            return SendResult(success=False, error=EMAIL_DISABLED_MESSAGE)

        connection, from_email = resolve_connection_and_from()
        if not from_email:
            msg = 'SMTP не настроен (нет EmailSettings в БД и DEFAULT_FROM_EMAIL в env)'
            logger.warning('MailService: %s', msg)
            return SendResult(success=False, error=msg)

        try:
            rendered: RenderedEmail = EmailTemplateResolver.resolve(notification)
        except Exception as exc:
            logger.exception('MailService: ошибка рендера письма notification=%s', notification.pk)
            return SendResult(success=False, error=f'Ошибка рендера шаблона: {exc}')

        try:
            apply_django_smtp_helo_override()
            sender = rendered.from_email or from_email
            message = EmailMultiAlternatives(
                subject=rendered.subject,
                body=rendered.text_body,
                from_email=sender,
                to=[recipient_email],
                connection=connection,
            )
            apply_transactional_email_headers(
                message,
                from_email=sender,
                unsubscribe_url=resolve_unsubscribe_url(),
            )
            message.attach_alternative(rendered.html_body, 'text/html')
            message.send(fail_silently=False)
            return SendResult(success=True)
        except Exception as exc:
            logger.exception(
                'MailService: ошибка отправки notification=%s -> %s',
                notification.pk, recipient_email,
            )
            return SendResult(success=False, error=format_smtp_error(exc))
