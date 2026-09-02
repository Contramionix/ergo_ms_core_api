"""
Кэш индекса client-маршрутов (path → module).

Снижает нагрузку на GetCMSPages и sync_cms_pages: discovery выполняется
только при изменении routes.js в core/ или modules/.
"""

import logging
from pathlib import Path

from src.config.paths import CACHE_DIR
from src.config.settings.base import DJANGO_CORE_DIR, MODULES_DIR, SYSTEM_DIR
from src.core.cms.scripts import (
    build_module_url_prefixes,
    build_route_name_to_path_index,
    discover_client_routes_catalog,
)
from src.core.utils.auto_api.auto_config import ModuleDiscoverer
from src.core.utils.cache_io import read_bin_cache, write_bin_cache

logger = logging.getLogger('utils')

CACHE_FILE = CACHE_DIR / 'client_routes_index.bin'


def _max_mtime_routes_js(root: Path) -> float:
    max_mtime = 0.0
    try:
        for pattern in ('routes.js',):
            for path in root.rglob(pattern):
                if path.is_file() and 'node_modules' not in path.parts:
                    try:
                        max_mtime = max(max_mtime, path.stat().st_mtime)
                    except OSError:
                        pass
    except OSError:
        pass
    return max_mtime


def _get_fingerprint() -> dict:
    result = {}
    core_config = SYSTEM_DIR / 'core' / 'client' / 'src' / 'config' / 'routes.js'
    if core_config.exists():
        try:
            result['core_routes_js'] = core_config.stat().st_mtime
        except OSError:
            result['core_routes_js'] = 0
    else:
        result['core_routes_js'] = 0

    # Версия парсера: смена scripts.py должна инвалидировать кэш
    scripts_path = SYSTEM_DIR / 'core' / 'api' / 'src' / 'core' / 'cms' / 'scripts.py'
    try:
        result['scripts_py'] = scripts_path.stat().st_mtime if scripts_path.exists() else 0
    except OSError:
        result['scripts_py'] = 0

    for name, path in (('core', DJANGO_CORE_DIR), ('modules', MODULES_DIR)):
        p = Path(path)
        if p.exists():
            try:
                result[f'{name}_dir'] = p.stat().st_mtime
                result[f'{name}_routes'] = _max_mtime_routes_js(p)
            except OSError:
                result[f'{name}_dir'] = 0
                result[f'{name}_routes'] = 0
        else:
            result[f'{name}_dir'] = 0
            result[f'{name}_routes'] = 0

    discoverer = ModuleDiscoverer()
    result['route_modules_count'] = len(discoverer.discover_client_route_modules())
    return result


def _is_cache_valid(cached: dict) -> bool:
    if not isinstance(cached, dict):
        return False
    if cached.get('fingerprint') != _get_fingerprint():
        return False
    data = cached.get('data')
    catalog = cached.get('catalog')
    by_name = cached.get('by_route_name')
    prefixes = cached.get('module_prefixes')
    return (
        isinstance(data, dict)
        and isinstance(catalog, dict)
        and isinstance(by_name, dict)
        and isinstance(prefixes, dict)
    )


def _build_cache_payload() -> dict:
    catalog = discover_client_routes_catalog()
    data = {
        path: entry.get('module_name', 'core')
        for path, entry in catalog.items()
    }
    return {
        'fingerprint': _get_fingerprint(),
        'data': data,
        'catalog': catalog,
        'by_route_name': build_route_name_to_path_index(catalog),
        'module_prefixes': build_module_url_prefixes(catalog),
    }


def get_client_routes_index(*, use_cache: bool = True) -> dict[str, str]:
    if use_cache:
        cached = read_bin_cache(CACHE_FILE)
        if _is_cache_valid(cached):
            return dict(cached['data'])

    payload = _build_cache_payload()
    if use_cache:
        write_bin_cache(CACHE_FILE, payload)
        logger.debug('Кэш client routes index обновлён (%s путей)', len(payload['data']))
    return dict(payload['data'])


def get_client_routes_catalog(*, use_cache: bool = True) -> dict[str, dict[str, str]]:
    if use_cache:
        cached = read_bin_cache(CACHE_FILE)
        if _is_cache_valid(cached):
            return {
                path: dict(entry)
                for path, entry in cached['catalog'].items()
            }

    payload = _build_cache_payload()
    if use_cache:
        write_bin_cache(CACHE_FILE, payload)
        logger.debug('Кэш client routes catalog обновлён (%s путей)', len(payload['catalog']))
    return {
        path: dict(entry)
        for path, entry in payload['catalog'].items()
    }


def get_client_route_name_index(*, use_cache: bool = True) -> dict[str, str]:
    """route_name → path для сопоставления пунктов меню с URL-политиками."""
    if use_cache:
        cached = read_bin_cache(CACHE_FILE)
        if _is_cache_valid(cached):
            return dict(cached['by_route_name'])

    payload = _build_cache_payload()
    if use_cache:
        write_bin_cache(CACHE_FILE, payload)
    return dict(payload['by_route_name'])


def get_module_url_prefixes(*, use_cache: bool = True) -> dict[str, list[str]]:
    """module_name → список URL-префиксов (`/impuls-analysis`)."""
    if use_cache:
        cached = read_bin_cache(CACHE_FILE)
        if _is_cache_valid(cached):
            return {
                module_name: list(prefixes)
                for module_name, prefixes in cached['module_prefixes'].items()
            }

    payload = _build_cache_payload()
    if use_cache:
        write_bin_cache(CACHE_FILE, payload)
    return {
        module_name: list(prefixes)
        for module_name, prefixes in payload['module_prefixes'].items()
    }


def invalidate_client_routes_index_cache() -> None:
    """Удаляет файловый кэш индекса client-маршрутов."""
    try:
        CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        logger.warning('Не удалось удалить %s', CACHE_FILE, exc_info=True)
