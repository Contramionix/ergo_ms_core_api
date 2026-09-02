"""
Кэш списка URL (route, dotted include) — как discovered_apps.

Обход FS только при смене fingerprint; include() по-прежнему ленивый.
"""
import logging
import os
from typing import List, Optional, Tuple

from src.config.paths import CACHE_DIR
from src.config.settings.base import BASE_DIR, DJANGO_CORE_DIR, MODULES_DIR

from src.core.utils.auto_api.discovered_apps_cache import (
    _should_skip_walk_dir,
    get_discovery_dirs_fingerprint,
    max_mtime_named_narrow,
    modules_named_mtime,
)

logger = logging.getLogger('utils')

UrlEntry = Tuple[str, str, str]


def _cache_file():
    try:
        from src.core.utils.module_registry import get_discovered_apps_cache_suffix

        suffix = get_discovered_apps_cache_suffix()
    except Exception:
        suffix = 'api'
    return CACHE_DIR / f'discovered_urls_{suffix}.bin'


_in_memory_cache: Optional[tuple] = None


def clear_discovered_urls_memory_cache() -> None:
    global _in_memory_cache
    _in_memory_cache = None


def invalidate_discovered_urls_cache() -> None:
    clear_discovered_urls_memory_cache()
    for path in CACHE_DIR.glob('discovered_urls_*.bin'):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning('Не удалось удалить %s', path, exc_info=True)


def _url_fingerprint() -> dict:
    fingerprint = get_discovery_dirs_fingerprint()
    fingerprint['core_urls'] = max_mtime_named_narrow(DJANGO_CORE_DIR, 'urls.py')
    fingerprint['modules_urls'] = modules_named_mtime(
        MODULES_DIR, 'urls.py', under_api=True
    )
    fingerprint['urls_algo'] = 2
    try:
        from src.core.utils.module_registry import get_process_filter_fingerprint

        fingerprint['process_filter'] = get_process_filter_fingerprint()
    except Exception:
        fingerprint['process_filter'] = fingerprint.get('process_filter', '')
    return fingerprint


def _collect_core_url_entries(current_dir: str, current_prefix: str, current_route: str, out: list) -> None:
    if not os.path.isdir(current_dir):
        return
    try:
        names = os.listdir(current_dir)
    except OSError:
        return
    for module_name in names:
        if _should_skip_walk_dir(module_name):
            continue
        module_path = os.path.join(current_dir, module_name)
        if not os.path.isdir(module_path):
            continue
        if not os.path.exists(os.path.join(module_path, '__init__.py')):
            continue
        module_full_path = f'{current_prefix}.{module_name}' if current_prefix else module_name
        new_route = f'{current_route}{module_name}/'
        if os.path.exists(os.path.join(module_path, 'urls.py')):
            try:
                from src.core.utils.module_registry import allow_core_url_route

                if not allow_core_url_route(new_route):
                    continue
            except Exception:
                pass
            out.append((new_route, f'{module_full_path}.urls', 'core'))
        _collect_core_url_entries(module_path, module_full_path, new_route, out)


def _collect_module_api_url_entries(
    current_dir: str,
    current_module: str,
    installed_apps: list,
    out: list,
) -> None:
    app_base = current_module
    if app_base not in installed_apps:
        parts = current_module.split('.')
        if len(parts) >= 3:
            app_base = '.'.join(parts[:3])
    if app_base not in installed_apps:
        return
    if os.path.exists(os.path.join(current_dir, 'urls.py')):
        route_parts = current_module.replace('modules.', '').replace('.api', '').split('.')
        route = '/'.join(route_parts) + '/'
        out.append((route, f'{current_module}.urls', 'module'))
    if not os.path.isdir(current_dir):
        return
    try:
        names = os.listdir(current_dir)
    except OSError:
        return
    for item_name in names:
        if _should_skip_walk_dir(item_name):
            continue
        item_path = os.path.join(current_dir, item_name)
        if os.path.isdir(item_path):
            _collect_module_api_url_entries(
                item_path,
                f'{current_module}.{item_name}',
                installed_apps,
                out,
            )


def _collect_url_entries() -> List[UrlEntry]:
    from src.core.utils.path_utils import convert_path_to_dot_notation

    entries: List[UrlEntry] = []
    core_prefix = convert_path_to_dot_notation(DJANGO_CORE_DIR.relative_to(BASE_DIR.parent))
    _collect_core_url_entries(str(DJANGO_CORE_DIR), core_prefix, '', entries)

    if not os.path.isdir(MODULES_DIR):
        return entries

    from django.conf import settings
    from src.core.utils.module_registry import (
        is_module_loadable_in_process,
        is_valid_module_dir_name,
    )

    installed_apps = getattr(settings, 'INSTALLED_APPS', [])
    try:
        names = os.listdir(MODULES_DIR)
    except OSError:
        return entries
    for module_name in names:
        if _should_skip_walk_dir(module_name):
            continue
        if not is_valid_module_dir_name(module_name):
            continue
        if not is_module_loadable_in_process(module_name):
            continue
        api_path = os.path.join(MODULES_DIR, module_name, 'api')
        if os.path.isdir(api_path):
            _collect_module_api_url_entries(
                api_path,
                f'modules.{module_name}.api',
                installed_apps,
                entries,
            )
    return entries


def _entries_to_pairs(entries) -> List[Tuple[str, str]]:
    from src.core.utils.django_cli import is_lean_schema_cli

    if is_lean_schema_cli():
        return [
            (route, dotted)
            for route, dotted, source in entries
            if source != 'module'
        ]
    return [(route, dotted) for route, dotted, _source in entries]


def iter_top_level_module_prefixes(
    entries: List[Tuple[str, str]] | None = None,
) -> List[str]:
    """Имена модулей из discovered URL (первый сегмент ``<name>/…``)."""
    pairs = entries if entries is not None else get_discovered_url_entries()
    seen: set[str] = set()
    names: List[str] = []
    for route, dotted in pairs:
        if not str(dotted).startswith('modules.'):
            continue
        name = str(route).strip('/').split('/')[0]
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def get_discovered_url_entries() -> List[Tuple[str, str]]:
    """
    Список (route, dotted_module) для path(route, include(dotted)).
    """
    global _in_memory_cache
    current_fingerprint = _url_fingerprint()
    if _in_memory_cache is not None:
        from src.core.utils.cache_fingerprint import fingerprint_equal

        if fingerprint_equal(_in_memory_cache[0], current_fingerprint):
            return _in_memory_cache[1]

    from src.core.utils.cache_fingerprint import fingerprint_equal
    from src.core.utils.cache_io import read_bin_cache, write_bin_cache

    cache_path = _cache_file()
    data = read_bin_cache(cache_path)
    if data is not None:
        cached_fingerprint = data.get('fingerprint', {})
        if fingerprint_equal(cached_fingerprint, current_fingerprint):
            stored = data.get('entries')
            if stored is not None:
                pairs = _entries_to_pairs(stored)
                _in_memory_cache = (current_fingerprint, pairs)
                logger.debug('Discovered urls: загружено из кэша')
                return pairs

    entries = _collect_url_entries()
    if write_bin_cache(cache_path, {'fingerprint': current_fingerprint, 'entries': entries}):
        logger.info('Discovered urls: сохранено в кэш (%d маршрутов)', len(entries))
    pairs = _entries_to_pairs(entries)
    _in_memory_cache = (current_fingerprint, pairs)
    return pairs
