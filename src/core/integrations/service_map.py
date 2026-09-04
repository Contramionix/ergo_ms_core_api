"""
Карта HTTP-сервисов ModuleBridge (MODULE_RUNTIME=microservice).

Источники:
- ``BRIDGE_SERVICE_URLS`` — ``name=url,name2=url2``
- ``modules/<name>/api/bridge_manifest.yaml`` — ops/groups владельца
- ``BRIDGE_CORE_URL`` — URL процесса ядра для обратных вызовов
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from src.config.paths import SYSTEM_DIR

_DEPLOYMENT_DIR = SYSTEM_DIR / 'core' / 'deployment'
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from lifecycle.modules.colocate import (  # noqa: E402
    is_colocated_url,
    parse_bridge_colocate,
    parse_service_urls,
    this_process_hosts,
)

logger = logging.getLogger('integrations.bridge')


def _modules_dir() -> Path | None:
    modules_dir = getattr(settings, 'MODULES_DIR', None)
    if modules_dir is None:
        return None
    path = Path(modules_dir)
    return path if path.is_dir() else None


def load_bridge_manifest(module_name: str) -> dict[str, Any]:
    """Читает ``bridge_manifest.yaml`` модуля (пустой dict, если нет файла)."""
    root = _modules_dir()
    if root is None:
        return {}
    path = root / module_name / 'api' / 'bridge_manifest.yaml'
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        logger.exception('Не удалось прочитать bridge_manifest: %s', path)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def build_service_map() -> dict[str, Any]:
    """
    Собирает маршрутизацию:

    {
      'urls': {service: base_url},
      'op_owners': {op_name: service},
      'group_owners': {group: set(service)},
      'core_url': str | None,
    }
    """
    urls = parse_service_urls(getattr(settings, 'BRIDGE_SERVICE_URLS', '') or '')
    core_url = (getattr(settings, 'BRIDGE_CORE_URL', '') or '').strip().rstrip('/') or None

    op_owners: dict[str, str] = {}
    group_owners: dict[str, set[str]] = {}

    split_raw = (
        getattr(settings, 'MICROSERVICE_MODULES', None)
        or ''
        or ''
    )
    if not isinstance(split_raw, str):
        split_names = frozenset(str(x) for x in split_raw)
    else:
        split_names = frozenset(m.strip() for m in split_raw.split(',') if m.strip())

    # Если список пуст — манифесты всех сервисов из URL-карты.
    scan_names = split_names or frozenset(urls.keys())

    for name in sorted(scan_names):
        manifest = load_bridge_manifest(name)
        service = str(manifest.get('service') or name).strip() or name
        if service not in urls and name in urls:
            # манифест переименовал service — оставляем url по имени папки
            urls.setdefault(service, urls[name])
        for op in manifest.get('ops') or []:
            op_name = str(op).strip()
            if op_name:
                op_owners[op_name] = service
        for group in manifest.get('groups') or []:
            group_name = str(group).strip()
            if group_name:
                group_owners.setdefault(group_name, set()).add(service)

    return {
        'urls': urls,
        'op_owners': op_owners,
        'group_owners': {k: frozenset(v) for k, v in group_owners.items()},
        'core_url': core_url,
    }


def clear_service_map_cache() -> None:
    build_service_map.cache_clear()


def resolve_op_base_url(op_name: str) -> str | None:
    data = build_service_map()
    owner = data['op_owners'].get(op_name)
    if not owner:
        # convention: ``module_name.op`` → service module_name
        if '.' in op_name:
            prefix = op_name.split('.', 1)[0]
            if prefix in data['urls']:
                return data['urls'][prefix]
        return data.get('core_url')
    return data['urls'].get(owner) or data.get('core_url')


def iter_group_base_urls(group: str) -> list[str]:
    """URL сервисов, которые могут отдавать провайдеров группы (+ все split URLs как fallback)."""
    data = build_service_map()
    owners = data['group_owners'].get(group)
    urls: list[str] = []
    seen: set[str] = set()
    if owners:
        for owner in owners:
            url = data['urls'].get(owner)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    else:
        for url in data['urls'].values():
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    core_url = data.get('core_url')
    if core_url and core_url not in seen:
        urls.append(core_url)
    return urls


def all_remote_base_urls() -> list[str]:
    data = build_service_map()
    return list(dict.fromkeys(data['urls'].values()))


def is_colocate_enabled() -> bool:
    return (
        parse_bridge_colocate(
            getattr(settings, 'BRIDGE_COLOCATE', 'auto') or '',
            transport=getattr(settings, 'BRIDGE_TRANSPORT', 'local') or '',
        )
        == 'on'
    )


def is_colocated_base_url(url: str | None) -> bool:
    """Сосед на этой машине: loopback или тот же хост, что у процесса."""
    if not url or not is_colocate_enabled():
        return False
    import os

    self_url = (getattr(settings, 'BRIDGE_CORE_URL', '') or '').strip() or None
    role = (getattr(settings, 'ERGO_PROCESS_ROLE', '') or '').strip().lower()
    data = build_service_map()
    if role.startswith('module:'):
        name = role.split(':', 1)[1].strip()
        self_url = data['urls'].get(name) or self_url
    elif role in ('api', 'core-api', ''):
        self_url = data.get('core_url') or self_url
    hosts = this_process_hosts(os.environ, self_url=self_url)
    return is_colocated_url(url, self_hosts=hosts)
