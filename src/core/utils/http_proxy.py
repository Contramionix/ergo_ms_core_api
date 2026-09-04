"""Исходящий HTTP-прокси: ERGO_HTTP_TRUST_ENV и NO_PROXY из окружения."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.request import OpenerDirector, ProxyHandler, build_opener

_TRUE = frozenset({'1', 'true', 'yes', 'on'})
_FALSE = frozenset({'0', 'false', 'no', 'off'})


def http_trust_env(environ: Mapping[str, str] | None = None) -> bool:
    """
    true — httpx/urllib берут HTTP_PROXY и NO_PROXY из окружения.
    false (по умолчанию) — внутренний LAN не идёт через прокси.
    """
    env = environ if environ is not None else os.environ
    raw = (env.get('ERGO_HTTP_TRUST_ENV') or '').strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return False


def urllib_opener(environ: Mapping[str, str] | None = None) -> OpenerDirector:
    """Opener с учётом ERGO_HTTP_TRUST_ENV."""
    if http_trust_env(environ):
        return build_opener()
    return build_opener(ProxyHandler({}))
