"""
Внутренний HTTP API ModuleBridge (/internal/bridge/*).

Не для браузера: только service-to-service с BRIDGE_INTERNAL_TOKEN.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
from collections.abc import Iterator
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from src.core.integrations import bridge
from src.core.integrations.module_contracts import (
    ADP_CHECK_API_ACCESS,
    ADP_CHECK_MODULE_PERMISSION,
    ADP_IS_ADMIN,
    SESSION_DEVICE_ACTIVE,
)
from src.core.integrations.transports.bind_kwargs import kwargs_accepted_by_handler
from src.core.utils.request_id import request_id_from_meta

# jwt_claims на каждом запросе модуля: не режем служебный вход лимитом общего моста.
_RATE_EXEMPT_OPS = frozenset({
    SESSION_DEVICE_ACTIVE,
    ADP_IS_ADMIN,
    ADP_CHECK_API_ACCESS,
    ADP_CHECK_MODULE_PERMISSION,
})

logger = logging.getLogger('integrations.bridge.internal')

_TOKEN_HEADER = 'HTTP_X_BRIDGE_TOKEN'


def _is_loopback(request: HttpRequest) -> bool:
    """True, если клиентский адрес — loopback (127.0.0.1 / ::1)."""
    addr = (request.META.get('REMOTE_ADDR') or '').strip()
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return addr.lower() in ('localhost',)


def _peer_allowed(request: HttpRequest) -> bool:
    """Мост не для публичного интернета: loopback; private при HTTP-мосте или microservice."""
    addr = (request.META.get('REMOTE_ADDR') or '').strip()
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr.lower() in ('localhost',)
    if ip.is_loopback:
        return True
    runtime = (getattr(settings, 'MODULE_RUNTIME', 'monolith') or 'monolith').strip().lower()
    transport = (getattr(settings, 'BRIDGE_TRANSPORT', 'local') or 'local').strip().lower()
    peer_http = runtime in ('microservice', 'split') or transport == 'http'
    if peer_http and (ip.is_private or ip.is_link_local):
        return True
    return False


def _token_ok(request: HttpRequest) -> bool:
    expected = (getattr(settings, 'BRIDGE_INTERNAL_TOKEN', '') or '').strip()
    if not expected:
        # Без токена: только DEBUG + loopback. В prod токен обязателен.
        if getattr(settings, 'DEBUG', False) and _is_loopback(request):
            return True
        return False
    got = request.META.get(_TOKEN_HEADER, '') or request.headers.get('X-Bridge-Token', '')
    if not isinstance(got, str):
        got = str(got)
    return hmac.compare_digest(got, expected)


def _unauthorized() -> JsonResponse:
    return JsonResponse({'detail': 'Unauthorized'}, status=401)


def _rate_limited(request: HttpRequest) -> bool:
    """True, если служебный мост превысил BRIDGE_INTERNAL_RATE."""
    raw = (getattr(settings, 'BRIDGE_INTERNAL_RATE', '60/minute') or '60/minute').strip()
    try:
        count_s, period = raw.split('/', 1)
        limit = int(count_s)
    except (TypeError, ValueError):
        limit, period = 60, 'minute'
    window = 60 if 'minute' in period else 1
    addr = (request.META.get('REMOTE_ADDR') or 'unknown').strip()
    key = f'bridge:rl:{addr}'
    try:
        current = cache.get(key)
        if current is None:
            cache.set(key, 1, timeout=window)
            return False
        if int(current) >= limit:
            return True
        cache.incr(key)
    except Exception:
        # Сломанный кэш не должен глушить мост: токен уже проверен.
        return False
    return False


def _call_op_name(request: HttpRequest) -> str:
    if request.method != 'POST':
        return ''
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ''
    op = body.get('op')
    return op if isinstance(op, str) else ''


def _guard(request: HttpRequest):
    request_id_from_meta(request.META)
    if not _peer_allowed(request) or not _token_ok(request):
        return _unauthorized()
    if _call_op_name(request) in _RATE_EXEMPT_OPS:
        return None
    if _rate_limited(request):
        return JsonResponse({'detail': 'Too many requests'}, status=429)
    return None


def _json_safe_callable(key: str, value: Any) -> dict[str, Any] | tuple[str, Any] | None:
    """Callable → JSON: spec на функции, либо вызов без аргументов (rate)."""
    spec = getattr(value, '_bridge_json', None)
    if isinstance(spec, dict):
        return spec
    if key == 'rate':
        try:
            resolved = value()
            json.dumps(resolved)
            return (key, resolved)
        except Exception:
            return None
    return None


def _json_safe_providers(providers: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только JSON-сериализуемые значения группы."""
    safe: dict[str, Any] = {}
    for key, obj in providers.items():
        try:
            json.dumps(obj, default=None)
        except (TypeError, ValueError):
            if isinstance(obj, dict):
                trimmed: dict[str, Any] = {}
                for field, value in obj.items():
                    if callable(value):
                        encoded = _json_safe_callable(field, value)
                        if isinstance(encoded, dict):
                            trimmed.update(encoded)
                        elif isinstance(encoded, tuple):
                            trimmed[encoded[0]] = encoded[1]
                        continue
                    try:
                        json.dumps(value)
                        trimmed[field] = value
                    except (TypeError, ValueError):
                        continue
                safe[key] = trimmed
            continue
        else:
            safe[key] = obj
    return safe


def _is_bridge_iterator(value: Any) -> bool:
    if isinstance(value, (str, bytes, bytearray, dict, list, tuple)):
        return False
    return isinstance(value, Iterator)


def _stream_bridge_result(result: Iterator[Any]) -> StreamingHttpResponse:
    def event_stream():
        try:
            for item in result:
                try:
                    json.dumps(item)
                except (TypeError, ValueError):
                    logger.exception('internal bridge stream chunk is not JSON')
                    yield json.dumps({'error': 'Handler result is not JSON-serializable'}) + '\n'
                    return
                yield json.dumps({'chunk': item}, ensure_ascii=False) + '\n'
            yield json.dumps({'done': True}, ensure_ascii=False) + '\n'
        except Exception:
            logger.exception('internal bridge stream failed')
            yield json.dumps({'error': 'Handler error'}) + '\n'

    response = StreamingHttpResponse(event_stream(), content_type='application/x-ndjson')
    response['X-Bridge-Stream'] = '1'
    return response


@csrf_exempt
@require_POST
def bridge_call(request: HttpRequest) -> HttpResponse:
    blocked = _guard(request)
    if blocked is not None:
        return blocked
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'detail': 'Invalid JSON'}, status=400)

    op = body.get('op')
    if not op or not isinstance(op, str):
        return JsonResponse({'detail': 'op is required'}, status=400)

    args = body.get('args') or []
    kwargs = body.get('kwargs') or {}
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return JsonResponse({'detail': 'args must be list, kwargs must be object'}, status=400)

    # Только локальный провайдер — иначе цикл remote→remote.
    handler = bridge.local_providers().get(op)

    if handler is None:
        return JsonResponse({'detail': f'Provider {op!r} not found locally'}, status=404)

    try:
        result = handler(*args, **kwargs_accepted_by_handler(handler, kwargs))
    except Exception:
        logger.exception('internal bridge call failed for %s', op)
        return JsonResponse({'detail': 'Handler error'}, status=500)

    if _is_bridge_iterator(result):
        return _stream_bridge_result(result)

    try:
        json.dumps(result)
    except (TypeError, ValueError):
        return JsonResponse({'detail': 'Handler result is not JSON-serializable'}, status=500)

    return JsonResponse({'result': result})


@csrf_exempt
@require_GET
def bridge_has(request: HttpRequest) -> JsonResponse:
    blocked = _guard(request)
    if blocked is not None:
        return blocked
    op = request.GET.get('op', '').strip()
    if not op:
        return JsonResponse({'detail': 'op is required'}, status=400)

    has_local = op in bridge.local_providers()
    return JsonResponse({'has': bool(has_local)})


@csrf_exempt
@require_GET
def bridge_all(request: HttpRequest) -> JsonResponse:
    blocked = _guard(request)
    if blocked is not None:
        return blocked
    group = request.GET.get('group', '').strip()
    if not group:
        return JsonResponse({'detail': 'group is required'}, status=400)

    local = bridge.local_group(group)
    return JsonResponse({'providers': _json_safe_providers(local)})
