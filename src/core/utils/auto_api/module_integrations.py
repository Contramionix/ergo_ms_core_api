"""
Загрузка modules/<name>/integrations.yaml — зависимости модулей.

requires — обязательные peer-модули (имена папок modules/<name>).
extends — опциональные расширяющие модули (отсутствие не ошибка).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Tuple

import yaml

from src.config.settings.base import MODULES_DIR

logger = logging.getLogger('utils')

INTEGRATIONS_FILENAME = 'integrations.yaml'


@dataclass(frozen=True)
class ModuleIntegrations:
    """Объявленные интеграции модуля из integrations.yaml."""

    requires: Tuple[str, ...] = ()
    extends: Tuple[str, ...] = ()


_EMPTY = ModuleIntegrations()


def _as_str_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text and text not in seen:
                seen.add(text)
                items.append(text)
        return tuple(items)
    return ()


def parse_integrations_yaml(path: Path) -> ModuleIntegrations:
    """Разбирает integrations.yaml; при ошибке чтения/парсинга — пустой результат."""
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        logger.warning('Не удалось прочитать %s: %s', path, exc)
        return _EMPTY

    if not text.strip():
        return _EMPTY

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning('Не удалось разобрать %s: %s', path, exc)
        return _EMPTY

    if data is None:
        return _EMPTY
    if not isinstance(data, dict):
        logger.warning('%s: ожидается mapping, получен %s', path, type(data).__name__)
        return _EMPTY

    return ModuleIntegrations(
        requires=_as_str_tuple(data.get('requires')),
        extends=_as_str_tuple(data.get('extends')),
    )


def integrations_yaml_path(module_name: str) -> Path:
    return Path(MODULES_DIR) / module_name / INTEGRATIONS_FILENAME


@lru_cache(maxsize=256)
def read_module_integrations(module_name: str) -> ModuleIntegrations:
    """Читает integrations.yaml модуля; файла нет — пустые requires/extends."""
    path = integrations_yaml_path(module_name)
    if not path.is_file():
        return _EMPTY
    return parse_integrations_yaml(path)


def clear_module_integrations_cache() -> None:
    """Сбрасывает кэш read_module_integrations (тесты / hot-reload)."""
    read_module_integrations.cache_clear()
