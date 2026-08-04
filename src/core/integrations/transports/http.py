"""
HTTP Transport ModuleBridge (BRIDGE_TRANSPORT=http).

Локальный реестр как LocalTransport; при отсутствии провайдера —
RPC на сервис-владелец через /internal/bridge/*.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

import httpx
from django.conf import settings

from ..exceptions import DuplicateProvider
from ..service_map import (
    all_remote_base_urls,
    build_service_map,
    iter_group_base_urls,
    resolve_op_base_url,
)

logger = logging.getLogger('integrations.bridge.http')

_TOKEN_HEADER = 'X-Bridge-Token'


def _internal_token() -> str:
    return (getattr(settings, 'BRIDGE_INTERNAL_TOKEN', '') or '').strip()


def _timeout() -> float:
    raw = getattr(settings, 'BRIDGE_HTTP_TIMEOUT', 10)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 10.0


def _self_base_url() -> str | None:
    """URL этого процесса в карте сервисов — не звать сам себя по HTTP."""
    role = (getattr(settings, 'ERGO_PROCESS_ROLE', '') or '').strip().lower()
    data = build_service_map()
    if role.startswith('module:'):
        name = role.split(':', 1)[1].strip()
        return data['urls'].get(name)
    if role in ('api', 'core-api', ''):
        core = data.get('core_url')
        return core if core else None
    return None


def _remote_bases_for_group(group: str) -> list[str]:
    self_url = (_self_base_url() or '').rstrip('/')
    bases = iter_group_base_urls(group) or all_remote_base_urls()
    if not self_url:
        return bases
    return [u for u in bases if u.rstrip('/') != self_url]


def _json_safe(value: Any) -> Any:
    """Проверка/нормализация для JSON; несериализуемое → TypeError."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    raise TypeError(
        f'Bridge HTTP: значение типа {type(value).__name__} нельзя сериализовать в JSON'
    )


class HttpTransport:
    """
    Hybrid: local provide/call, иначе HTTP к владельцу операции.

    ``provide`` / ``provide_many`` всегда локальны (этот процесс — провайдер).
    """

    def __init__(self) -> None:
        self._providers: dict[str, Callable[..., Any]] = {}
        self._groups: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def provide(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        override: bool = False,
    ) -> None:
        if not name:
            raise ValueError("Operation name is required")
        if not callable(handler):
            raise TypeError(f"Handler for '{name}' must be callable")
        with self._lock:
            existing = self._providers.get(name)
            if existing is not None and existing is not handler and not override:
                raise DuplicateProvider(
                    f"Provider for '{name}' already registered: {existing!r}"
                )
            self._providers[name] = handler
            logger.debug("Registered local provider '%s'", name)

    def provide_many(self, group: str, key: str, obj: Any) -> None:
        if not group:
            raise ValueError("Group name is required")
        if not key:
            raise ValueError("Provider key is required")
        with self._lock:
            providers = self._groups.setdefault(group, {})
            providers[key] = obj
            logger.debug("Registered local '%s' for group '%s'", key, group)

    def has(self, name: str) -> bool:
        with self._lock:
            if name in self._providers:
                return True
        base = resolve_op_base_url(name)
        if not base:
            return False
        try:
            return self._remote_has(base, name)
        except Exception:
            # Недоступный peer при старте/проверках — как отсутствие провайдера.
            logger.debug(
                "Bridge HTTP has(%s) failed via %s",
                name,
                base,
                exc_info=True,
            )
            return False

    def call(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        default: Any,
    ) -> Any:
        with self._lock:
            handler = self._providers.get(name)
        if handler is not None:
            return handler(*args, **kwargs)

        base = resolve_op_base_url(name)
        if not base:
            return default
        try:
            return self._remote_call(base, name, args, kwargs)
        except Exception:
            logger.exception("Bridge HTTP call failed for '%s' via %s", name, base)
            return default

    def all(self, group: str) -> dict[str, Any]:
        with self._lock:
            merged = dict(self._groups.get(group, {}))

        for base in _remote_bases_for_group(group):
            try:
                remote = self._remote_all(base, group)
            except Exception:
                logger.exception("Bridge HTTP all(%s) failed via %s", group, base)
                continue
            for key, obj in remote.items():
                if key not in merged:
                    merged[key] = obj
        return merged

    def unregister(self, name: str) -> None:
        with self._lock:
            self._providers.pop(name, None)

    def unregister_many(self, group: str, key: str) -> None:
        with self._lock:
            providers = self._groups.get(group)
            if providers and key in providers:
                del providers[key]
                if not providers:
                    del self._groups[group]

    def reset(self) -> None:
        with self._lock:
            self._providers.clear()
            self._groups.clear()

    def local_providers(self) -> dict[str, Callable[..., Any]]:
        """Копия локального реестра single-op (без remote)."""
        with self._lock:
            return dict(self._providers)

    def local_group(self, group: str) -> dict[str, Any]:
        """Копия локальных провайдеров группы (без remote merge)."""
        with self._lock:
            return dict(self._groups.get(group, {}))

    def _headers(self, base: str | None = None) -> dict[str, str]:
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        token = _internal_token()
        if token:
            headers[_TOKEN_HEADER] = token
        # Compose-имена modules/<name> часто с ``_``; для Django Host это не RFC-hostname.
        if base:
            from urllib.parse import urlparse

            host = (urlparse(base).hostname or '').strip()
            if '_' in host:
                headers['Host'] = 'localhost'
        return headers

    def _remote_call(
        self,
        base: str,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        payload = {
            'op': name,
            'args': _json_safe(list(args)),
            'kwargs': _json_safe(kwargs),
        }
        url = f'{base}/internal/bridge/call'
        with httpx.Client(timeout=_timeout()) as client:
            response = client.post(url, json=payload, headers=self._headers(base))
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict) and 'result' in data:
            return data['result']
        return data

    def _remote_has(self, base: str, name: str) -> bool:
        url = f'{base}/internal/bridge/has'
        with httpx.Client(timeout=_timeout()) as client:
            response = client.get(
                url,
                params={'op': name},
                headers=self._headers(base),
            )
            response.raise_for_status()
            data = response.json()
        return bool(data.get('has')) if isinstance(data, dict) else False

    def _remote_all(self, base: str, group: str) -> dict[str, Any]:
        url = f'{base}/internal/bridge/all'
        with httpx.Client(timeout=_timeout()) as client:
            response = client.get(
                url,
                params={'group': group},
                headers=self._headers(base),
            )
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict) and isinstance(data.get('providers'), dict):
            return data['providers']
        if isinstance(data, dict):
            return data
        return {}
