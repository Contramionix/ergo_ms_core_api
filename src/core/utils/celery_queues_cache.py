"""
Кэш списка Celery-очередей (celery_queues.bin).

Записывается при warmup_caches, читается скриптами запуска (start_celery_worker.py)
без загрузки Django для минимального времени старта.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.config.paths import CACHE_DIR, MODULES_DIR

logger = logging.getLogger('celery.cache')

CACHE_FILE = CACHE_DIR / 'celery_queues.bin'


def _get_modules_config_mtime() -> float:
    """Max mtime по celery_config.py / celery_beat_config.py модулей."""
    from src.core.utils.cache_fingerprint import get_modules_config_max_mtime
    return get_modules_config_max_mtime(Path(MODULES_DIR))


def write_queues_cache(queues: Dict[str, Any]) -> None:
    """Записывает список очередей и mtime для валидации скриптами запуска."""
    from src.core.utils.celery_config_cache import process_scoped_celery_cache

    if process_scoped_celery_cache():
        logger.debug('celery_queues.bin: пропуск записи из процесса модуля')
        return
    from src.core.utils.cache_io import write_bin_cache

    queue_names = sorted(queues.keys()) if queues else []
    data = {
        'queues': queue_names,
        'modules_mtime': _get_modules_config_mtime(),
    }
    if write_bin_cache(CACHE_FILE, data):
        logger.debug('celery_queues.bin: записано %d очередей', len(queue_names))


def read_queues_cache() -> List[str]:
    """Читает список очередей из кэша (для использования в Django-контексте)."""
    from src.core.utils.cache_fingerprint import mtime_valid
    from src.core.utils.cache_io import read_bin_cache

    data = read_bin_cache(CACHE_FILE)
    if data is None:
        return []
    stored_mtime = data.get('modules_mtime', 0)
    current_mtime = _get_modules_config_mtime()
    if mtime_valid(stored_mtime, current_mtime):
        return data.get('queues', [])
    return []
