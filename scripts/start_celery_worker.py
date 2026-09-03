"""
Скрипт запуска Celery worker.

Читает celery_workers.yaml и кэш очередей, при пустом кэше вызывает warmup_celery,
затем запускает celery -A src worker. Django грузится только в Celery-процессе.
"""

import argparse
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import psutil
import yaml

from _common import (
    API_DIR,
    PROJECT_ROOT,
    WORKERS_CONFIG,
    ensure_caches,
    exec_celery,
    is_celery_process,
    read_queues_cache,
    setup_celery_script_logging,
)
from src.core.utils.celery.startup_format import format_queues_display

try:
    from celery_balance.overlay import worker_override
    from celery_balance.settings import load_settings
except ImportError:  # pragma: no cover — скрипт без пакета deployment
    worker_override = None  # type: ignore[assignment]
    load_settings = None  # type: ignore[assignment]


def load_workers_config() -> Dict[str, Any]:
    """Загружает celery_workers.yaml без Django."""
    if not WORKERS_CONFIG.exists():
        return {}
    try:
        with open(WORKERS_CONFIG, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def find_celery_worker(queues: Optional[str] = None, hostname: Optional[str] = None) -> bool:
    """Проверяет, запущен ли worker с указанными параметрами."""
    for proc in psutil.process_iter(['cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if not is_celery_process(cmdline):
                continue
            cmdline_lower = [str(p).lower() for p in cmdline]
            if 'worker' not in cmdline_lower:
                continue
            cmdline_str = ' '.join(cmdline)
            if hostname and (f'--hostname={hostname}' in cmdline_str or f'-n {hostname}' in cmdline_str):
                return True
            if queues:
                for i, arg in enumerate(cmdline):
                    if arg == '-Q' and i + 1 < len(cmdline):
                        worker_queues = set(cmdline[i + 1].lower().split(','))
                        requested = set(q.strip().lower() for q in queues.split(','))
                        if requested.issubset(worker_queues):
                            return True
                        break
            elif not hostname:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def resolve_queues(queues_config: Any, all_queues: List[str]) -> Optional[List[str]]:
    """
    Преобразует конфиг очередей в список.
    Возвращает [] для режима 'all' при пустом all_queues (celery без -Q).
    """
    if queues_config == 'all' or queues_config is None:
        if not all_queues:
            return []
        names = {'default'}
        names.update(all_queues)
        return sorted(names)
    if isinstance(queues_config, list):
        return queues_config
    if isinstance(queues_config, str):
        return [q.strip() for q in queues_config.split(',')]
    return []


def build_cmd(
    queues: Optional[List[str]],
    hostname: str,
    loglevel: str = 'info',
    concurrency: Optional[int] = None,
    pool: str = 'threads',
    prefetch_multiplier: Optional[int] = None,
    autoscale_min: Optional[int] = None,
    autoscale_max: Optional[int] = None,
) -> List[str]:
    """Формирует команду celery worker. queues=None или [] — без -Q (все очереди)."""
    effective_pool = (pool or 'threads').strip().lower()
    cmd = [
        sys.executable, '-m', 'celery', '-A', 'src', 'worker',
        f'--hostname={hostname}', f'--loglevel={loglevel}',
        f'--pool={effective_pool}', '-E',
    ]
    if queues:
        cmd.extend(['-Q', ','.join(queues)])
    if (
        effective_pool == 'prefork'
        and autoscale_min
        and autoscale_max
        and autoscale_max >= autoscale_min
    ):
        cmd.append(f'--autoscale={autoscale_max},{autoscale_min}')
    elif concurrency:
        cmd.append(f'--concurrency={concurrency}')
    if prefetch_multiplier:
        cmd.append(f'--prefetch-multiplier={prefetch_multiplier}')
    return cmd


def _balance_overrides(worker_name: Optional[str]) -> Dict[str, Any]:
    """Overlay auto-режима: concurrency/prefetch/autoscale. Иначе пусто."""
    if load_settings is None or worker_override is None:
        return {}
    try:
        settings = load_settings(PROJECT_ROOT)
        override = worker_override(PROJECT_ROOT, worker_name, mode=settings.mode)
    except Exception:
        return {}
    if override is None:
        return {}
    return {
        'concurrency': override.concurrency,
        'prefetch_multiplier': override.prefetch_multiplier,
        'autoscale_min': override.autoscale_min,
        'autoscale_max': override.autoscale_max,
    }


def main() -> int:
    start_time = time.time()
    parser = argparse.ArgumentParser(description='Запуск Celery worker')
    parser.add_argument('--worker', type=str, default=None)
    parser.add_argument('--list-workers', action='store_true')
    parser.add_argument('--queues', type=str, default=None)
    parser.add_argument('--hostname', type=str, default=None)
    parser.add_argument('--concurrency', type=int, default=None)
    parser.add_argument('--pool', type=str, default=None, help='пул: threads | prefork | solo')
    parser.add_argument('--loglevel', default='info')
    parser.add_argument('--module', default='', help='Очередь и PROCESS_MODULES одного модуля')
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Полные списки очередей и модулей при старте (или ERGO_CELERY_STARTUP_VERBOSE=true)',
    )
    opts = parser.parse_args()

    module_name = (opts.module or '').strip()
    user_queues = bool(opts.queues)

    if opts.verbose:
        os.environ['ERGO_CELERY_STARTUP_VERBOSE'] = 'true'

    log = setup_celery_script_logging('celery')
    config = load_workers_config()
    workers_config = config.get('workers', {})
    defaults = config.get('defaults', {})
    default_pool = defaults.get('pool', 'threads')
    log.info('Подготовка Celery worker: читаем кэш очередей (warmup_celery при необходимости)...')
    all_queues = ensure_caches(verbose=opts.verbose)
    if module_name:
        os.environ['ERGO_PROCESS_ROLE'] = f'module:{module_name}'
        os.environ['PROCESS_MODULES'] = module_name
    else:
        os.environ.setdefault('ERGO_PROCESS_ROLE', 'worker')
    if module_name and not user_queues and not opts.worker:
        from src.core.utils.celery.module_queues import queues_for_module
        from src.core.utils.celery_config_cache import read_routes_queues_cache

        cached = read_routes_queues_cache(
            validate_fingerprint=False,
            require_worker_fields=False,
        ) or {}
        module_queues = queues_for_module(
            module_name,
            routes=cached.get('routes') or {},
        )
        opts.queues = ','.join(module_queues or [module_name])

    if opts.list_workers:
        if not workers_config:
            log.info('В %s нет worker\'ов', WORKERS_CONFIG)
            return 0
        log.info('Worker\'ы:')
        for name, w in workers_config.items():
            q = w.get('queues', [])
            if q == 'all':
                qs = 'все'
            elif isinstance(q, str):
                qs = q
            elif isinstance(q, list):
                qs = ', '.join(q) if q else 'нет'
            else:
                qs = 'нет'
            pool = w.get('pool') or default_pool
            log.info(
                '  %s: %s очереди=%s пул=%s',
                name,
                w.get('description', ''),
                qs,
                pool,
            )
        return 0

    worker_name = opts.worker
    queues_opt = opts.queues
    hostname_opt = opts.hostname
    loglevel_opt = opts.loglevel or defaults.get('loglevel', 'info')
    concurrency_opt = opts.concurrency
    pool_opt = opts.pool or default_pool
    pool = pool_opt

    if worker_name:
        if worker_name not in workers_config:
            log.error(
                "Worker '%s' не найден. Доступные: %s",
                worker_name,
                ', '.join(workers_config.keys()),
            )
            return 1
        w = workers_config[worker_name]
        queues = resolve_queues(w.get('queues'), all_queues)
        hostname = w.get('hostname', f'worker@{worker_name}')
        overlay = {} if concurrency_opt else _balance_overrides(worker_name)
        concurrency = concurrency_opt or overlay.get('concurrency') or w.get('concurrency')
        loglevel = w.get('loglevel') or loglevel_opt
        pool = w.get('pool') or pool_opt
        log.info("Запуск worker'а '%s': %s", worker_name, w.get('description', ''))
        queues_display = format_queues_display(queues, all_queues, verbose=opts.verbose)
        log.info(
            'Режим: worker из конфигурации (--worker), hostname=%s, '
            'очереди=%s, параллелизм=%s, пул=%s',
            hostname,
            queues_display,
            concurrency or 'по умолчанию',
            pool,
        )
    elif queues_opt or hostname_opt:
        if queues_opt:
            queue_list = [q.strip() for q in queues_opt.split(',')]
            if all_queues and not module_name:
                invalid = set(queue_list) - set(all_queues) - {'default'}
                if invalid:
                    from src.core.utils.celery.startup_format import format_name_list

                    available = format_name_list(all_queues, verbose=opts.verbose)
                    log.error('Неизвестные очереди: %s. Доступные: %s', invalid, available)
                    return 1
            queues = queue_list
        else:
            queues = resolve_queues(None, all_queues)
        hostname = hostname_opt or (
            f'worker@{module_name}' if module_name else f"worker@{'_'.join(queues or ['all'])[:50]}"
        )
        concurrency = concurrency_opt
        loglevel = loglevel_opt
        pool = pool_opt
        log.info('Запуск Celery worker')
        queues_display = format_queues_display(queues, all_queues, verbose=opts.verbose)
        log.info(
            'Режим: worker из аргументов командной строки, hostname=%s, '
            'очереди=%s, параллелизм=%s, пул=%s',
            hostname,
            queues_display,
            concurrency or 'по умолчанию',
            pool,
        )
    elif workers_config:
        procs = []
        log.info('Запуск Celery worker\'ов из celery_workers.yaml (несколько процессов)...')
        for name, w in workers_config.items():
            queues = resolve_queues(w.get('queues'), all_queues)
            hostname = w.get('hostname', f'worker@{name}')
            if find_celery_worker(hostname=hostname):
                log.info('%s уже запущен, пропуск', hostname)
                continue
            queues_display = format_queues_display(queues, all_queues, verbose=opts.verbose)
            worker_pool = w.get('pool') or default_pool
            overlay = _balance_overrides(name)
            cmd = build_cmd(
                queues,
                hostname,
                w.get('loglevel', defaults.get('loglevel', 'info')),
                overlay.get('concurrency') or w.get('concurrency'),
                pool=worker_pool,
                prefetch_multiplier=overlay.get('prefetch_multiplier'),
                autoscale_min=overlay.get('autoscale_min'),
                autoscale_max=overlay.get('autoscale_max'),
            )
            log.info(
                "Запуск '%s' (%s) -> очереди=%s, параллелизм=%s, пул=%s",
                name,
                hostname,
                queues_display,
                overlay.get('concurrency') or w.get('concurrency') or 'по умолчанию',
                worker_pool,
            )
            env = os.environ.copy()
            env.setdefault('PYTHONPATH', '')
            env['PYTHONPATH'] = str(PROJECT_ROOT) + (os.pathsep + env['PYTHONPATH'] if env['PYTHONPATH'] else '')
            # Не перенаправляем stdout в PIPE без чтения — иначе воркер зависает
            # при заполнении буфера во время загрузки Django/Celery.
            proc = subprocess.Popen(
                cmd, cwd=str(API_DIR), start_new_session=True, env=env,
            )
            procs.append(proc)
            time.sleep(0.3)
        if not procs:
            log.info('Нет worker\'ов для запуска')
            return 0
        log.info('Для остановки нажмите Ctrl+C...')
        try:
            while any(p.poll() is None for p in procs):
                time.sleep(1)
        except KeyboardInterrupt:
            for p in procs:
                if p.poll() is None:
                    p.terminate()
            time.sleep(2)
            for p in procs:
                if p.poll() is None:
                    p.kill()
        return 0
    else:
        queues = resolve_queues(None, all_queues)
        hostname = f"worker@{'_'.join(queues or ['all'])[:50]}"
        concurrency = concurrency_opt
        loglevel = loglevel_opt
        pool = pool_opt
        log.info(
            'Файл celery_workers.yaml не найден. Запуск одного worker\'а со всеми '
            'очередями (без -Q, если кэш пуст).'
        )

    queues_display = format_queues_display(queues, all_queues, verbose=opts.verbose)
    if find_celery_worker(hostname=hostname):
        log.info('Worker %s уже запущен', hostname)
        return 0

    overlay = {} if concurrency_opt else _balance_overrides(worker_name)
    if overlay.get('concurrency'):
        concurrency = overlay['concurrency']
    log.info('Запуск %s, очереди: %s, пул=%s', hostname, queues_display, pool)
    cmd = build_cmd(
        queues,
        hostname,
        loglevel,
        concurrency,
        pool=pool,
        prefetch_multiplier=overlay.get('prefetch_multiplier'),
        autoscale_min=overlay.get('autoscale_min'),
        autoscale_max=overlay.get('autoscale_max'),
    )
    return exec_celery(cmd, str(API_DIR))


if __name__ == '__main__':
    sys.exit(main())
