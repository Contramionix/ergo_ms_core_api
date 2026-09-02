"""
Контракт session context платформы — единый дескриптор session-claims.

Модуль декларирует свои session-claims одним дескриптором в группе
``session_context.claims``::

    bridge.provide_many('session_context.claims', key='my_scope', obj={
        'claim': 'my_scope_id',        # ключ в payload JWT
        'request_attr': 'my_scope_id', # атрибут на request (по умолчанию = claim)
        'entity_key': 'my_scope',      # имя ленивого request.my_scope
        'resolve': load_my_scope,      # (my_scope_id=..., **kw) -> entity | None
        'required_guard': True,        # RequiresSessionScope / session_scope_required
    })

Ядро выводит из дескрипторов список JWT claims, карту ``entity_key -> resolve``,
карту ``entity_key -> claim`` и набор claims с ``required_guard``.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

from src.core.integrations import bridge
from src.core.integrations.module_contracts import SESSION_CLAIMS_GROUP

# Список имён session-claim в JWT. Ядро не знает конкретные ключи —
# их кладёт модуль-владелец scope, процесс модуля читает этот список.
SESSION_CLAIM_KEYS_JWT = 'session_claim_keys'

_RESERVED_JWT_CLAIMS = frozenset({
    'exp',
    'iat',
    'nbf',
    'jti',
    'token_type',
    'user_id',
    'device_id',
    'user_public_id',
    'username',
    'is_admin',
    'is_superuser',
    'is_staff',
    'refresh_jti',
    SESSION_CLAIM_KEYS_JWT,
})

_request_session_claims: ContextVar[dict[str, int] | None] = ContextVar(
    'ergo_session_claim_values',
    default=None,
)


def _process_is_module() -> bool:
    """Процесс модуля не ходит по HTTP за чужими session-claim."""
    from django.conf import settings

    role = (getattr(settings, 'ERGO_PROCESS_ROLE', '') or '').strip().lower()
    return role.startswith('module:')

__all__ = (
    'SESSION_CLAIMS_GROUP',
    'SESSION_CLAIM_KEYS_JWT',
    'get_session_claim_descriptors',
    'collect_session_jwt_claims',
    'get_session_entity_resolvers',
    'get_session_entity_claim_keys',
    'get_required_guard_claims',
    'reset_session_context_cache',
    'get_request_session_claim_values',
    'bind_request_session_claim_values',
    'reset_request_session_claim_values',
    'merge_session_scope_kwargs',
    'iter_payload_session_claim_names',
)

_descriptors_cache: list[dict] | None = None


def _normalize_descriptor(key: str, raw: Any) -> dict | None:
    """Приводит сырой дескриптор к каноническому виду или отбрасывает невалидный."""
    if not isinstance(raw, dict):
        return None
    claim = raw.get('claim')
    if not claim or not isinstance(claim, str):
        return None
    request_attr = raw.get('request_attr') or claim
    entity_key = raw.get('entity_key') or None
    resolve = raw.get('resolve')
    return {
        'key': key,
        'claim': claim,
        'request_attr': str(request_attr),
        'entity_key': str(entity_key) if entity_key else None,
        'resolve': resolve if callable(resolve) else None,
        'required_guard': bool(raw.get('required_guard', False)),
    }


def _merge_descriptors(providers: dict[str, Any]) -> list[dict]:
    """Сырые провайдеры группы → список канонических дескрипторов."""
    by_claim: dict[str, dict] = {}
    for key, raw in providers.items():
        descriptor = _normalize_descriptor(str(key), raw)
        if descriptor is None:
            continue
        existing = by_claim.get(descriptor['claim'])
        if existing is None:
            by_claim[descriptor['claim']] = descriptor
            continue
        if not existing.get('entity_key') and descriptor.get('entity_key'):
            existing['entity_key'] = descriptor['entity_key']
        if not existing.get('resolve') and descriptor.get('resolve'):
            existing['resolve'] = descriptor['resolve']
        if descriptor.get('required_guard'):
            existing['required_guard'] = True
    return list(by_claim.values())


def get_session_claim_descriptors(*, local_only: bool = False) -> list[dict]:
    """Дескрипторы session-claim.

    На ядре ``bridge.all`` один раз собирает группу с процессов модулей.
    Процесс модуля и запрос ``/internal/`` смотрят только локальный реестр:
    иначе каждый ``/internal/bridge/all`` снова обходит все URL и зацикливает мост.
    """
    global _descriptors_cache
    if local_only and not _process_is_module():
        return _merge_descriptors(bridge.local_group(SESSION_CLAIMS_GROUP))
    if _descriptors_cache is not None:
        return _descriptors_cache
    if local_only or _process_is_module():
        _descriptors_cache = _merge_descriptors(bridge.local_group(SESSION_CLAIMS_GROUP))
        return _descriptors_cache
    _descriptors_cache = _merge_descriptors(bridge.all(SESSION_CLAIMS_GROUP))
    return _descriptors_cache


def collect_session_jwt_claims() -> tuple[str, ...]:
    """Список claim, которые ядро читает с JWT в request."""
    return tuple(d['claim'] for d in get_session_claim_descriptors())


def get_session_entity_resolvers() -> dict[str, Callable]:
    """entity_key → resolver (для ленивой загрузки ORM-сущности из claim)."""
    return {
        d['entity_key']: d['resolve']
        for d in get_session_claim_descriptors()
        if d.get('entity_key') and callable(d.get('resolve'))
    }


def get_session_entity_claim_keys() -> dict[str, str]:
    """entity_key → claim (имя атрибута на request с id сущности)."""
    return {
        d['entity_key']: d['request_attr']
        for d in get_session_claim_descriptors()
        if d.get('entity_key')
    }


def get_required_guard_claims() -> tuple[str, ...]:
    """claim с флагом required_guard (RequiresSessionScope / session_scope_required)."""
    return tuple(
        d['request_attr']
        for d in get_session_claim_descriptors()
        if d.get('required_guard')
    )


def reset_session_context_cache() -> None:
    """Сброс кеша дескрипторов (тесты, регистрация после старта)."""
    global _descriptors_cache
    _descriptors_cache = None


def get_request_session_claim_values() -> dict[str, int]:
    """Session-claim текущего HTTP-запроса (id из JWT)."""
    return dict(_request_session_claims.get() or {})


def bind_request_session_claim_values(values: dict[str, int]):
    """Запомнить session-claim на время запроса. Возвращает token ContextVar."""
    return _request_session_claims.set(dict(values))


def reset_request_session_claim_values(token) -> None:
    _request_session_claims.reset(token)


def merge_session_scope_kwargs(kwargs: dict | None) -> dict:
    """Session-claim с request плюс явные kwargs проверки (явные побеждают)."""
    merged = get_request_session_claim_values()
    if kwargs:
        merged.update(kwargs)
    return merged


def iter_payload_session_claim_names(payload: Any, descriptors: list) -> list[str]:
    """Имена session-claim в JWT: дескрипторы, ``session_claim_keys``, запасной ``*_id``.

    Процесс модуля не видит чужие дескрипторы. Список ключей в токене и
    целочисленные ``*_id`` (кроме зарезервированных JWT) закрывают этот разрыв.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: Any) -> None:
        if not isinstance(name, str) or not name or name in seen:
            return
        if name in _RESERVED_JWT_CLAIMS:
            return
        seen.add(name)
        names.append(name)

    for descriptor in descriptors or []:
        _add(descriptor.get('claim'))

    raw_keys = None
    try:
        raw_keys = payload.get(SESSION_CLAIM_KEYS_JWT)
    except Exception:
        raw_keys = None
    if isinstance(raw_keys, str):
        raw_keys = [raw_keys]
    if isinstance(raw_keys, (list, tuple)):
        for key in raw_keys:
            _add(key)

    try:
        payload_keys = list(payload.keys()) if hasattr(payload, 'keys') else []
    except Exception:
        payload_keys = []
    for key in payload_keys:
        if not isinstance(key, str) or not key.endswith('_id'):
            continue
        try:
            value = payload.get(key)
        except Exception:
            continue
        if value is None:
            continue
        _add(key)

    return names
