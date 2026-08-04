"""
Порядок загрузки модулей по ``module_requires`` на AppConfig.

Django вызывает ``AppConfig.ready()`` в порядке ``INSTALLED_APPS``.
Объявление ``module_requires = ('tasks', ...)`` на конфиге модуля
(имена папок ``modules/<name>``) позволяет топологически упорядочить
модульные приложения: провайдеры ModuleBridge раньше потребителей.
"""

from __future__ import annotations

import ast
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from django.core.exceptions import ImproperlyConfigured

from src.config.settings.base import MODULES_DIR

logger = logging.getLogger('utils')


def _microservice_peer_names() -> frozenset[str]:
    """Имена модулей из MICROSERVICE_MODULES (доступны в другом процессе через мост)."""
    import os

    raw = os.environ.get('MICROSERVICE_MODULES', '') or ''
    return frozenset(m.strip() for m in raw.split(',') if m.strip())


def module_name_from_app(app_path: str) -> str | None:
    """``modules.<name>.api`` → ``<name>``; иначе None."""
    if not app_path.startswith('modules.'):
        return None
    parts = app_path.split('.')
    if len(parts) < 2:
        return None
    return parts[1]


def _const_str_tuple(node: ast.AST) -> Tuple[str, ...]:
    """Читает кортеж/список строковых литералов из AST."""
    if isinstance(node, (ast.Tuple, ast.List)):
        values: List[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
            else:
                return ()
        return tuple(values)
    if isinstance(node, ast.Constant) and node.value == ():
        return ()
    return ()


def parse_module_requires_from_apps_py(apps_py: Path) -> Tuple[str, ...]:
    """
    Извлекает ``module_requires`` из класса AppConfig в apps.py без импорта.

    Берётся объединение объявлений со всех классов файла (обычно один конфиг).
    """
    try:
        source = apps_py.read_text(encoding='utf-8')
    except OSError:
        return ()

    try:
        tree = ast.parse(source, filename=str(apps_py))
    except SyntaxError:
        logger.warning('Не удалось разобрать %s для module_requires', apps_py)
        return ()

    found: List[str] = []
    seen: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            value_node = None
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == 'module_requires':
                        value_node = item.value
                        break
            elif (
                isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and item.target.id == 'module_requires'
                and item.value is not None
            ):
                value_node = item.value
            if value_node is None:
                continue
            for name in _const_str_tuple(value_node):
                if name not in seen:
                    seen.add(name)
                    found.append(name)
    return tuple(found)


def read_module_requires(module_name: str) -> Tuple[str, ...]:
    """Читает ``module_requires`` из ``modules/<name>/api/apps.py``."""
    apps_py = Path(MODULES_DIR) / module_name / 'api' / 'apps.py'
    if not apps_py.is_file():
        return ()
    return parse_module_requires_from_apps_py(apps_py)


def _topo_sort_modules(
    module_names: Sequence[str],
    requires: Dict[str, Tuple[str, ...]],
) -> List[str]:
    """
    Топологическая сортировка имён модулей (Kahn).

    При равной готовности сохраняет исходный порядок (стабильность).
    """
    name_set = set(module_names)
    indegree = {name: 0 for name in module_names}
    edges: Dict[str, List[str]] = defaultdict(list)

    remote_peers = _microservice_peer_names()
    for name in module_names:
        for dep in requires.get(name, ()):
            if dep not in name_set:
                # MODULE_RUNTIME=microservice: зависимость в другом HTTP-процессе.
                if dep in remote_peers:
                    logger.debug(
                        'module_requires: %r → %r пропущен в этом процессе '
                        '(peer из MICROSERVICE_MODULES)',
                        name,
                        dep,
                    )
                    continue
                raise ImproperlyConfigured(
                    f"Модуль {name!r} объявил module_requires={(requires.get(name),)}: "
                    f"зависимость {dep!r} не найдена среди установленных модулей "
                    f"(отключена в DISABLED_MODULES или отсутствует в modules/)."
                )
            edges[dep].append(name)
            indegree[name] += 1

    queue = deque([name for name in module_names if indegree[name] == 0])
    ordered: List[str] = []

    while queue:
        current = queue.popleft()
        ordered.append(current)
        for child in edges[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(ordered) != len(module_names):
        leftover = [name for name in module_names if name not in ordered]
        cycle_hint = ', '.join(leftover)
        raise ImproperlyConfigured(
            f"Цикл в module_requires между модулями: {cycle_hint}. "
            f"Проверьте AppConfig.module_requires в modules/*/api/apps.py."
        )
    return ordered


def sort_discovered_apps(apps: Iterable[str]) -> List[str]:
    """
    Сортирует discovered apps: ядро как было, модули — по module_requires.

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

    requires = {name: read_module_requires(name) for name in module_order}
    sorted_names = _topo_sort_modules(module_order, requires)

    result = list(core_apps)
    for name in sorted_names:
        result.extend(by_module[name])

    if requires and any(requires.values()):
        logger.debug(
            'Discovered apps: порядок модулей по module_requires: %s',
            ' → '.join(sorted_names),
        )
    return result
