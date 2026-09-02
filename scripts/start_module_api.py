"""
Запуск HTTP API одного модуля (MODULE_RUNTIME=microservice).

Точка входа для compose-сервиса модуля и ``ergoms start-module``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from _replace_process import replace_current_process

SCRIPT_DIR = Path(__file__).resolve().parent
API_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = API_DIR.parent.parent
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'


def _ensure_sys_path() -> None:
    for path in (API_DIR, PROJECT_ROOT):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    deployment = str(DEPLOYMENT_DIR)
    if deployment not in sys.path:
        sys.path.insert(0, deployment)


def _ensure_api_secret() -> None:
    from security.ensure_secret import ensure_mode_secrets_for_process

    ensure_mode_secrets_for_process(PROJECT_ROOT)


def _build_env(module_name: str) -> dict:
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    path_entries = [str(PROJECT_ROOT), str(API_DIR)]
    existing = env.get('PYTHONPATH', '')
    merged = path_entries + ([existing] if existing else [])
    env['PYTHONPATH'] = os.pathsep.join(merged)

    env['ERGO_PROCESS_ROLE'] = f'module:{module_name}'
    env['PROCESS_MODULES'] = module_name
    env.setdefault('MODULE_RUNTIME', 'microservice')

    # Порт модуля
    key = module_name.upper().replace('-', '_')
    port = (
        env.get('MODULE_API_BIND_PORT')
        or env.get(f'{key}_PORT')
        or ''
    ).strip()
    if not port:
        port = str(8100 + (sum(ord(c) for c in module_name) % 500))
    env['API_PORT'] = port
    env.setdefault('API_HOST', '0.0.0.0')
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description='Start module API process')
    parser.add_argument('--module', required=True, help='Имя папки modules/<name>')
    args = parser.parse_args()
    module_name = args.module.strip()
    if not module_name:
        print('module name is required', file=sys.stderr)
        return 2

    _ensure_sys_path()
    _ensure_api_secret()

    from src.config.deploy import (
        build_dev_command,
        build_daphne_command,
        get_api_bind_host,
        get_settings_module,
        is_production,
    )
    from src.core.utils.startup_timing import ENV_API_START_WALL, mark_start

    mark_start(env_key=ENV_API_START_WALL)

    run_env = _build_env(module_name)
    run_env.setdefault('DJANGO_SETTINGS_MODULE', get_settings_module())

    # get_api_bind_* читают os.environ — синхронизируем
    os.environ.update({
        'API_PORT': run_env['API_PORT'],
        'API_HOST': run_env.get('API_HOST', '0.0.0.0'),
        'ERGO_PROCESS_ROLE': run_env['ERGO_PROCESS_ROLE'],
        'PROCESS_MODULES': run_env['PROCESS_MODULES'],
    })

    host = get_api_bind_host()
    port = run_env['API_PORT']

    if is_production():
        cmd = build_daphne_command(sys.executable)
        print(f'Module API {module_name} (production): daphne на {host}:{port}')
    else:
        cmd = build_dev_command(sys.executable)
        print(f'Module API {module_name} (разработка): runserver на {host}:{port}')

    return replace_current_process(cmd, cwd=str(API_DIR), env=run_env)


if __name__ == '__main__':
    raise SystemExit(main())
