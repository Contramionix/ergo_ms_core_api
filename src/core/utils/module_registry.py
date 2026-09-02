"""
Реестр модулей: список установленных модулей и флаг отключения.

Единая точка чтения DISABLED_MODULES и фильтра процесса (MODULE_RUNTIME).
Реализация каталога — lifecycle.modules.catalog.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import FrozenSet, List

# paths, не settings.base: `commands install` идёт до django-environ / poetry install.
from src.config.paths import MODULES_DIR, SYSTEM_DIR

_cached_catalog = None


def _deployment_dir() -> Path:
    return SYSTEM_DIR / 'core' / 'deployment'


def _get_catalog():
    global _cached_catalog
    if _cached_catalog is not None:
        return _cached_catalog

    deployment = _deployment_dir()
    if str(deployment) not in sys.path:
        sys.path.insert(0, str(deployment))

    from lifecycle.modules.catalog import ModuleCatalog  # noqa: WPS433

    _cached_catalog = ModuleCatalog.from_env(SYSTEM_DIR)
    return _cached_catalog


def get_disabled_modules() -> FrozenSet[str]:
    """Возвращает множество имён отключённых модулей (кэш процесса)."""
    return _get_catalog().disabled


def is_module_disabled(module_name: str) -> bool:
    """Проверяет, отключён ли модуль по имени."""
    return _get_catalog().is_disabled(module_name)


def is_module_loadable_in_process(module_name: str) -> bool:
    """Модуль загружается в текущем процессе (disabled + MODULE_RUNTIME / role)."""
    return _get_catalog().is_loadable_in_process(module_name)


def get_microservice_modules() -> FrozenSet[str]:
    """Модули, вынесенные в отдельные HTTP-процессы при MODULE_RUNTIME=microservice."""
    return _get_catalog().microservice_modules


def get_split_modules() -> FrozenSet[str]:
    """Устаревший алиас ``get_microservice_modules``."""
    return get_microservice_modules()


def get_module_runtime() -> str:
    """``monolith`` или ``microservice``."""
    return _get_catalog().module_runtime


def get_process_role() -> str:
    """``ERGO_PROCESS_ROLE`` (например ``api``, ``module:<name>``)."""
    return _get_catalog().process_role


def get_process_filter_fingerprint() -> str:
    """Отпечаток фильтра процесса для кэша discovered_apps."""
    base = _get_catalog().process_filter_fingerprint()
    return f'{base};{_process_profile_fingerprint()}'


def _process_profile_mod():
    deployment = _deployment_dir()
    if str(deployment) not in sys.path:
        sys.path.insert(0, str(deployment))
    from lifecycle.module_process_profile import (  # noqa: WPS433
        allow_core_url_route,
        filter_core_apps,
        is_slim_module_process,
        process_profile_fingerprint,
    )

    return filter_core_apps, is_slim_module_process, allow_core_url_route, process_profile_fingerprint


def _process_profile_fingerprint() -> str:
    try:
        _filter, _slim, _url, fingerprint = _process_profile_mod()
        return fingerprint(SYSTEM_DIR)
    except Exception:
        return ''


def filter_core_apps_for_process(apps: list) -> list:
    try:
        filter_core_apps, _slim, _url, _fp = _process_profile_mod()
        return filter_core_apps(apps, SYSTEM_DIR)
    except Exception:
        return list(apps)


def is_slim_module_process() -> bool:
    try:
        _filter, is_slim, _url, _fp = _process_profile_mod()
        return bool(is_slim())
    except Exception:
        return False


def allow_core_url_route(route: str) -> bool:
    try:
        _filter, _slim, allow_route, _fp = _process_profile_mod()
        return bool(allow_route(route))
    except Exception:
        return True


def get_discovered_apps_cache_suffix() -> str:
    """Суффикс имени файла кэша discovered_apps."""
    return _get_catalog().cache_key_suffix()


def is_valid_module_dir_name(name: str) -> bool:
    """Папка верхнего уровня modules/ — модуль, а не служебный каталог."""
    deployment = _deployment_dir()
    if str(deployment) not in sys.path:
        sys.path.insert(0, str(deployment))
    from lifecycle.modules.catalog import ModuleCatalog  # noqa: WPS433

    return ModuleCatalog.is_valid_module_dir_name(name)


def is_valid_module_name(module_name: str) -> bool:
    """
    Имя модуля для Celery/discovery: только a-z и `_`, без цифр и верхнего регистра.
    Без Django — используется при прогреве кэшей без django.setup().
    """
    return bool(re.match(r'^[a-z_]+$', module_name))


def get_installed_module_names(*, include_disabled: bool = False) -> List[str]:
    """
    Имена установленных модулей из ``modules/`` (есть ``api/`` и/или ``client/``).

    Пустые placeholder-каталоги (неинициализированные submodule) не включаются.
    Не импортирует приложения — только обход файловой системы.
    """
    if not MODULES_DIR.exists() or not MODULES_DIR.is_dir():
        return []
    return _get_catalog().list_module_names(include_disabled=include_disabled)


def reset_cache() -> None:
    """Сбрасывает кэш (для тестов или при hot-reload)."""
    global _cached_catalog
    _cached_catalog = None


def top_level_module_from_menu_source(module_source: str) -> str | None:
    """``modules/<name>/…`` → ``<name>``; иначе ``None``."""
    deployment = _deployment_dir()
    if str(deployment) not in sys.path:
        sys.path.insert(0, str(deployment))
    from lifecycle.modules.module_source import top_level_from_module_source  # noqa: WPS433

    return top_level_from_module_source(module_source)
