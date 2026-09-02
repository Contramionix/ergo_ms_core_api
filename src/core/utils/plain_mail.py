"""Отправка простых текстовых писем через единый SMTP-resolver (БД или env)."""

from __future__ import annotations

import logging
import re
from typing import Optional, Sequence, Tuple

from django.core.mail import EmailMultiAlternatives

from src.core.utils.smtp_errors import format_smtp_error, sanitize_email_delivery_message
from src.core.utils.smtp_resolver import EMAIL_DISABLED_MESSAGE, is_email_enabled, resolve_connection_and_from
from src.core.utils.transactional_email_headers import apply_transactional_email_headers

logger = logging.getLogger(__name__)

SendResult = Tuple[bool, Optional[str]]


def normalize_recipient_email(email: str) -> str:
    """Удаляет из адреса символы, способные вызвать подмену заголовков (CRLF, управляющие)."""
    if not email or not isinstance(email, str):
        return ''
    first_line = email.strip().splitlines()[0].strip()
    return re.sub(r'[\r\n\x00-\x1f\x7f]', '', first_line)


def check_email_enabled() -> SendResult | None:
    """None — email включён; иначе (False, сообщение об ошибке)."""
    if not is_email_enabled():
        return False, EMAIL_DISABLED_MESSAGE
    return None


def _plain_to_simple_html(body: str) -> str:
    """Простой HTML-альтернатив (ссылки кликабельны) — меньше «голого» spam-сигнала."""
    escaped = (
        body.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )
    linked = re.sub(
        r'(https?://[^\s<>"\']+)',
        r'<a href="\1">\1</a>',
        escaped,
    )
    paragraphs = ''.join(
        f'<p style="margin:0 0 12px 0;">{block.replace(chr(10), "<br>")}</p>'
        for block in linked.split('\n\n')
    )
    return (
        '<!DOCTYPE html><html><body style="font-family:sans-serif;font-size:14px;'
        f'line-height:1.5;color:#222;">{paragraphs}</body></html>'
    )


def send_plain_email(
    *,
    subject: str,
    body: str,
    recipients: Sequence[str],
    fail_log_level: int = logging.ERROR,
    html_body: str | None = None,
) -> SendResult:
    """
    Отправить plain-text письмо (опционально с HTML-альтернативой).

    Returns:
        (True, None) при успехе; (False, сообщение) при ошибке.
    """
    disabled = check_email_enabled()
    if disabled is not None:
        return disabled[0], sanitize_email_delivery_message(disabled[1])

    normalized = [normalize_recipient_email(addr) for addr in recipients]
    normalized = [addr for addr in normalized if addr]
    if not normalized:
        return False, 'Недопустимый адрес получателя'

    connection, from_email = resolve_connection_and_from()
    if not from_email:
        error_msg = 'SMTP не настроен (нет EmailSettings в БД и DEFAULT_FROM_EMAIL в env)'
        logger.log(fail_log_level, error_msg)
        return False, sanitize_email_delivery_message(error_msg)

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=from_email,
            to=normalized,
            connection=connection,
        )
        message.encoding = 'utf-8'
        apply_transactional_email_headers(message, from_email=from_email)
        html = html_body if html_body is not None else _plain_to_simple_html(body)
        message.attach_alternative(html, 'text/html')
        message.send(fail_silently=False)
        return True, None
    except Exception as exc:
        error_msg = format_smtp_error(exc)
        logger.log(
            fail_log_level,
            'SMTP Error (%s): %s',
            type(exc).__name__,
            error_msg,
            exc_info=fail_log_level >= logging.ERROR,
        )
        return False, sanitize_email_delivery_message(error_msg)
