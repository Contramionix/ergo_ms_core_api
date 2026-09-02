"""
Скрипт запуска Celery beat без загрузки Django.

Запускает celery -A src beat. Django грузится только в процессе Celery.
С --module=<name> планирует только этот модуль (свой файл расписания).
"""

import argparse
import os
import sys
from typing import List

import psutil

from _common import (
    API_DIR,
    CACHE_DIR,
    ensure_caches,
    exec_celery,
    is_celery_process,
    setup_celery_script_logging,
)


def beat_schedule_filename(module: str = '') -> str:
    """Имя файла расписания: общий Beat и Beat модуля не делят один файл."""
    catalog = (module or '').strip()
    if catalog:
        return f'celerybeat-schedule-{catalog}'
    return 'celerybeat-schedule'


def find_celery_beat(module: str = '') -> bool:
    """Проверяет, запущен ли Beat ядра или Beat указанного модуля."""
    catalog = (module or '').strip()
    marker = f'celerybeat-schedule-{catalog}' if catalog else ''
    for proc in psutil.process_iter(['cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if not is_celery_process(cmdline):
                continue
            cmdline_lower = [str(p).lower() for p in cmdline]
            if 'beat' not in cmdline_lower:
                continue
            text = ' '.join(str(p) for p in cmdline)
            if catalog:
                if marker in text or f'--module={catalog}' in text:
                    return True
                continue
            if 'celerybeat-schedule-' in text:
                continue
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description='Запуск Celery beat')
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Полные списки очередей и модулей при старте (или ERGO_CELERY_STARTUP_VERBOSE=true)',
    )
    parser.add_argument('--loglevel', default='info')
    parser.add_argument(
        '--module',
        default='',
        help='PROCESS_MODULES и отдельный файл расписания одного модуля',
    )
    opts, _unknown = parser.parse_known_args()

    module_name = (opts.module or '').strip()

    if opts.verbose:
        os.environ['ERGO_CELERY_STARTUP_VERBOSE'] = 'true'

    log = setup_celery_script_logging('celery_beat')
    if find_celery_beat(module_name):
        if module_name:
            log.info('Celery beat модуля %s уже запущен', module_name)
        else:
            log.info('Celery beat уже запущен')
        return 0

    log.info(
        'Подготовка Celery beat: подготавливаем кэш очередей и расписаний '
        '(warmup_celery при необходимости)...'
    )
    ensure_caches(verbose=opts.verbose)
    if module_name:
        os.environ['ERGO_PROCESS_ROLE'] = f'module:{module_name}'
        os.environ['PROCESS_MODULES'] = module_name
    else:
        os.environ.setdefault('ERGO_PROCESS_ROLE', 'beat')

    loglevel = opts.loglevel
    for arg in sys.argv[1:]:
        if arg.startswith('--loglevel='):
            loglevel = arg.split('=', 1)[1]
            break

    schedule_path = CACHE_DIR / beat_schedule_filename(module_name)
    cmd: List[str] = [
        sys.executable, '-m', 'celery', '-A', 'src', 'beat',
        f'--loglevel={loglevel}',
        f'--schedule={schedule_path}',
    ]
    if module_name:
        log.info(
            'Запуск Celery beat модуля %s (уровень логов=%s, schedule=%s)...',
            module_name,
            loglevel,
            schedule_path,
        )
    else:
        log.info('Запуск Celery beat (уровень логов=%s)...', loglevel)
    return exec_celery(cmd, str(API_DIR))


if __name__ == '__main__':
    sys.exit(main())
