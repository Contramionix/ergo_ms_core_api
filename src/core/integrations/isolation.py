"""
Runtime-страж изоляции модулей ModuleBridge.

Регистрирует ``sys.addaudithook`` на событие ``import`` и при попытке
загрузить ``modules.<Y>.*`` из кода, физически расположенного в
``modules.<X>/...`` (X != Y), реагирует согласно настройке
``BRIDGE_ISOLATION``:

- ``'off'``   — хук не устанавливается;
- ``'warn'``  — :class:`BridgeIsolationWarning` через :mod:`warnings`
                и запись в лог;
- ``'raise'`` — :class:`BridgeIsolationError`.

Является runtime-аналогом статического AST-сканера
``validate_module_isolation``: оба инструмента дополняют друг друга.

Известные ограничения:

- Хук регистрируется в :py:meth:`AppConfig.ready`, который вызывается
  уже после первичного ``apps.populate()``. Импорты, инициированные
  самим Django при старте (``apps.py``, ``models.py``, ``urls.py`` —
  первичная загрузка), хук не увидит. Защита покрывает все последующие
  импорты в обработке HTTP-запросов, Celery-задач, тестов и lazy-импортов
  внутри кода модулей.
- Для покрытия первичной загрузки хук нужно ставить раньше — например,
  в ``manage.py`` / ``asgi.py`` / ``wsgi.py``; это отдельная задача.
- Оверхед минимален: фильтрация по префиксу ``modules.`` отсекает
  основную массу импортов до похода в стек.
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path

from src.core.utils.log_i18n import log_t

from .exceptions import BridgeError

logger = logging.getLogger('integrations.bridge')

VALID_MODES = ('off', 'warn', 'raise')

ALLOWED_MODULE_PREFIXES = (
    'src.core.',
    'core.',
    'django',
    'rest_framework',
    'drf_spectacular',
    'celery',
    'channels',
    'daphne',
)

_MODULES_PACKAGE_PREFIX = 'modules.'

_installed: bool = False
_seen_violations: set[tuple[str, int, str, str]] = set()


class BridgeIsolationWarning(UserWarning):
    """Предупреждение о прямом межмодульном импорте в обход ModuleBridge."""


class BridgeIsolationError(BridgeError):
    """
    Запрещённый прямой межмодульный импорт в режиме ``BRIDGE_ISOLATION='raise'``.

    Возникает в момент попытки импорта ``modules.<Y>.*`` из кода
    ``modules.<X>`` (X != Y). Используйте ``bridge.call('<Y>.<operation>', ...)``.
    """


def find_modules_dir(start: Path, max_depth: int = 6) -> Path | None:
    """
    Подняться от ``start`` вверх по дереву каталогов в поисках папки ``modules/``.

    Возвращает абсолютный путь к ``modules/`` или ``None``, если не найден.
    Используется и runtime-стражем, и статическим сканером
    ``validate_module_isolation`` для согласованности.
    """
    candidate = Path(start).resolve()
    for _ in range(max_depth):
        target = candidate / 'modules'
        if target.is_dir():
            return target
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def install_isolation_audit_hook(
    mode: str,
    modules_dir: Path | None,
) -> None:
    """
    Установить audit-hook изоляции модулей.

    :param mode: один из ``'off' | 'warn' | 'raise'``.
    :param modules_dir: абсолютный путь к корневой папке ``modules/``.
                        Если ``None`` или путь не существует — хук не ставится.

    Идемпотентно: повторный вызов не плодит обработчики.
    """
    global _installed

    normalized = (mode or 'warn').strip().lower()
    if normalized not in VALID_MODES:
        raise ValueError(
            f"Unknown BRIDGE_ISOLATION value: {mode!r}. "
            f"Expected one of {VALID_MODES}."
        )

    if normalized == 'off':
        logger.debug(log_t('bridge_isolation_disabled'))
        return

    if _installed:
        logger.debug(log_t('bridge_isolation_hook_already'))
        return

    if modules_dir is None:
        logger.warning(log_t('bridge_isolation_no_modules_dir'))
        return

    modules_root = Path(modules_dir).resolve()
    if not modules_root.is_dir():
        logger.warning(
            log_t('bridge_isolation_modules_dir_missing', path=modules_root),
        )
        return

    raise_on_violation = normalized == 'raise'
    modules_root_str = str(modules_root)

    def _on_audit(event: str, args: tuple) -> None:
        if event != 'import':
            return
        if not args:
            return
        target_module = args[0]
        if not isinstance(target_module, str):
            return
        if not target_module.startswith(_MODULES_PACKAGE_PREFIX):
            return

        target_owner = _extract_module_name(target_module)
        if target_owner is None:
            return

        caller = _detect_caller_module(modules_root_str)
        if caller is None:
            return

        caller_owner, caller_file, caller_line = caller
        if caller_owner == target_owner:
            return

        if _is_migration_file(caller_file):
            return

        key = (caller_file, caller_line, target_owner, target_module)
        if key in _seen_violations:
            return
        _seen_violations.add(key)

        message = _format_violation(
            caller_file=caller_file,
            caller_line=caller_line,
            caller_owner=caller_owner,
            target_module=target_module,
            target_owner=target_owner,
        )
        logger.warning("BridgeIsolation: %s", message)

        if raise_on_violation:
            raise BridgeIsolationError(message)

        warnings.warn(message, BridgeIsolationWarning, stacklevel=3)

    sys.addaudithook(_on_audit)
    _installed = True
    logger.info(
        log_t(
            'bridge_isolation_hook_installed',
            mode=normalized,
            modules_dir=modules_root,
        ),
    )


def reset_isolation_state() -> None:
    """
    Сбросить кеш увиденных нарушений (для тестов).

    Сам audit-hook снять нельзя (Python API не предоставляет такой возможности),
    но можно повторно увидеть одно и то же нарушение после reset.
    """
    _seen_violations.clear()


def _extract_module_name(dotted: str) -> str | None:
    parts = dotted.split('.', 2)
    if len(parts) < 2:
        return None
    return parts[1] or None


def _detect_caller_module(
    modules_root_str: str,
) -> tuple[str, str, int] | None:
    try:
        frame = sys._getframe(2)
    except (ValueError, AttributeError):
        return None
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename and _is_inside_modules(filename, modules_root_str):
            owner = _owner_from_path(filename, modules_root_str)
            if owner is not None:
                return owner, filename, frame.f_lineno
        frame = frame.f_back
    return None


def _is_inside_modules(filename: str, modules_root_str: str) -> bool:
    try:
        normalized = str(Path(filename).resolve())
    except (OSError, ValueError):
        normalized = filename
    return normalized.startswith(modules_root_str + os.sep)


def _owner_from_path(filename: str, modules_root_str: str) -> str | None:
    try:
        normalized = Path(filename).resolve()
        relative = normalized.relative_to(Path(modules_root_str))
    except (OSError, ValueError):
        return None
    parts = relative.parts
    return parts[0] if parts else None


def _is_migration_file(filename: str) -> bool:
    parts = Path(filename).parts
    return 'migrations' in parts


def _format_violation(
    *,
    caller_file: str,
    caller_line: int,
    caller_owner: str,
    target_module: str,
    target_owner: str,
) -> str:
    return log_t(
        'bridge_isolation_violation',
        caller_file=caller_file,
        caller_line=caller_line,
        caller_owner=caller_owner,
        target_module=target_module,
        target_owner=target_owner,
    )


__all__ = [
    'BridgeIsolationWarning',
    'BridgeIsolationError',
    'install_isolation_audit_hook',
    'reset_isolation_state',
    'find_modules_dir',
    'ALLOWED_MODULE_PREFIXES',
    'VALID_MODES',
]
