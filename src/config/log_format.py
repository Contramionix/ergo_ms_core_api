"""
Единый формат строки логов ERGO MS (API, Media API, Celery, daphne).

Эталон: [LEVEL] YYYY-MM-DD HH:MM:SS logger.name module message
"""

from __future__ import annotations

from typing import Any

# dictConfig / logging.Formatter(style='{')
VERBOSE_FORMAT = '[{levelname}] {asctime} {name} {module} {message}'
VERBOSE_STYLE = '{'
DATEFMT = '%Y-%m-%d %H:%M:%S'

# logging.Formatter / daphne --log-fmt / Celery worker_*_log_format (style='%')
VERBOSE_FORMAT_PERCENT = '[%(levelname)s] %(asctime)s %(name)s %(module)s %(message)s'
DAPHNE_LOG_FMT = VERBOSE_FORMAT_PERCENT

# Тот же префикс, что verbose; processName / task_id — доп. поля worker.
CELERY_WORKER_LOG_FORMAT = (
    '[%(levelname)s] %(asctime)s %(name)s %(module)s %(processName)s %(message)s'
)
CELERY_WORKER_TASK_LOG_FORMAT = (
    '[%(levelname)s] %(asctime)s %(name)s %(module)s %(processName)s '
    '[%(task_name)s(%(task_id)s)] %(message)s'
)


def verbose_formatter_dict() -> dict[str, Any]:
    """Блок formatters для dictConfig: verbose и simple (алиас на тот же формат)."""
    verbose = {
        'format': VERBOSE_FORMAT,
        'style': VERBOSE_STYLE,
        'datefmt': DATEFMT,
    }
    return {
        'verbose': verbose,
        'simple': dict(verbose),
    }
