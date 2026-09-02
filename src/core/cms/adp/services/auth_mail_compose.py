"""Общая сборка служебных писем учётки: текст ядра и необязательная подмена модулем."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from src.core.integrations import bridge

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComposedAuthEmail:
    subject: str
    body: str
    html_body: str | None = None


def frontend_login_url() -> str:
    base_url = getattr(settings, 'FRONTEND_BASE_URL', 'http://localhost:8001').rstrip('/')
    return f'{base_url}/login'


def normalize_composed_email(raw: Any) -> ComposedAuthEmail | None:
    if not isinstance(raw, dict):
        return None
    subject = raw.get('subject')
    body = raw.get('body')
    if not isinstance(subject, str) or not isinstance(body, str):
        return None
    subject = subject.strip()
    body = body.strip()
    if not subject or not body:
        return None
    html_raw = raw.get('html_body')
    html_body = html_raw.strip() if isinstance(html_raw, str) and html_raw.strip() else None
    return ComposedAuthEmail(subject=subject, body=body, html_body=html_body)


def compose_via_optional_op(
    op_name: str,
    default: ComposedAuthEmail,
    **payload: Any,
) -> ComposedAuthEmail:
    """
    Вызывает необязательный op моста.

    Нет провайдера, None, неполный dict или исключение — текст ядра.
    """
    try:
        override = bridge.call(op_name, default=None, **payload)
    except Exception:
        logger.exception('Провайдер %s упал, отправляется текст ядра', op_name)
        return default
    composed = normalize_composed_email(override)
    return composed if composed is not None else default
