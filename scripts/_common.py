"""
Общие константы и утилиты для скриптов запуска Celery (worker, beat, warmup).

Все скрипты в scripts/ импортируют пути и функции отсюда,
чтобы избежать дублирования логики чтения кэша и fingerprint.
"""

import logging
import logging.config
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
API_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = API_DIR.parent.parent
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from console_tags import format_console  # noqa: E402

_BOOTSTRAP_LOGGER: Optional[logging.Logger] = None
_LOGGING_CONFIGURED = False


def setup_celery_script_logging(service: Optional[str] = None) -> logging.Logger:
    """dictConfig до Django — единый формат bootstrap-сообщений worker/beat."""
    global _BOOTSTRAP_LOGGER, _LOGGING_CONFIGURED
    if not _LOGGING_CONFIGURED:
        from src.config.log_paths import resolve_logging_service
        from src.config.logging_config import build_logging_config

        resolved = service or resolve_logging_service(sys.argv)
        logging.config.dictConfig(build_logging_config(resolved))
        _LOGGING_CONFIGURED = True
    if _BOOTSTRAP_LOGGER is None:
        _BOOTSTRAP_LOGGER = logging.getLogger('celery.bootstrap')
    return _BOOTSTRAP_LOGGER


def get_bootstrap_logger() -> logging.Logger:
    if _BOOTSTRAP_LOGGER is None:
        return setup_celery_script_logging()
    return _BOOTSTRAP_LOGGER


def _bootstrap_project_env() -> None:
    """Записывает пустые секреты текущих режимов в .env / env/*.env до django.setup."""
    from security.ensure_secret import ensure_mode_secrets_for_process

    ensure_mode_secrets_for_process(PROJECT_ROOT)


_bootstrap_project_env()

MODULES_DIR = PROJECT_ROOT / 'modules'
WORKERS_CONFIG = PROJECT_ROOT / 'celery_workers.yaml'

from src.config.paths import CACHE_DIR  # noqa: E402

CACHE_FILE = CACHE_DIR / 'celery_queues.bin'
ROUTES_QUEUES_CACHE_FILE = CACHE_DIR / 'celery_routes_queues.bin'
WARMUP_LOCK = CACHE_DIR / 'warmup.lock'
LOCK_MAX_AGE = 120


def _pid_exists(pid: int) -> bool:
    """Кроссплатформенная проверка существования процесса."""
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False


def is_celery_process(cmdline: list) -> bool:
    """Проверяет, что cmdline — реальный процесс Celery, а не grep/cat и т.п."""
    if len(cmdline) < 2:
        return False
    from src.core.utils.os_abstraction import get_os_abstraction
    first = str(cmdline[0]).lower()
    names = get_os_abstraction().process_executable_names('celery')
    return 'python' in first or any(first.endswith(n) for n in names)


def get_modules_config_mtime() -> float:
    """Max mtime по celery_config.py / celery_beat_config.py модулей."""
    from src.core.utils.cache_fingerprint import get_modules_config_max_mtime
    return get_modules_config_max_mtime(MODULES_DIR)


def get_fingerprint() -> Dict[str, float]:
    """Fingerprint для routes/queues кэша (celery_routes_queues.bin)."""
    from src.core.utils.cache_fingerprint import get_celery_config_fingerprint
    return get_celery_config_fingerprint(PROJECT_ROOT, MODULES_DIR)


def _load_bin_cache(path: Path) -> Optional[Dict]:
    """Загружает кэш (JSON+HMAC через cache_io). Возвращает None при ошибке."""
    from src.core.utils.cache_io import read_bin_cache
    data = read_bin_cache(path)
    return data if isinstance(data, dict) else None


def read_queues_cache() -> List[str]:
    """Читает список очередей из celery_queues.bin или celery_routes_queues.bin."""
    from src.core.utils.cache_fingerprint import mtime_valid, fingerprint_equal

    data = _load_bin_cache(CACHE_FILE)
    if data is not None:
        stored_mtime = data.get('modules_mtime', 0)
        current_mtime = get_modules_config_mtime()
        if mtime_valid(stored_mtime, current_mtime):
            queues = data.get('queues', [])
            if queues:
                return queues
    data = _load_bin_cache(ROUTES_QUEUES_CACHE_FILE)
    if data is not None:
        if fingerprint_equal(data.get('fingerprint', {}), get_fingerprint()):
            queues = data.get('queues', {})
            if queues:
                return sorted(queues.keys())
    return []


def cache_valid() -> bool:
    """Проверяет валидность кэша без загрузки Django."""
    from src.core.utils.cache_fingerprint import mtime_valid, fingerprint_equal

    data = _load_bin_cache(CACHE_FILE)
    if data is not None:
        stored_mtime = data.get('modules_mtime', 0)
        current_mtime = get_modules_config_mtime()
        if mtime_valid(stored_mtime, current_mtime):
            return True
    data = _load_bin_cache(ROUTES_QUEUES_CACHE_FILE)
    if data is not None:
        if fingerprint_equal(data.get('fingerprint', {}), get_fingerprint()):
            return True
    return False


def _acquire_warmup_lock() -> bool:
    """Атомарно создаёт lock-файл. Возвращает True если удалось захватить."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(WARMUP_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_warmup_lock():
    try:
        WARMUP_LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def _is_lock_stale() -> bool:
    """Lock устаревший, если процесс-владелец завершился или файлу больше LOCK_MAX_AGE."""
    try:
        with open(str(WARMUP_LOCK), 'r') as f:
            pid_str = f.read().strip()
        if pid_str.isdigit() and not _pid_exists(int(pid_str)):
            return True
    except OSError:
        pass
    try:
        age = time.time() - WARMUP_LOCK.stat().st_mtime
        return age > LOCK_MAX_AGE
    except OSError:
        return True


def _wait_for_warmup() -> bool:
    """Ждёт завершения warmup другим процессом. Возвращает True если кэш стал валидным (даже при 0 очередях)."""
    while True:
        if not WARMUP_LOCK.exists():
            return cache_valid()
        if _is_lock_stale():
            _release_warmup_lock()
            return False
        time.sleep(0.5)


def ensure_caches(*, verbose: Optional[bool] = None) -> List[str]:
    """
    Если кэш пуст — вызывает warmup_celery (без django.setup()), затем перечитывает.
    Использует file lock чтобы при параллельном старте N воркеров только один
    процесс прогревал кэши, остальные ждали результата.
    """
    from src.core.utils.celery.startup_format import celery_startup_verbose, format_name_list

    log = get_bootstrap_logger()
    show_full = verbose if verbose is not None else celery_startup_verbose()
    queues = read_queues_cache()
    # Если кэш уже валиден (даже с 0 очередями) — не пытаемся заново греть Celery.
    if queues or cache_valid():
        if queues:
            detail = format_name_list(queues, verbose=show_full)
            log.info(
                'Кэш очередей Celery валиден: %s очередей (%s), warmup_celery не требуется',
                len(queues),
                detail,
            )
        else:
            log.info(
                'Кэш очередей Celery валиден, очередей 0 (нет Celery-модулей), '
                'warmup_celery не требуется'
            )
        return queues

    if _acquire_warmup_lock():
        try:
            log.info('Кэш очередей Celery пуст или невалиден. Заполняем через warmup_celery...')
            result = subprocess.run(
                [sys.executable, '-m', 'commands', 'warmup_celery'],
                cwd=str(API_DIR),
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'},
            )
            if result.returncode == 0:
                queues = read_queues_cache()
                if cache_valid():
                    log.info('Кэш заполнен после warmup_celery: %s очередей', len(queues))
                    return queues
            log.warning('Не удалось заполнить кэш, запуск без -Q (все очереди)')
        except Exception as e:
            log.warning('Не удалось заполнить кэш (%s), запуск без -Q (все очереди)', e)
        finally:
            _release_warmup_lock()
        return []

    log.info('Другой процесс заполняет кэш, ожидание...')
    if _wait_for_warmup():
        queues = read_queues_cache()
        log.info('Кэш готов: %s очередей', len(queues))
        return queues
    log.warning('Истекло время ожидания кэша, запуск без -Q (все очереди)')
    return []


def exec_celery(cmd: List[str], cwd: str) -> int:
    """Заменяет процесс на celery — systemd/NSSM держат уже worker/beat."""
    from _replace_process import replace_current_process

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    project_root = str(PROJECT_ROOT)
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = project_root + (os.pathsep + existing if existing else '')
    return replace_current_process(cmd, cwd=cwd, env=env)


def run_celery_with_timing(
    cmd: List[str],
    cwd: str,
    ready_pattern: str,
    service_name: str,
    start: Optional[float] = None,
) -> int:
    """Запускает celery, пробрасывает stdout и печатает одну строку полного времени запуска."""
    from src.core.utils.startup_timing import try_print_service_ready

    if start is None:
        start = time.time()
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    project_root = str(PROJECT_ROOT)
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = project_root + (os.pathsep + existing if existing else '')
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
    )
    try:
        if proc.stdout:
            for line in proc.stdout:
                # Сырой вывод Celery (уже в своём формате) — без повторного formatter.
                print(line, end='')
                if ready_pattern in line:
                    try_print_service_ready(
                        service_name,
                        elapsed=max(0.0, time.time() - start),
                    )
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    return proc.returncode or 0
