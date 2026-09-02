"""
Сценарий Redis: URL для кэша, channel layer и Celery.

Параметры подключения — секция redis в databases.yaml (с fallback на REDIS_* в .env).
Включение — ERGO_BROKER=redis или явный REDIS_ENABLED.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from src.config.env import env
from src.config.ergo_runtime import redis_mode_enabled
from src.config.paths import SYSTEM_DIR

_KOMBU_REDIS_RESP2_PATCHED = False
_HOST_LOOPBACK = '127.0.0.1'
_REDIS_SECTION_CACHE: dict[str, Any] | None = None


def running_in_container() -> bool:
    """Процесс внутри Docker-контейнера (не хост ergoms dev / portable Redis)."""
    if Path('/.dockerenv').is_file():
        return True
    cgroup = Path('/proc/self/cgroup')
    if cgroup.is_file():
        try:
            return 'docker' in cgroup.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            pass
    return False


def _docker_service_redis_names() -> frozenset[str]:
    service = env.str('DOCKER_SERVICE_REDIS', default='redis').strip().lower() or 'redis'
    return frozenset({service, 'redis'})


def _load_redis_section() -> dict[str, Any]:
    global _REDIS_SECTION_CACHE
    if _REDIS_SECTION_CACHE is not None:
        return _REDIS_SECTION_CACHE

    from src.core.utils.database.config_manager import (
        _get_cached_yaml,
        resolve_databases_yaml_path,
    )

    databases = _get_cached_yaml(resolve_databases_yaml_path(SYSTEM_DIR)) or {}
    section = databases.get('redis')
    if isinstance(section, dict):
        _REDIS_SECTION_CACHE = dict(section)
    else:
        _REDIS_SECTION_CACHE = {}
    return _REDIS_SECTION_CACHE


def _section_int(section: dict[str, Any], key: str, default: int) -> int:
    raw = section.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def effective_redis_host() -> str:
    """
    Хост Redis для текущего runtime.

    На хосте имя сервиса compose ``redis`` не резолвится — 127.0.0.1.
    """
    section = _load_redis_section()
    host = str(section.get('host') or '').strip()
    if not host:
        host = env.str('REDIS_HOST', default=_HOST_LOOPBACK).strip() or _HOST_LOOPBACK
    if not running_in_container() and host.strip().lower() in _docker_service_redis_names():
        return _HOST_LOOPBACK
    return host


def _normalize_redis_url(url: str) -> str:
    if running_in_container():
        return url
    parts = urlsplit(url)
    if not parts.hostname or parts.hostname.strip().lower() not in _docker_service_redis_names():
        return url
    port = parts.port or redis_port()
    userinfo = ''
    if parts.password is not None:
        user = parts.username or ''
        userinfo = f'{user}:{parts.password}@'
    elif parts.username:
        userinfo = f'{parts.username}@'
    netloc = f'{userinfo}{_HOST_LOOPBACK}:{port}'
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redis_enabled() -> bool:
    return redis_mode_enabled()


def effective_cache_backend() -> str:
    explicit = env.str('API_CACHE_BACKEND', default='').strip().lower()
    if explicit:
        return explicit
    if redis_enabled():
        return 'redis'
    return 'locmem'


def effective_channel_layer_backend() -> str:
    explicit = env.str('CHANNEL_LAYER_BACKEND', default='').strip().lower()
    if explicit:
        return explicit
    if redis_enabled():
        return 'redis'
    return 'memory'


def effective_celery_broker_backend() -> str:
    explicit = env.str('CELERY_BROKER_BACKEND', default='').strip().lower()
    if explicit:
        return explicit
    if redis_enabled():
        return 'redis'
    return 'local'


def redis_host() -> str:
    return effective_redis_host()


def redis_port() -> int:
    section = _load_redis_section()
    if 'port' in section:
        return _section_int(section, 'port', 6379)
    raw = env.str('REDIS_PORT', default='6379').strip() or '6379'
    try:
        return int(raw)
    except ValueError:
        return 6379


def redis_db_cache() -> int:
    section = _load_redis_section()
    if 'db_cache' in section:
        return _section_int(section, 'db_cache', 1)
    raw = env.str('REDIS_DB_CACHE', default='1').strip() or '1'
    try:
        return int(raw)
    except ValueError:
        return 1


def redis_db_channel() -> int:
    section = _load_redis_section()
    if 'db_channel' in section:
        return _section_int(section, 'db_channel', 0)
    raw = env.str('REDIS_DB_CHANNEL', default='0').strip() or '0'
    try:
        return int(raw)
    except ValueError:
        return 0


def redis_db_celery_broker() -> int:
    section = _load_redis_section()
    if 'db_celery_broker' in section:
        return _section_int(section, 'db_celery_broker', 2)
    raw = env.str('REDIS_DB_CELERY_BROKER', default='2').strip() or '2'
    try:
        return int(raw)
    except ValueError:
        return 2


def redis_db_celery_result() -> int:
    section = _load_redis_section()
    if 'db_celery_result' in section:
        return _section_int(section, 'db_celery_result', 3)
    raw = env.str('REDIS_DB_CELERY_RESULT', default='3').strip() or '3'
    try:
        return int(raw)
    except ValueError:
        return 3


def redis_password() -> str:
    """Пароль Redis из databases.yaml → redis.password (пусто = без AUTH)."""
    section = _load_redis_section()
    raw = section.get('password', '')
    if raw is None:
        return ''
    return str(raw).strip()


def redis_username() -> str:
    """ACL-пользователь Redis из databases.yaml → redis.user (пусто = default)."""
    section = _load_redis_section()
    raw = section.get('user', '')
    if raw is None:
        return ''
    return str(raw).strip()


def redis_url(db: int) -> str:
    host = redis_host()
    port = redis_port()
    password = redis_password()
    username = redis_username()
    if password and username:
        return (
            f'redis://{quote(username, safe="")}:{quote(password, safe="")}'
            f'@{host}:{port}/{db}'
        )
    if password:
        return f'redis://:{quote(password, safe="")}@{host}:{port}/{db}'
    return f'redis://{host}:{port}/{db}'


def sanitize_celery_redis_url(url: str) -> str:
    """
    Убирает query-параметры из URL Celery broker/result.

    ``?protocol=2`` нужен redis-py (Django cache), но kombu передаёт query в
    Connection._init_params() и падает с unexpected keyword argument 'protocol'.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', parts.fragment))


def _celery_redis_url(db: int) -> str:
    return redis_url(db)


def cache_redis_url() -> str:
    explicit = env.str('API_CACHE_REDIS_URL', default='').strip()
    if explicit:
        return _normalize_redis_url(explicit)
    return redis_url(redis_db_cache())


def channel_layer_redis_url() -> str:
    explicit = env.str('CHANNEL_LAYER_REDIS_URL', default='').strip()
    if explicit:
        return _normalize_redis_url(explicit)
    return redis_url(redis_db_channel())


def celery_broker_redis_url() -> str:
    explicit = env.str('CELERY_BROKER_URL', default='').strip()
    if explicit:
        return _normalize_redis_url(sanitize_celery_redis_url(explicit))
    return _celery_redis_url(redis_db_celery_broker())


def celery_result_redis_url() -> str:
    explicit = env.str('CELERY_RESULT_BACKEND', default='').strip()
    if explicit:
        return _normalize_redis_url(sanitize_celery_redis_url(explicit))
    return _celery_redis_url(redis_db_celery_result())


def uses_redis_celery_backend(broker_url: str, result_backend: str = '') -> bool:
    for url in (broker_url, result_backend):
        if url.startswith('redis://') or url.startswith('rediss://'):
            return True
    return False


def ensure_kombu_redis_resp2() -> None:
    """RESP2 для portable Redis 5.x / совместимости с redis-py 8+."""
    global _KOMBU_REDIS_RESP2_PATCHED
    if _KOMBU_REDIS_RESP2_PATCHED:
        return

    from kombu.transport import redis as kombu_redis_transport

    original_connparams = kombu_redis_transport.Channel._connparams

    def _connparams_with_resp2(self, asynchronous=False):
        params = original_connparams(self, asynchronous=asynchronous)
        params['protocol'] = 2
        return params

    kombu_redis_transport.Channel._connparams = _connparams_with_resp2
    _KOMBU_REDIS_RESP2_PATCHED = True


def redis_connection_options() -> dict[str, int]:
    return {'protocol': 2}


def redis_channel_connection_options() -> dict[str, int | None]:
    return {
        'protocol': 2,
        'socket_timeout': None,
    }
