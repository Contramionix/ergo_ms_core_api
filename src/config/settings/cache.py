"""
Настройки Django CACHES из переменных окружения (.env).

Режимы API_CACHE_BACKEND:
- locmem — in-process (по умолчанию; разработка, один воркер)
- file   — файловый кэш в virtual_env/cache/django
- redis  — Redis (общий кэш для нескольких воркеров)
- dummy  — отключён (тесты, отладка)
"""

from src.config.env import env
from src.config.redis_runtime import (
    cache_redis_url,
    effective_cache_backend,
    redis_connection_options,
)
from src.config.paths import CACHE_DIR

CACHE_BACKEND = effective_cache_backend()
CACHE_DEFAULT_TIMEOUT = env.int('API_CACHE_DEFAULT_TIMEOUT', default=300)

_django_cache_dir = CACHE_DIR / 'django'

if CACHE_BACKEND == 'redis':
    _redis_url = cache_redis_url()
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _redis_url,
            'TIMEOUT': CACHE_DEFAULT_TIMEOUT,
            'OPTIONS': redis_connection_options(),
        },
    }
elif CACHE_BACKEND == 'locmem':
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'ergo-default-cache',
            'TIMEOUT': CACHE_DEFAULT_TIMEOUT,
        },
    }
elif CACHE_BACKEND == 'dummy':
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
        },
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': str(_django_cache_dir),
            'TIMEOUT': CACHE_DEFAULT_TIMEOUT,
        },
    }

# TTL сериализованного snapshot прав в Django cache (секунды); 0 — отключить
PERMISSIONS_SNAPSHOT_CACHE_TTL = env.int('API_PERMISSIONS_SNAPSHOT_CACHE_TTL', default=60)
