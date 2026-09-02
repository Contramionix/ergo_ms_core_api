"""Discovery и синхронизация каталога API-эндпоинтов для политик policy_type=api."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.urls import URLPattern, URLResolver, get_resolver


API_PREFIX = '/api/'


@dataclass(frozen=True)
class ApiEndpointsSyncResult:
    paths: frozenset[str]
    added: frozenset[str]
    removed: frozenset[str]
    unchanged: frozenset[str]


def normalize_api_path(path: str) -> str:
    """Нормализовать path: ведущий /, без query, схлопнутые слеши."""
    value = (path or '').strip()
    if not value:
        return '/'
    if not value.startswith('/'):
        value = f'/{value}'
    while '//' in value:
        value = value.replace('//', '/')
    return value


def _route_str(pattern) -> str:
    try:
        return str(pattern)
    except Exception:
        return ''


def _walk_patterns(patterns: Iterable, prefix: str = '') -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    for entry in patterns:
        if isinstance(entry, URLResolver):
            route = _route_str(entry.pattern)
            collected.extend(_walk_patterns(entry.url_patterns, f'{prefix}{route}'))
        elif isinstance(entry, URLPattern):
            route = _route_str(entry.pattern)
            full = normalize_api_path(f'{prefix}{route}')
            collected.append((full, entry.name or ''))
    return collected


def _guess_module_name(path: str) -> str:
    """Определить module_name по префиксу path (/api/<segment>/...)."""
    parts = [p for p in path.strip('/').split('/') if p]
    if len(parts) < 2:
        return 'core'
    segment = parts[1]
    core_segments = {
        'cms', 'settings', 'system', 'audit', 'notifications',
        'messenger', 'realtime', 'utils', 'search', 'client_monitor',
        'internal',
    }
    if segment in core_segments:
        return 'core'
    from src.core.cms.adp.services.permission_catalog import canonicalize_module_name
    return canonicalize_module_name(segment)


def discover_api_endpoints() -> dict[str, dict]:
    """Сканировать Django URLConf → {path: {name, module_name}} только под /api/."""
    resolver = get_resolver()
    result: dict[str, dict] = {}
    for path, name in _walk_patterns(resolver.url_patterns, ''):
        if not path.startswith(API_PREFIX) or path in (API_PREFIX.rstrip('/'), API_PREFIX):
            continue
        module_name = _guess_module_name(path)
        prev = result.get(path)
        if prev is None or (name and not prev.get('name')):
            result[path] = {
                'name': name or '',
                'module_name': module_name,
            }
    return result


def sync_api_endpoints(*, remove_orphans: bool = False, dry_run: bool = False) -> ApiEndpointsSyncResult:
    """Синхронизировать ApiEndpoint с discovery Django urls."""
    from src.core.cms.models import ApiEndpoint

    discovered = discover_api_endpoints()
    discovered_paths = set(discovered.keys())
    existing = set(ApiEndpoint.objects.values_list('path', flat=True))

    added = discovered_paths - existing
    removed = (existing - discovered_paths) if remove_orphans else set()
    unchanged = discovered_paths & existing

    if not dry_run:
        for path in sorted(discovered_paths):
            meta = discovered[path]
            ApiEndpoint.objects.update_or_create(
                path=path,
                defaults={
                    'name': meta.get('name') or '',
                    'module_name': meta.get('module_name') or 'core',
                },
            )
        if removed:
            ApiEndpoint.objects.filter(path__in=removed).delete()

    return ApiEndpointsSyncResult(
        paths=frozenset(discovered_paths),
        added=frozenset(added),
        removed=frozenset(removed),
        unchanged=frozenset(unchanged),
    )
