"""Cache-based блокировка входа после серии неудачных попыток."""

from __future__ import annotations

import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('django.core.cms.adp')

_ATTEMPTS_PREFIX = 'auth:lockout:attempts:'
_LOCK_PREFIX = 'auth:lockout:blocked:'


def _normalize_login(login: str) -> str:
    return (login or '').strip().lower()


def _key_suffix(login: str) -> str:
    normalized = _normalize_login(login)
    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]
    return digest


def lockout_max_attempts() -> int:
    return int(getattr(settings, 'API_AUTH_LOCKOUT_MAX_ATTEMPTS', 0) or 0)


def lockout_window_seconds() -> int:
    return int(getattr(settings, 'API_AUTH_LOCKOUT_WINDOW_SECONDS', 900) or 900)


def lockout_duration_seconds() -> int:
    return int(getattr(settings, 'API_AUTH_LOCKOUT_DURATION_SECONDS', 900) or 900)


def is_login_locked(login: str) -> bool:
    if lockout_max_attempts() <= 0:
        return False
    return cache.get(f'{_LOCK_PREFIX}{_key_suffix(login)}') is not None


def login_lock_retry_after(login: str) -> int:
    """Секунды до снятия блокировки; минимум 1, если блокировка активна."""
    duration = lockout_duration_seconds()
    if lockout_max_attempts() <= 0:
        return duration
    expiry = cache.get(f'{_LOCK_PREFIX}{_key_suffix(login)}')
    if expiry is None:
        return duration
    try:
        expiry_ts = float(expiry)
    except (TypeError, ValueError):
        return duration
    # Старые ключи хранили флаг 1, а не unix-время.
    if expiry_ts < 1_000_000_000:
        return duration
    return max(int(expiry_ts - time.time()), 1)


def register_failed_login(login: str) -> bool:
    """
    Учесть неудачную попытку. Возвращает True, если после этого вход заблокирован.
    """
    max_attempts = lockout_max_attempts()
    if max_attempts <= 0:
        return False

    suffix = _key_suffix(login)
    attempts_key = f'{_ATTEMPTS_PREFIX}{suffix}'
    lock_key = f'{_LOCK_PREFIX}{suffix}'
    window = lockout_window_seconds()
    duration = lockout_duration_seconds()

    try:
        attempts = cache.incr(attempts_key)
    except ValueError:
        cache.set(attempts_key, 1, timeout=window)
        attempts = 1

    if attempts >= max_attempts:
        cache.set(lock_key, time.time() + duration, timeout=duration)
        cache.delete(attempts_key)
        logger.warning('auth lockout: login blocked after %s failed attempts', attempts)
        return True
    return False


def clear_login_lockout(login: str) -> None:
    if lockout_max_attempts() <= 0:
        return
    suffix = _key_suffix(login)
    cache.delete(f'{_ATTEMPTS_PREFIX}{suffix}')
    cache.delete(f'{_LOCK_PREFIX}{suffix}')
