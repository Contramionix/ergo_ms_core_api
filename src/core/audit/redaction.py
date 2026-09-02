"""Очистка полезной нагрузки аудита от секретов перед записью.

Единая точка: ни ядро, ни модули не должны заботиться о том, что в meta/changes
могут попасть пароли или токены — движок вырежет их сам (см. security.mdc).
"""

from __future__ import annotations

from typing import Any

REDACTED = '***'

# Совпадение по подстроке в имени ключа (регистронезависимо).
SENSITIVE_KEY_PARTS = (
    'password',
    'passwd',
    'secret',
    'token',
    'authorization',
    'api_key',
    'apikey',
    'access',
    'refresh',
    'private_key',
    'session',
    'csrf',
    'otp',
    'code',
    'passport',
    'birth_date',
    'birth_place',
    'registration_address',
)

_MAX_DEPTH = 6
_MAX_STR = 2000


def _is_sensitive(key: str) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact(value: Any, _depth: int = 0) -> Any:
    """Рекурсивно маскирует чувствительные ключи и обрезает длинные строки."""
    if _depth > _MAX_DEPTH:
        return REDACTED

    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _is_sensitive(key):
                cleaned[key] = REDACTED
            else:
                cleaned[key] = redact(item, _depth + 1)
        return cleaned

    if isinstance(value, (list, tuple)):
        return [redact(item, _depth + 1) for item in value]

    if isinstance(value, str) and len(value) > _MAX_STR:
        return value[:_MAX_STR] + '…'

    return value


def redact_changes(changes: Any) -> Any:
    """Маскирует значения old/new для чувствительных полей в списке изменений."""
    if not isinstance(changes, list):
        return redact(changes)

    cleaned = []
    for entry in changes:
        if not isinstance(entry, dict):
            cleaned.append(redact(entry))
            continue
        field = entry.get('field', '')
        item = dict(entry)
        if _is_sensitive(field):
            if 'old' in item:
                item['old'] = REDACTED
            if 'new' in item:
                item['new'] = REDACTED
        else:
            if 'old' in item:
                item['old'] = redact(item['old'])
            if 'new' in item:
                item['new'] = redact(item['new'])
        cleaned.append(item)
    return cleaned
