"""
Порядок загрузки модулей по ``requires`` из ``modules/<name>/integrations.yaml``.

Django вызывает ``AppConfig.ready()`` в порядке ``INSTALLED_APPS``.
Объявление ``requires`` (имена папок ``modules/<name>``) позволяет
топологически упорядочить модульные приложения: провайдеры ModuleBridge
раньше потребителей.

``extends`` — опциональные расширяющие модули: отсутствие не ошибка,
в топсорт не входят (чтобы не создавать циклы с ``requires`` расширителя).
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Dict, Iterable, List, Sequence, Tuple

from django.core.exceptions import ImproperlyConfigured

from src.core.utils.auto_api.module_integrations import (
    clear_module_integrations_cache,
    read_module_integrations,
)

logger = logging.getLogger('utils')


def _remote_peer_names() -> frozenset[str]:
    """Модули в другом процессе: MICROSERVICE_MODULES и ключи BRIDGE_SERVICE_URLS."""
    import os

    names: set[str] = set()
    raw = os.environ.get('MICROSERVICE_MODULES', '') or ''
    names.update(item.strip() for item in raw.split(',') if item.strip())
    urls = os.environ.get('BRIDGE_SERVICE_URLS', '') or ''
    for part in urls.split(','):
        part = part.strip()
        if not part or '=' not in part:
            continue
        name = part.split('=', 1)[0].strip()
        if name:
            names.add(name)
    return frozenset(names)


def module_name_from_app(app_path: str) -> str | None:
    """``modules.<name>.api`` → ``<name>``; иначе None."""
    if not app_path.startswith('modules.'):
        return None
    parts = app_path.split('.')
    if len(parts) < 2:
        return None
    return parts[1]


def read_module_requires(module_name: str) -> Tuple[str, ...]:
    """Читает обязательные зависимости из ``modules/<name>/integrations.yaml``."""
    return read_module_integrations(module_name).requires


def read_module_extends(module_name: str) -> Tuple[str, ...]:
    """Читает расширяющие зависимости из ``modules/<name>/integrations.yaml``."""
    return read_module_integrations(module_name).extends


def _topo_sort_modules(
    module_names: Sequence[str],
    requires: Dict[str, Tuple[str, ...]],
) -> List[str]:
    """
    Топологическая сортировка имён модулей (Kahn).

    Нет обязательной зависимости — потребитель выкидывается из порядка
    (WARNING), остальные модули остаются. При равной готовности сохраняет
    исходный порядок (стабильность).
    """
    name_set = set(module_names)
    remote_peers = _remote_peer_names()
    excluded: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name in module_names:
            if name in excluded:
                continue
            for dep in requires.get(name, ()):
                if dep in remote_peers:
                    continue
                if dep in name_set and dep not in excluded:
                    continue
                reason = (
                    'отключён или отсутствует в modules/'
                    if dep not in name_set
                    else 'сам пропущен из-за своей зависимости'
                )
                logger.warning(
                    'Модуль %r пропущен: requires %r (%s). '
                    'Остальные модули загружаются.',
                    name,
                    dep,
                    reason,
                )
                excluded.add(name)
                changed = True
                break

    remaining = [name for name in module_names if name not in excluded]
    name_set = set(remaining)
    indegree = {name: 0 for name in remaining}
    edges = defaultdict(list)
    for name in remaining:
        for dep in requires.get(name, ()):
            if dep not in name_set:
                continue
            edges[dep].append(name)
            indegree[name] += 1

    queue = deque([name for name in remaining if indegree[name] == 0])
    ordered: List[str] = []

    while queue:
        current = queue.popleft()
        ordered.append(current)
        for child in edges[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(ordered) != len(remaining):
        leftover = [name for name in remaining if name not in ordered]
        cycle_hint = ', '.join(leftover)
        raise ImproperlyConfigured(
            f"Цикл в integrations.yaml requires между модулями: {cycle_hint}. "
            f"Проверьте modules/*/integrations.yaml."
        )
    return ordered


def _log_extends(module_order: Sequence[str], name_set: set[str]) -> None:
    for name in module_order:
        extends = read_module_extends(name)
        if not extends:
            continue
        for peer in extends:
            present = peer in name_set
            logger.debug(
                'integrations.extends: модуль %r расширяется %r (%s)',
                name,
                peer,
                'установлен' if present else 'отсутствует',
            )


def sort_discovered_apps(apps: Iterable[str]) -> List[str]:
    """
    Сортирует discovered apps: ядро как было, модули — по requires из integrations.yaml.

    Приложения одного модуля сохраняют относительный порядок discovery.
    """
    apps_list = list(apps)
    core_apps = [app for app in apps_list if not app.startswith('modules.')]
    module_apps = [app for app in apps_list if app.startswith('modules.')]

    by_module: Dict[str, List[str]] = {}
    module_order: List[str] = []
    for app in module_apps:
        name = module_name_from_app(app)
        if name is None:
            core_apps.append(app)
            continue
        if name not in by_module:
            by_module[name] = []
            module_order.append(name)
        by_module[name].append(app)

    clear_module_integrations_cache()
    requires = {name: read_module_requires(name) for name in module_order}
    sorted_names = _topo_sort_modules(module_order, requires)
    _log_extends(module_order, set(module_order))

    result = list(core_apps)
    for name in sorted_names:
        result.extend(by_module[name])

    if requires and any(requires.values()):
        logger.debug(
            'Discovered apps: порядок модулей по integrations.requires: %s',
            ' → '.join(sorted_names),
        )
    return result
