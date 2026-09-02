"""Клиент Meilisearch с проверкой доступности."""

from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger('search')

_client = None
_available: bool | None = None


def is_search_enabled() -> bool:
  return bool(getattr(settings, 'ERGO_SEARCH_ENABLED', True))


_TEMPLATE_MEILI_KEY = 'ergo_ms_dev_meili_key'


def _master_key_is_insecure(key: str) -> bool:
  stripped = (key or '').strip()
  return (not stripped) or stripped.lower() == _TEMPLATE_MEILI_KEY


def get_meili_client():
  global _client
  if _client is not None:
    return _client
  if not is_search_enabled():
    return None
  host = getattr(settings, 'MEILI_HOST', '') or ''
  if not host:
    return None
  api_key = getattr(settings, 'MEILI_MASTER_KEY', '') or ''
  if _master_key_is_insecure(api_key):
    from src.config.deploy import is_development

    if not is_development():
      logger.warning(
        'MEILI_MASTER_KEY пуст или из шаблона — клиент поиска не подключается'
      )
      return None
  try:
    import meilisearch
  except ImportError:
    logger.warning('Пакет meilisearch не установлен — поиск через fallback')
    return None
  timeout = getattr(settings, 'MEILI_SEARCH_TIMEOUT_SEC', 5.0)
  _client = meilisearch.Client(host, api_key or None, timeout=timeout)
  return _client


def reset_client_cache() -> None:
  global _client, _available
  _client = None
  _available = None


@lru_cache(maxsize=1)
def _ping_once() -> bool:
  client = get_meili_client()
  if client is None:
    return False
  try:
    health = client.health()
    return health.get('status') == 'available'
  except Exception:
    logger.warning('Meilisearch недоступен — используется fallback', exc_info=True)
    return False


def is_meili_available(*, force_check: bool = False) -> bool:
  global _available
  if not is_search_enabled():
    return False
  if force_check:
    _ping_once.cache_clear()
    _available = _ping_once()
    return _available
  if _available is None:
    _available = _ping_once()
  return _available
