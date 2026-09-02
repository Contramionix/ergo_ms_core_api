"""
Единая точка инвалидации кэшей ядра.

Цели (targets):
  django       — Django cache.clear() (меню, уведомления, аудит, device session, …)
  menu         — bump версии меню (без полного clear)
  audit        — каталог аудита
  permissions  — in-process permission_catalog + server snapshot прав
  apps         — discovered_apps (файл + память процесса)
  celery       — celery_*.bin
  client_routes — client_routes_index.bin
  modules_env  — modules_env.bin
  file         — все файловые .bin в virtual_env/cache (apps + celery + routes + env)
  memory       — in-process кэши без Django/file
  all          — всё перечисленное
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from django.core.cache import cache

from src.config.paths import CACHE_DIR

logger = logging.getLogger('utils.cache')

ALL_TARGETS = (
    'django',
    'menu',
    'audit',
    'permissions',
    'apps',
    'celery',
    'client_routes',
    'modules_env',
    'file',
    'memory',
    'all',
)

_FILE_BIN_NAMES = (
    'discovered_apps.bin',
    'celery_routes_queues.bin',
    'celery_queues.bin',
    'celery_beat_schedule.bin',
    'client_routes_index.bin',
    'modules_env.bin',
)

_CELERY_BIN_NAMES = (
    'celery_routes_queues.bin',
    'celery_queues.bin',
    'celery_beat_schedule.bin',
)


def _cache_dir() -> Path:
    return CACHE_DIR


def _delete_bin_files(names: Iterable[str]) -> list[str]:
    deleted: list[str] = []
    root = _cache_dir()
    for name in names:
        path = root / name
        try:
            if path.is_file():
                path.unlink()
                deleted.append(name)
        except OSError as exc:
            logger.warning('Не удалось удалить %s: %s', path, exc)
    return deleted


def invalidate_django_cache() -> str:
    cache.clear()
    return 'Django cache очищен'


def invalidate_menu_cache() -> str:
    from src.core.cms.adp.menu.menu_cache import bump_menu_cache_version

    version = bump_menu_cache_version()
    return f'Версия кэша меню: {version}'


def invalidate_audit_cache() -> str:
    from src.core.audit.catalog import invalidate_audit_catalog_cache

    invalidate_audit_catalog_cache()
    return 'Кэш каталога аудита сброшен'


def invalidate_permissions_cache() -> str:
    from src.core.cms.adp.services.permission_catalog import clear_cache as clear_permission_catalog
    from src.core.cms.adp.services.permissions_snapshot_cache import invalidate_all_permissions_snapshots

    clear_permission_catalog()
    invalidate_all_permissions_snapshots()
    return 'Кэш прав (catalog + snapshot) сброшен'


def invalidate_apps_cache() -> str:
    from src.core.utils.auto_api.discovered_apps_cache import invalidate_discovered_apps_cache

    invalidate_discovered_apps_cache()
    return 'Кэш discovered_apps сброшен'


def invalidate_celery_file_cache() -> str:
    deleted = _delete_bin_files(_CELERY_BIN_NAMES)
    return f'Файлы Celery удалены: {", ".join(deleted) or "нет"}'


def invalidate_client_routes_cache() -> str:
    from src.core.cms.client_routes_cache import invalidate_client_routes_index_cache

    invalidate_client_routes_index_cache()
    return 'Кэш client routes index сброшен'


def invalidate_modules_env_cache() -> str:
    deleted = _delete_bin_files(('modules_env.bin',))
    return f'modules_env.bin: {"удалён" if deleted else "не найден"}'


def invalidate_file_caches() -> str:
    deleted = _delete_bin_files(_FILE_BIN_NAMES)
    try:
        from src.core.utils.auto_api.discovered_urls_cache import invalidate_discovered_urls_cache

        invalidate_discovered_urls_cache()
        deleted.append('discovered_urls_*.bin')
    except Exception:
        logger.debug('invalidate discovered_urls skipped', exc_info=True)
    return f'Файловые кэши удалены: {", ".join(deleted) or "нет"}'


def invalidate_memory_caches() -> str:
    from src.core.cms.adp.services.permission_catalog import clear_cache as clear_permission_catalog
    from src.core.utils.auto_api.discovered_apps_cache import clear_discovered_apps_memory_cache
    from src.core.utils.auto_api.discovered_urls_cache import clear_discovered_urls_memory_cache

    parts: list[str] = []
    clear_permission_catalog()
    parts.append('permission_catalog')
    clear_discovered_apps_memory_cache()
    parts.append('discovered_apps (memory)')
    clear_discovered_urls_memory_cache()
    parts.append('discovered_urls (memory)')
    try:
        from src.core.utils.geoip import reset_geoip_reader_cache

        reset_geoip_reader_cache()
        parts.append('GeoIP reader')
    except Exception:
        logger.debug('GeoIP cache reset skipped', exc_info=True)
    return ', '.join(parts)


_TARGET_HANDLERS = {
    'django': invalidate_django_cache,
    'menu': invalidate_menu_cache,
    'audit': invalidate_audit_cache,
    'permissions': invalidate_permissions_cache,
    'apps': invalidate_apps_cache,
    'celery': invalidate_celery_file_cache,
    'client_routes': invalidate_client_routes_cache,
    'modules_env': invalidate_modules_env_cache,
    'file': invalidate_file_caches,
    'memory': invalidate_memory_caches,
}


def _expand_targets(targets: Iterable[str]) -> list[str]:
    expanded: list[str] = []
    for raw in targets:
        name = (raw or '').strip().lower()
        if not name:
            continue
        if name == 'all':
            for key in (
                'django',
                'menu',
                'audit',
                'permissions',
                'apps',
                'celery',
                'client_routes',
                'modules_env',
            ):
                if key not in expanded:
                    expanded.append(key)
            continue
        if name not in _TARGET_HANDLERS:
            raise ValueError(f'Неизвестная цель кэша: {name!r}. Доступны: {", ".join(ALL_TARGETS)}')
        if name not in expanded:
            expanded.append(name)
    return expanded


def invalidate_cache_targets(targets: Iterable[str]) -> dict[str, str]:
    """Инвалидирует перечисленные цели. Возвращает {target: сообщение}."""
    results: dict[str, str] = {}
    for target in _expand_targets(targets):
        handler = _TARGET_HANDLERS[target]
        try:
            results[target] = handler()
            logger.info('invalidate cache [%s]: %s', target, results[target])
        except Exception as exc:
            msg = f'Ошибка: {exc}'
            results[target] = msg
            logger.exception('invalidate cache [%s] failed', target)
    return results


def warmup_file_caches() -> str:
    from src.core.utils.warmup_file_caches import run_file_cache_warmup

    run_file_cache_warmup(include_modules_env=True)
    return 'warmup_caches выполнен'
