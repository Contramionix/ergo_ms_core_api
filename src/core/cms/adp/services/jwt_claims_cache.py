"""Краткий кэш ответов ядра для jwt_claims — иначе каждый запрос бьёт в HTTP-мост."""

from __future__ import annotations

import hashlib
import json

from django.core.cache import cache

from src.config.env import env

# Тот же ключ, что у проверки устройства на ядре: снимок живёт не дольше сессии.
_TTL = env.int('API_DEVICE_SESSION_CACHE_TTL', default=45)


def _device_key(user_id, device_id, user_public_id: str) -> str:
    return f'jwt_claims:device:{user_id or ""}:{user_public_id or ""}:{device_id}'


def get_device_snapshot(user_id, device_id, user_public_id: str = ''):
    return cache.get(_device_key(user_id, device_id, user_public_id))


def set_device_snapshot(user_id, device_id, user_public_id: str, snapshot: dict) -> None:
    cache.set(_device_key(user_id, device_id, user_public_id), snapshot, timeout=_TTL)


def drop_device_snapshot(user_id, device_id, user_public_id: str = '') -> None:
    cache.delete(_device_key(user_id, device_id, user_public_id))


def _adp_key(kind: str, user_id, user_public_id: str, extra: str = '') -> str:
    return f'jwt_claims:adp:{kind}:{user_id or ""}:{user_public_id or ""}:{extra}'


def get_adp(kind: str, user_id, user_public_id: str, extra: str = ''):
    return cache.get(_adp_key(kind, user_id, user_public_id, extra))


def set_adp(kind: str, user_id, user_public_id: str, value, extra: str = '') -> None:
    cache.set(_adp_key(kind, user_id, user_public_id, extra), value, timeout=_TTL)


def extra_fingerprint(payload) -> str:
    raw = json.dumps(payload or {}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
