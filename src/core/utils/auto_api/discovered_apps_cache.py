"""
Кэширование результата discovery приложений.

Снижает время запуска API, Celery worker и Beat за счёт кэширования
списка приложений в файл. Discovery выполняется только при изменении
структуры core/ или modules/. Сканирование core/ и modules/ — параллельно.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from src.config.paths import CACHE_DIR
from src.config.settings.base import DJANGO_CORE_DIR, MODULES_DIR

logger = logging.getLogger('utils')


def _cache_file() -> Path:
    """Отдельный файл кэша на роль процесса (api vs module:…)."""
    try:
        from src.core.utils.module_registry import get_discovered_apps_cache_suffix

        suffix = get_discovered_apps_cache_suffix()
    except Exception:
        suffix = 'api'
    return CACHE_DIR / f'discovered_apps_{suffix}.bin'


# Обратная совместимость для импортов, ожидающих CACHE_FILE
CACHE_FILE = CACHE_DIR / 'discovered_apps.bin'

# Не обходим деревья, где apps.py / urls.py не бывают (на Windows rglob дороже кэша).
SKIP_WALK_DIR_NAMES = frozenset({
    '__pycache__',
    'migrations',
    '.git',
    'node_modules',
    'dist',
    '.venv',
    'virtual_env',
})


def _should_skip_walk_dir(name: str) -> bool:
    return name in SKIP_WALK_DIR_NAMES or name.startswith('.')


def max_mtime_named_narrow(root: Path, filename: str) -> float:
    """Max mtime файла filename под root, без тяжёлых каталогов."""
    max_mtime = 0.0
    root_s = os.fspath(root)
    if not os.path.isdir(root_s):
        return 0.0
    try:
        for dirpath, dirnames, filenames in os.walk(root_s, topdown=True, followlinks=False):
            dirnames[:] = [name for name in dirnames if not _should_skip_walk_dir(name)]
            if filename not in filenames:
                continue
            try:
                max_mtime = max(max_mtime, os.path.getmtime(os.path.join(dirpath, filename)))
            except OSError:
                pass
    except OSError:
        pass
    return max_mtime


def modules_named_mtime(modules_dir: Path, filename: str, *, under_api: bool) -> float:
    """
    mtime по modules/<name>/… без обхода client/ и submodule .git.

    under_api=True — только modules/<name>/api/**/filename
    under_api=False — modules/<name>/filename (integrations.yaml).
    """
    max_mtime = 0.0
    root_s = os.fspath(modules_dir)
    if not os.path.isdir(root_s):
        return 0.0
    try:
        with os.scandir(root_s) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if _should_skip_walk_dir(entry.name):
                    continue
                if under_api:
                    api_path = os.path.join(entry.path, 'api')
                    if os.path.isdir(api_path):
                        max_mtime = max(max_mtime, max_mtime_named_narrow(Path(api_path), filename))
                else:
                    candidate = os.path.join(entry.path, filename)
                    if os.path.isfile(candidate):
                        try:
                            max_mtime = max(max_mtime, os.path.getmtime(candidate))
                        except OSError:
                            pass
    except OSError:
        pass
    return max_mtime


def get_discovery_dirs_fingerprint() -> dict:
    """
    Fingerprint для кэша discovered_apps / discovered_urls.
    Учитывает mtime директорий, apps.py и integrations.yaml.
    Также включает DISABLED_MODULES, MICROSERVICE_MODULES и BRIDGE_SERVICE_URLS —
    при изменении списка или карты соседей кэш инвалидируется.
    """
    result = {}
    core_path = Path(DJANGO_CORE_DIR)
    modules_path = Path(MODULES_DIR)
    if core_path.exists():
        try:
            dir_mtime = core_path.stat().st_mtime
            apps_mtime = max_mtime_named_narrow(core_path, 'apps.py')
            result['core_dir'] = dir_mtime
            result['core_apps'] = max(dir_mtime, apps_mtime)
        except OSError:
            result['core_dir'] = 0
            result['core_apps'] = 0
    else:
        result['core_dir'] = 0
        result['core_apps'] = 0
    if modules_path.exists():
        try:
            dir_mtime = modules_path.stat().st_mtime
            apps_mtime = modules_named_mtime(modules_path, 'apps.py', under_api=True)
            integrations_mtime = modules_named_mtime(
                modules_path, 'integrations.yaml', under_api=False
            )
            result['modules_dir'] = dir_mtime
            result['modules_apps'] = max(dir_mtime, apps_mtime, integrations_mtime)
        except OSError:
            result['modules_dir'] = 0
            result['modules_apps'] = 0
    else:
        result['modules_dir'] = 0
        result['modules_apps'] = 0
    result['disabled_modules'] = os.getenv('DISABLED_MODULES', '')
    result['microservice_modules'] = os.getenv('MICROSERVICE_MODULES', '')
    result['bridge_service_urls'] = os.getenv('BRIDGE_SERVICE_URLS', '')
    try:
        from src.core.utils.module_registry import get_process_filter_fingerprint

        result['process_filter'] = get_process_filter_fingerprint()
    except Exception:
        result['process_filter'] = ''
    # Инвалидация кэша при смене алгоритма порядка (integrations.yaml)
    result['module_load_order'] = 3
    # Узкий обход вместо Path.rglob
    result['fingerprint_algo'] = 3
    return result


def _get_dirs_fingerprint() -> dict:
    return get_discovery_dirs_fingerprint()


def _finalize_discovered_apps(apps: List[str]) -> List[str]:
    """Топсорт модулей по requires из integrations.yaml."""
    from src.core.utils.auto_api.module_load_order import sort_discovered_apps

    return sort_discovered_apps(apps)


def _run_discovery_fast() -> List[str]:
    """Быстрый discovery без импорта. Сканирование core/ и modules/ параллельно."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_core = ex.submit(_collect_core_apps_fast)
        f_mod = ex.submit(_collect_module_apps_fast)
        core_apps = f_core.result()
        module_apps = f_mod.result()
    return _finalize_discovered_apps(core_apps + module_apps)


def _collect_core_apps_fast() -> List[str]:
    """Собирает приложения из core/."""
    result: List[str] = []
    _recursively_find_apps_fast(str(DJANGO_CORE_DIR), 'src.core', result)
    try:
        from src.core.utils.module_registry import filter_core_apps_for_process

        return filter_core_apps_for_process(result)
    except Exception:
        return result


def _collect_module_apps_fast() -> List[str]:
    """Собирает приложения из modules/."""
    result: List[str] = []
    _find_modules_apps_fast(str(MODULES_DIR), result)
    return result


def _recursively_find_apps_fast(current_dir: str, base_module: str, installed_apps: list) -> None:
    """Рекурсивный поиск приложений по структуре (без импорта)."""
    if not os.path.isdir(current_dir):
        return
    for app_name in os.listdir(current_dir):
        if _should_skip_walk_dir(app_name):
            continue
        app_path = os.path.join(current_dir, app_name)
        if os.path.isdir(app_path):
            module_path = f'{base_module}.{app_name}' if base_module else app_name
            if os.path.exists(os.path.join(app_path, 'apps.py')):
                installed_apps.append(module_path)
            _recursively_find_apps_fast(app_path, module_path, installed_apps)


def _find_modules_apps_fast(modules_dir: str, installed_apps: list) -> None:
    """Поиск приложений модулей по структуре (без импорта)."""
    if not os.path.isdir(modules_dir):
        return
    from src.core.utils.module_registry import (
        is_module_loadable_in_process,
        is_valid_module_dir_name,
    )
    for module_name in os.listdir(modules_dir):
        if _should_skip_walk_dir(module_name):
            continue
        if not is_valid_module_dir_name(module_name):
            continue
        if not is_module_loadable_in_process(module_name):
            continue
        module_path = os.path.join(modules_dir, module_name)
        if os.path.isdir(module_path):
            api_path = os.path.join(module_path, 'api')
            if os.path.isdir(api_path):
                base_module = f'modules.{module_name}.api'
                _find_apps_in_api_fast(api_path, base_module, installed_apps)


def _find_apps_in_api_fast(current_dir: str, current_module: str, installed_apps: list) -> None:
    """Рекурсивный поиск в api/ (без импорта)."""
    if os.path.exists(os.path.join(current_dir, 'apps.py')):
        installed_apps.append(current_module)
    if not os.path.isdir(current_dir):
        return
    for app_name in os.listdir(current_dir):
        app_path = os.path.join(current_dir, app_name)
        if os.path.isdir(app_path) and app_name != '__pycache__' and not app_name.startswith('.'):
            nested_module = f'{current_module}.{app_name}'
            _find_apps_in_api_fast(app_path, nested_module, installed_apps)


def _run_discovery() -> List[str]:
    """Выполняет полное discovery с проверкой AppConfig (медленно)."""
    from src.core.utils.auto_api.auto_config import ModuleDiscoverer

    discoverer = ModuleDiscoverer()
    core_apps: List[str] = []
    discoverer._recursively_find_apps(str(DJANGO_CORE_DIR), 'src.core', core_apps)
    try:
        from src.core.utils.module_registry import filter_core_apps_for_process

        core_apps = filter_core_apps_for_process(core_apps)
    except Exception:
        pass
    module_apps: List[str] = []
    discoverer._find_modules_apps(str(MODULES_DIR), module_apps)
    return _finalize_discovered_apps(core_apps + module_apps)


_in_memory_cache: Optional[tuple] = None


def clear_discovered_apps_memory_cache() -> None:
    """Сбрасывает in-process кэш (без удаления файла)."""
    global _in_memory_cache
    _in_memory_cache = None


def invalidate_discovered_apps_cache() -> None:
    """Сбрасывает файловый и in-process кэш discovered_apps."""
    clear_discovered_apps_memory_cache()
    for path in (_cache_file(), CACHE_FILE):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning('Не удалось удалить %s', path, exc_info=True)
    try:
        from src.core.utils.auto_api.discovered_urls_cache import invalidate_discovered_urls_cache

        invalidate_discovered_urls_cache()
    except Exception:
        logger.debug('invalidate discovered_urls skipped', exc_info=True)


def get_discovered_apps(use_cache: Optional[bool] = None, fast_discovery: Optional[bool] = None) -> List[str]:
    """
    Возвращает список обнаруженных приложений.

    use_cache: читать из кэша (всегда True по умолчанию)
    fast_discovery: при промахе — быстрый поиск без импорта (API_DISCOVERED_APPS_FAST_DISCOVERY)
    В пределах одного процесса результат кэшируется в памяти (fingerprint, apps).
    """
    global _in_memory_cache
    if use_cache is None:
        use_cache = True
    if fast_discovery is None:
        fast_discovery = os.getenv('API_DISCOVERED_APPS_FAST_DISCOVERY', 'true').lower() in ('1', 'true', 'yes')

    if not use_cache:
        _in_memory_cache = None
        return _run_discovery_fast() if fast_discovery else _run_discovery()

    current_fingerprint = _get_dirs_fingerprint()
    if _in_memory_cache is not None:
        from src.core.utils.cache_fingerprint import fingerprint_equal
        if fingerprint_equal(_in_memory_cache[0], current_fingerprint):
            return _in_memory_cache[1]

    from src.core.utils.cache_fingerprint import fingerprint_equal
    from src.core.utils.cache_io import read_bin_cache

    cache_path = _cache_file()
    data = read_bin_cache(cache_path)
    if data is not None:
        cached_fingerprint = data.get('fingerprint', {})
        if fingerprint_equal(cached_fingerprint, current_fingerprint):
            apps = data.get('apps')
            if apps is not None:
                _in_memory_cache = (current_fingerprint, apps)
                logger.debug('Discovered apps: загружено из кэша')
                return apps

    apps = _run_discovery_fast() if fast_discovery else _run_discovery()
    from src.core.utils.cache_io import write_bin_cache

    if write_bin_cache(cache_path, {'fingerprint': current_fingerprint, 'apps': apps}):
        logger.info('Discovered apps: сохранено в кэш (%d приложений)', len(apps))

    _in_memory_cache = (current_fingerprint, apps)
    return apps
