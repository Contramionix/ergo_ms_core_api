"""Кэш каталога аудита и списка инициаторов."""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db.models import Max

from src.core.integrations import bridge
from src.core.integrations.module_contracts import AUDIT_ACTION_DEFINITIONS_GROUP

logger = logging.getLogger('core.audit')

ACTION_DEFINITIONS_GROUP = AUDIT_ACTION_DEFINITIONS_GROUP

CATALOG_CACHE_KEY = 'audit:catalog:v2'
ACTORS_CACHE_KEY = 'audit:actors:v1'
CATALOG_CACHE_TTL = 600
ACTORS_CACHE_TTL = 600


def _normalize_action(raw: dict) -> dict | None:
    action = raw.get('action')
    if not action:
        return None
    return {
        'action': action,
        'label': raw.get('label') or action,
        'icon': raw.get('icon') or '',
        'category': raw.get('category') or '',
        'category_label': raw.get('category_label') or '',
        'severity': raw.get('severity') or 'info',
    }


def _build_catalog() -> dict:
    catalog: dict = {}
    for key, section in bridge.all(ACTION_DEFINITIONS_GROUP).items():
        if not isinstance(section, dict):
            logger.warning('Каталог аудита: секция %r не dict, пропуск', key)
            continue
        module = section.get('module') or key
        actions: dict = {}
        for raw in section.get('actions') or []:
            spec = _normalize_action(raw)
            if spec is None:
                logger.warning('Каталог аудита: действие без ключа в %r', module)
                continue
            actions[spec['action']] = spec
        catalog[module] = {
            'module': module,
            'module_label': section.get('module_label') or module,
            'actions': actions,
        }
    return catalog


def invalidate_audit_catalog_cache() -> None:
    cache.delete(CATALOG_CACHE_KEY)
    cache.delete(ACTORS_CACHE_KEY)


def get_catalog() -> dict:
    """{module: {'module_label': str, 'actions': {action: spec}}}."""
    cached = cache.get(CATALOG_CACHE_KEY)
    if cached is not None:
        return cached
    catalog = _build_catalog()
    cache.set(CATALOG_CACHE_KEY, catalog, CATALOG_CACHE_TTL)
    return catalog


def get_action_spec(source_module: str, action: str) -> dict | None:
    section = get_catalog().get(source_module or '')
    if not section:
        return None
    return section['actions'].get(action or '')


def find_action_spec(source_module: str, action: str) -> dict | None:
    """Спецификация действия: сначала модуль источника, затем любой модуль каталога."""
    action_key = action or ''
    if not action_key:
        return None
    spec = get_action_spec(source_module, action_key)
    if spec is not None:
        return spec
    for section in get_catalog().values():
        spec = section['actions'].get(action_key)
        if spec is not None:
            return spec
    return None


def get_flat_actions() -> list[dict]:
    """Плоский список действий для фильтров/подписей на клиенте."""
    result: list[dict] = []
    for section in get_catalog().values():
        for spec in section['actions'].values():
            result.append({
                'module': section['module'],
                'module_label': section['module_label'],
                **spec,
            })
    return result


def get_modules() -> list[dict]:
    """Список модулей-источников для фильтра."""
    return [
        {'module': section['module'], 'module_label': section['module_label']}
        for section in get_catalog().values()
    ]


def _build_distinct_actors(limit: int = 500, scope: dict | None = None) -> list[dict]:
    from .models import AuditActor, AuditEvent

    scope_filter = {'scope__contains': scope} if scope else {}

    if not scope:
        dimension = list(
            AuditActor.objects
            .order_by('label')
            .values('filter_value', 'label')[:limit]
        )
        if dimension:
            return [
                {'value': row['filter_value'], 'label': row['label']}
                for row in dimension
            ]

    from urllib.parse import quote

    linked = (
        AuditEvent.objects
        .filter(actor_id__isnull=False, actor__public_id__isnull=False, **scope_filter)
        .values('actor__public_id')
        .annotate(label=Max('actor_label'))
        .order_by('label')[:limit]
    )
    orphans = (
        AuditEvent.objects
        .filter(actor_id__isnull=True, **scope_filter)
        .exclude(actor_label='')
        .values('actor_label')
        .distinct()
        .order_by('actor_label')
    )

    result: list[dict] = []
    for row in linked:
        public_id = row['actor__public_id']
        label = (row['label'] or '').strip() or str(public_id)
        result.append({'value': str(public_id), 'label': label})

    for row in orphans:
        label = (row['actor_label'] or '').strip()
        if not label:
            continue
        result.append({'value': f'label:{quote(label, safe="")}', 'label': label})

    result.sort(key=lambda item: item['label'].casefold())
    return result


def get_distinct_actors(limit: int = 500, scope: dict | None = None) -> list[dict]:
    """Уникальные инициаторы из журнала для фильтра UI."""
    if scope:
        scope_suffix = ','.join(f'{k}={scope[k]}' for k in sorted(scope))
    else:
        scope_suffix = 'all'
    cache_key = f'{ACTORS_CACHE_KEY}:{limit}:{scope_suffix}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = _build_distinct_actors(limit=limit, scope=scope)
    cache.set(cache_key, result, ACTORS_CACHE_TTL)
    return result
