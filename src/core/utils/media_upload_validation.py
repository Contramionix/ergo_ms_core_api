"""Серверная валидация параметров upload-токена media_api."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from django.conf import settings
from rest_framework.exceptions import ValidationError

# Безопасный относительный путь: сегменты [a-zA-Z0-9_.-], без '..'
_TARGET_DIR_RE = re.compile(
    r'^[a-zA-Z0-9_][a-zA-Z0-9_.-]*(?:/[a-zA-Z0-9_][a-zA-Z0-9_.-]*)*/?$'
)

# Служебные пакеты справки пишет процесс через MediaClient, не браузер.
_RESERVED_UPLOAD_PREFIXES = frozenset({'knowledge'})

# Расширения по умолчанию (клиент может запросить подмножество)
_DEFAULT_ALLOWED_EXTENSIONS = frozenset({
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico',
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'txt', 'csv', 'rtf',
    'parquet',
    'zip', '7z', 'rar', 'tar', 'gz',
    'mp3', 'wav', 'ogg', 'mp4', 'webm', 'mov', 'avi',
    'json', 'xml', 'yaml', 'yml', 'md',
    'epub', 'fb2',
})


def _configured_extensions() -> frozenset[str]:
    configured = getattr(settings, 'MEDIA_UPLOAD_ALLOWED_EXTENSIONS', None)
    if configured:
        return frozenset(str(x).lower().lstrip('.') for x in configured)
    return _DEFAULT_ALLOWED_EXTENSIONS


def normalize_target_dir(target_dir: str | None) -> str:
    """
    Нормализует target_dir: относительный POSIX-путь без ``..``.
    Пустая строка допустима (корень хранилища).
    """
    if target_dir is None:
        return ''
    raw = str(target_dir).replace('\\', '/').strip()
    if not raw or raw in ('.', './'):
        return ''
    if raw.startswith('/') or (len(raw) >= 2 and raw[1] == ':'):
        raise ValidationError({'target_dir': 'Абсолютный путь запрещён'})
    posix = PurePosixPath(raw)
    if posix.is_absolute() or '..' in posix.parts:
        raise ValidationError({'target_dir': 'Путь содержит недопустимые сегменты'})
    parts = [p for p in posix.parts if p not in ('', '.')]
    if not parts:
        return ''
    if parts[0].lower() in _RESERVED_UPLOAD_PREFIXES:
        raise ValidationError({
            'target_dir': 'Каталог knowledge зарезервирован для служебных пакетов',
        })
    normalized = '/'.join(parts)
    if not _TARGET_DIR_RE.match(normalized):
        raise ValidationError({'target_dir': 'Недопустимый формат каталога'})
    max_len = int(getattr(settings, 'MEDIA_UPLOAD_TARGET_DIR_MAX_LENGTH', 200))
    if len(normalized) > max_len:
        raise ValidationError({'target_dir': f'Слишком длинный путь (макс. {max_len})'})
    return normalized


def _media_default_max() -> int:
    return int(getattr(settings, 'MEDIA_UPLOAD_MAX_SIZE', 524288000))


def _media_hard_max() -> int:
    """Абсолютный потолок; модули могут запросить выше MEDIA_UPLOAD_MAX_SIZE, но не выше hard."""
    default = _media_default_max()
    hard = int(getattr(settings, 'MEDIA_UPLOAD_HARD_MAX_SIZE', 0) or 0)
    if hard <= 0:
        hard = default
    return max(hard, default)


def cap_max_size(requested: int | None) -> int:
    """
    Без max_size в токене — дефолт MEDIA_UPLOAD_MAX_SIZE.
    С явным max_size — до MEDIA_UPLOAD_HARD_MAX_SIZE (модульный override выше дефолта).
    """
    default_limit = _media_default_max()
    hard_limit = _media_hard_max()
    if requested is None:
        return default_limit
    try:
        value = int(requested)
    except (TypeError, ValueError) as exc:
        raise ValidationError({'max_size': 'Некорректный max_size'}) from exc
    if value <= 0:
        raise ValidationError({'max_size': 'max_size должен быть положительным'})
    return min(value, hard_limit)


def filter_allowed_types(requested: list | None) -> list[str] | None:
    """
    Пересечение клиентского списка с серверным whitelist.
    None / пустой список → None (ограничение только серверным лимитом размера).
    """
    allowed = _configured_extensions()
    if not requested:
        return None
    if not isinstance(requested, (list, tuple)):
        raise ValidationError({'allowed_types': 'Ожидается список расширений'})
    cleaned = []
    for item in requested:
        ext = str(item).lower().lstrip('.')
        if not ext or not re.fullmatch(r'[a-z0-9]{1,16}', ext):
            continue
        if ext in allowed:
            cleaned.append(ext)
    if not cleaned:
        raise ValidationError({
            'allowed_types': 'Ни один из указанных типов не разрешён сервером',
        })
    return cleaned
