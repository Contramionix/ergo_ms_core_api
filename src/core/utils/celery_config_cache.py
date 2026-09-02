"""
Кэш конфигурации Celery: маршруты/очереди и расписание Beat.

- celery_routes_queues.bin — CeleryModuleManager (routes, queues, limits)
- celery_beat_schedule.bin — CeleryBeatModuleManager (schedule)
"""
import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from src.config.paths import CACHE_DIR, MODULES_DIR, SYSTEM_DIR

logger = logging.getLogger('celery.cache')

_SCHEDULE_TYPE = '_schedule_type'


def _to_json_safe(val: Any) -> Any:
    """Преобразует значение в JSON-совместимый тип."""
    if isinstance(val, (set, frozenset)):
        return sorted(val)
    return val


def _serialize_schedule_value(obj: Any) -> Any:
    """Сериализует объект расписания Celery в JSON-совместимый dict."""
    from celery.schedules import crontab, schedule

    if isinstance(obj, crontab):
        return {
            _SCHEDULE_TYPE: 'crontab',
            'minute': _to_json_safe(getattr(obj, '_orig_minute', '*')),
            'hour': _to_json_safe(getattr(obj, '_orig_hour', '*')),
            'day_of_week': _to_json_safe(getattr(obj, '_orig_day_of_week', '*')),
            'day_of_month': _to_json_safe(getattr(obj, '_orig_day_of_month', '*')),
            'month_of_year': _to_json_safe(getattr(obj, '_orig_month_of_year', '*')),
        }
    if isinstance(obj, schedule):
        run_every = getattr(obj, 'run_every', None)
        sec = run_every.total_seconds() if run_every else 0
        return {
            _SCHEDULE_TYPE: 'schedule',
            'run_every': sec,
            'relative': getattr(obj, 'relative', False),
        }
    if isinstance(obj, timedelta):
        return {_SCHEDULE_TYPE: 'timedelta', 'seconds': obj.total_seconds()}
    return obj


def _deserialize_schedule_value(data: Any) -> Any:
    """Восстанавливает объект расписания Celery из dict."""
    from celery.schedules import crontab, schedule

    if not isinstance(data, dict) or _SCHEDULE_TYPE not in data:
        return data
    stype = data[_SCHEDULE_TYPE]
    if stype == 'crontab':
        return crontab(
            minute=data.get('minute', '*'),
            hour=data.get('hour', '*'),
            day_of_week=data.get('day_of_week', '*'),
            day_of_month=data.get('day_of_month', '*'),
            month_of_year=data.get('month_of_year', '*'),
        )
    if stype == 'schedule':
        sec = data.get('run_every', 0)
        return schedule(run_every=timedelta(seconds=sec), relative=data.get('relative', False))
    if stype == 'timedelta':
        return timedelta(seconds=data.get('seconds', 0))
    return data


def _serialize_schedule(schedule_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Сериализует весь schedule: преобразует все schedule-значения в JSON-совместимые."""
    result = {}
    for name, entry in schedule_dict.items():
        serialized = {}
        for k, v in entry.items():
            serialized[k] = _serialize_schedule_value(v)
        result[name] = serialized
    return result


def _deserialize_schedule(data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Восстанавливает schedule: преобразует dict обратно в объекты Celery."""
    result = {}
    for name, entry in data.items():
        restored = {}
        for k, v in entry.items():
            restored[k] = _deserialize_schedule_value(v)
        result[name] = restored
    return result

CACHE_FILE = CACHE_DIR / 'celery_routes_queues.bin'
BEAT_SCHEDULE_CACHE_FILE = CACHE_DIR / 'celery_beat_schedule.bin'
# v2: queue_limits + annotations, чтобы worker не импортировал celery_config.py
WORKER_CACHE_VERSION = 2


def process_scoped_celery_cache() -> bool:
    """Процесс модуля не должен затирать общий кэш routes/schedule."""
    if os.environ.get('PROCESS_MODULES', '').strip():
        return True
    role = os.environ.get('ERGO_PROCESS_ROLE', '').strip().lower()
    return role.startswith('module:')


def _get_fingerprint() -> Dict[str, float]:
    """Fingerprint на основе mtime конфигурационных файлов модулей."""
    from src.core.utils.cache_fingerprint import get_celery_config_fingerprint
    return get_celery_config_fingerprint(
        Path(SYSTEM_DIR), Path(MODULES_DIR)
    )


def write_routes_queues_cache(
    routes: Dict[str, Any],
    queues: Dict[str, Any],
    *,
    queue_limits: Optional[Dict[str, int]] = None,
    annotations: Optional[Dict[str, Any]] = None,
    additional_configs: Optional[Dict[str, Any]] = None,
    modules: Optional[list] = None,
) -> None:
    """Сохраняет маршруты, очереди и поля worker в файловый кэш."""
    if process_scoped_celery_cache():
        logger.debug('celery_routes_queues.bin: пропуск записи из процесса модуля')
        return
    from src.core.utils.cache_io import write_bin_cache

    data = {
        'fingerprint': _get_fingerprint(),
        'cache_version': WORKER_CACHE_VERSION,
        'routes': routes,
        'queues': queues,
        'queue_limits': queue_limits or {},
        'annotations': annotations or {},
        'additional_configs': additional_configs or {},
        'modules': list(modules or []),
    }
    if write_bin_cache(CACHE_FILE, data):
        logger.debug(
            'celery_routes_queues.bin: записано %d маршрутов, %d очередей',
            len(routes), len(queues),
        )


def read_routes_queues_cache(
    *,
    validate_fingerprint: bool = True,
    require_worker_fields: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Читает routes/queues (и поля worker) из кэша.
    Возвращает dict или None если кэш отсутствует/невалиден.

    validate_fingerprint=False — только наличие данных в bin (без обхода
    celery_config.py модулей). Для HTTP-процесса API после warmup_caches.
    require_worker_fields=True — старый bin без limits/annotations не подходит
    worker/beat: нужен полный импорт celery_config.py.
    """
    from src.core.utils.cache_io import read_bin_cache

    data = read_bin_cache(CACHE_FILE)
    if data is None:
        return None
    if validate_fingerprint:
        from src.core.utils.cache_fingerprint import fingerprint_equal

        if not fingerprint_equal(data.get('fingerprint', {}), _get_fingerprint()):
            return None
    routes = data.get('routes')
    queues = data.get('queues')
    if routes is None or queues is None:
        return None
    if require_worker_fields:
        if int(data.get('cache_version') or 0) < WORKER_CACHE_VERSION:
            return None
        if 'queue_limits' not in data:
            return None
    return {
        'routes': routes,
        'queues': queues,
        'queue_limits': data.get('queue_limits') or {},
        'annotations': data.get('annotations') or {},
        'additional_configs': data.get('additional_configs') or {},
        'modules': list(data.get('modules') or []),
    }


def write_beat_schedule_cache(schedule: Dict[str, Dict[str, Any]]) -> None:
    """Сохраняет расписание Beat в файловый кэш с fingerprint."""
    if process_scoped_celery_cache():
        logger.debug('celery_beat_schedule.bin: пропуск записи из процесса модуля')
        return
    from src.core.utils.cache_io import write_bin_cache

    serialized = _serialize_schedule(schedule)
    data = {
        'fingerprint': _get_fingerprint(),
        'schedule': serialized,
    }
    if write_bin_cache(BEAT_SCHEDULE_CACHE_FILE, data):
        logger.debug('celery_beat_schedule.bin: записано %d задач', len(schedule))


def read_beat_schedule_cache() -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Читает расписание Beat из кэша.
    Возвращает schedule или None если кэш невалиден.
    """
    from src.core.utils.cache_fingerprint import fingerprint_equal
    from src.core.utils.cache_io import read_bin_cache

    data = read_bin_cache(BEAT_SCHEDULE_CACHE_FILE)
    if data is None:
        return None
    if not fingerprint_equal(data.get('fingerprint', {}), _get_fingerprint()):
        return None
    schedule = data.get('schedule')
    if schedule is not None:
        return _deserialize_schedule(schedule)
    return None
