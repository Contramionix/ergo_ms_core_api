"""Очереди Celery, которые принадлежат одному каталогу modules/<name>."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger('celery.module_queues')

_QUEUE_KEY = 'queue'


def _project_root() -> Path:
    # core/api/src/core/utils/celery/module_queues.py → корень рядом с modules/
    return Path(__file__).resolve().parents[6]


def _default_module_dir(module_name: str) -> Path:
    return _project_root() / 'modules' / module_name


def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.strip()
        return value or None
    return None


def _queues_from_assign_target(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Dict):
        yield from _queues_from_dict(node)
        return
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            yield from _queues_from_assign_target(elt)


def _queues_from_dict(node: ast.Dict) -> Iterable[str]:
    for key, value in zip(node.keys, node.values):
        if _literal_str(key) == _QUEUE_KEY:
            queue = _literal_str(value)
            if queue:
                yield queue
        if isinstance(value, ast.Dict):
            yield from _queues_from_dict(value)
        elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for elt in value.elts:
                yield from _queues_from_assign_target(elt)


def queues_from_celery_configs(module_dir: Path) -> set[str]:
    """Имена очередей из ``celery_config.py`` каталога модуля, без импорта Django."""
    found: set[str] = set()
    if not module_dir.is_dir():
        return found
    for path in module_dir.rglob('celery_config.py'):
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            logger.debug('Не удалось разобрать %s: %s', path, exc)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                found.update(_queues_from_dict(node))
    return found


def queues_for_module(
    module_name: str,
    *,
    routes: dict[str, Any] | None = None,
    module_dir: str | Path | None = None,
) -> list[str]:
    """Имя модуля плюс очереди из маршрутов ``modules.<name>.*``.

    Worker ``--module=<name>`` слушает все эти очереди, а не только
    очередь с именем папки: у модуля могут быть вложенные Django-apps
    со своими ``queue`` в ``celery_config``.

    Пустой кэш routes не обнуляет набор: очереди дополнительно читаются
    из файлов ``celery_config.py`` каталога модуля.
    """
    catalog = (module_name or '').strip()
    if not catalog:
        return []

    owned: set[str] = {catalog}
    prefix = f'modules.{catalog}.'
    for pattern, dest in (routes or {}).items():
        if not isinstance(pattern, str) or not pattern.startswith(prefix):
            continue
        queue = dest.get('queue') if isinstance(dest, dict) else dest
        if isinstance(queue, str) and queue.strip():
            owned.add(queue.strip())

    directory = Path(module_dir) if module_dir is not None else _default_module_dir(catalog)
    owned.update(queues_from_celery_configs(directory))

    return sorted(owned)
