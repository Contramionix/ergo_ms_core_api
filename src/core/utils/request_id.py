"""Сквозной идентификатор запроса: прокси → API → мост → Celery."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_HEADER = 'HTTP_X_REQUEST_ID'
_RESPONSE_HEADER = 'X-Request-ID'

_request_id: ContextVar[str] = ContextVar('ergo_request_id', default='')


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> str:
    return _request_id.get() or ''


def set_request_id(value: str) -> str:
    token = (value or '').strip()
    if not token:
        token = new_request_id()
    _request_id.set(token)
    return token


def request_id_from_meta(meta: dict) -> str:
    raw = meta.get(_HEADER) or meta.get('HTTP_X_CORRELATION_ID') or ''
    return set_request_id(str(raw).strip())


def apply_response_header(response) -> None:
    token = get_request_id()
    if token and hasattr(response, '__setitem__'):
        response[_RESPONSE_HEADER] = token


REQUEST_ID_HEADER = _RESPONSE_HEADER
