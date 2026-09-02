"""
HTTP Transport ModuleBridge (BRIDGE_TRANSPORT=http).

Локальный реестр как LocalTransport; при отсутствии провайдера —
RPC на сервис-владелец через /internal/bridge/*.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Iterator

import httpx
from django.conf import settings

from ..exceptions import DuplicateProvider
from .bind_kwargs import kwargs_accepted_by_handler
from ..service_map import (
    all_remote_base_urls,
    build_service_map,
    iter_group_base_urls,
    resolve_op_base_url,
)
from src.core.integrations.module_contracts import SESSION_DEVICE_ACTIVE
from src.core.utils.request_id import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger('integrations.bridge.http')

_TOKEN_HEADER = 'X-Bridge-Token'


_STREAM_READ_TIMEOUT = 300.0


def _http_send(client: httpx.Client, method: str, url: str, *, stream: bool = False, **kwargs):
    """Повтор при 5xx и TransportError, кроме ConnectError (peer не слушает)."""
    last_exc: Exception | None = None
    attempts = _retries() + 1
    for attempt in range(attempts):
        try:
            if stream:
                request = client.build_request(method, url, **kwargs)
                response = client.send(request, stream=True)
            else:
                response = client.request(method, url, **kwargs)
            if response.status_code >= 500 and attempt + 1 < attempts:
                logger.warning(
                    'Bridge HTTP %s %s -> %s, retry %s/%s',
                    method,
                    url,
                    response.status_code,
                    attempt + 1,
                    attempts,
                )
                response.close()
                continue
            return response
        except httpx.ConnectError:
            # Peer ещё не слушает — повтор сразу не поможет (django check / старт).
            raise
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
            logger.warning(
                'Bridge HTTP %s %s transport error, retry %s/%s: %s',
                method,
                url,
                attempt + 1,
                attempts,
                exc,
            )
    if last_exc:
        raise last_exc
    raise RuntimeError('Bridge HTTP: empty retry loop')


def _stream_timeout() -> httpx.Timeout:
    base = _timeout()
    return httpx.Timeout(
        connect=min(30.0, base),
        read=max(base, _STREAM_READ_TIMEOUT),
        write=base,
        pool=base,
    )


def _is_bridge_stream_response(response: httpx.Response) -> bool:
    ctype = (response.headers.get('content-type') or '').lower()
    if 'ndjson' in ctype:
        return True
    return (response.headers.get('X-Bridge-Stream') or '').strip() == '1'


def _iter_ndjson_chunks(client: httpx.Client, response: httpx.Response) -> Iterator[Any]:
    def chunks() -> Iterator[Any]:
        try:
            for raw in response.iter_lines():
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get('done'):
                    break
                error = data.get('error')
                if error:
                    raise RuntimeError(str(error))
                if 'chunk' in data:
                    yield data['chunk']
        finally:
            response.close()

    return chunks()


def _internal_token() -> str:
    return (getattr(settings, 'BRIDGE_INTERNAL_TOKEN', '') or '').strip()


def _timeout() -> float:
    raw = getattr(settings, 'BRIDGE_HTTP_TIMEOUT', 10)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 10.0


def _retries() -> int:
    raw = getattr(settings, 'BRIDGE_HTTP_RETRIES', 2)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 2


_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None


def close_shared_http_client() -> None:
    """Закрыть процессный клиент моста (тесты / смена настроек)."""
    global _shared_client
    with _client_lock:
        client = _shared_client
        _shared_client = None
    if client is not None and not client.is_closed:
        client.close()


def _http_client(timeout: float | httpx.Timeout | None = None) -> httpx.Client:
    # Один клиент на процесс: keep-alive между bridge.call, без сокета на каждый op.
    # trust_env=False: http_proxy/HTTP_PROXY не должны перехватывать
    # /internal/bridge/* на private LAN (иначе ReadTimeout через прокси).
    global _shared_client
    with _client_lock:
        if _shared_client is None or _shared_client.is_closed:
            _shared_client = httpx.Client(
                timeout=_stream_timeout() if timeout is None else timeout,
                trust_env=False,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
            )
        return _shared_client


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


def _same_base(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.rstrip('/') == right.rstrip('/')


def _core_owned_identity_op(name: str) -> bool:
    """Роль и сессия живут на ядре: процесс модуля не читает свою cms_adp_*."""
    return name == SESSION_DEVICE_ACTIVE or name.startswith('adp.')


def _prefer_local_provider(base: str | None, name: str = '') -> bool:
    """Локальный handler только если этот процесс — владелец op.

    На ``module:<name>`` ядро всё равно регистрирует ``session.device_active``
    и ``adp.*``: пользователи и устройства живут на ядре, не в БД модуля.
    """
    role = (getattr(settings, 'ERGO_PROCESS_ROLE', '') or '').strip().lower()
    if not role.startswith('module:'):
        return True
    if _core_owned_identity_op(name):
        if not base:
            return False
        return _same_base(base, _self_base_url())
    if not base:
        return True
    return _same_base(base, _self_base_url())


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


def _json_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только JSON-примитивы. Объект user и callback по HTTP не едут."""
    safe: dict[str, Any] = {}
    for key, value in kwargs.items():
        try:
            safe[str(key)] = _json_safe(value)
        except TypeError:
            logger.debug(
                'Bridge HTTP: пропуск kwargs %s (%s)',
                key,
                type(value).__name__,
            )
    return safe


def _json_kwargs_list(args: tuple[Any, ...]) -> list[Any]:
    safe: list[Any] = []
    for value in args:
        try:
            safe.append(_json_safe(value))
        except TypeError:
            logger.debug(
                'Bridge HTTP: пропуск args (%s)',
                type(value).__name__,
            )
    return safe


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
        base = resolve_op_base_url(name)
        if handler is not None and _prefer_local_provider(base, name):
            return handler(*args, **kwargs_accepted_by_handler(handler, kwargs))

        if not base:
            return default
        try:
            return self._remote_call(base, name, args, kwargs)
        except Exception as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status == 429:
                # Лимит на ядре — ожидаемо при jwt_claims на каждый запрос; не traceback.
                logger.warning("Bridge HTTP call limited for '%s' via %s", name, base)
            else:
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
        request_id = get_request_id()
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
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
            'args': _json_kwargs_list(args),
            'kwargs': _json_kwargs(kwargs),
        }
        url = f'{base}/internal/bridge/call'
        client = _http_client()
        response = None
        try:
            response = _http_send(
                client,
                'POST',
                url,
                json=payload,
                headers=self._headers(base),
                stream=True,
            )
            response.raise_for_status()
            if _is_bridge_stream_response(response):
                return _iter_ndjson_chunks(client, response)
            # stream=True: httpx не кладёт тело в .content, пока не вызвать read().
            # Иначе обычный JSON (session.device_active и др.) даёт ResponseNotRead,
            # jwt_claims принимает это за мёртвую сессию и отвечает 401.
            response.read()
            data = response.json()
        except Exception:
            if response is not None:
                response.close()
            raise
        response.close()
        if isinstance(data, dict) and 'result' in data:
            return data['result']
        return data

    def _remote_has(self, base: str, name: str) -> bool:
        url = f'{base}/internal/bridge/has'
        response = _http_send(
            _http_client(),
            'GET',
            url,
            params={'op': name},
            headers=self._headers(base),
        )
        response.raise_for_status()
        data = response.json()
        return bool(data.get('has')) if isinstance(data, dict) else False

    def _remote_all(self, base: str, group: str) -> dict[str, Any]:
        url = f'{base}/internal/bridge/all'
        response = _http_send(
            _http_client(),
            'GET',
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
