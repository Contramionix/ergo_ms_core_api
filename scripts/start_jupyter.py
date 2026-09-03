"""
Скрипт регистрации Django IPython kernel и запуска JupyterLab.

Устанавливает кастомный kernel 'ergo_django' с автоинициализацией Django ORM,
затем запускает JupyterLab с effective-настройками из jupyter_runtime.py.

Запуск: ergoms start-jupyter
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from _common import API_DIR, PROJECT_ROOT, format_console

_DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
for _path in (_DEPLOYMENT_DIR, _SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from project_layout import ensure_dir, jupyter_dir  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
KERNEL_NAME = 'ergo_django'
KERNEL_DISPLAY_NAME = 'Django Kernel (ERGO MS)'
KERNEL_LAUNCHER = SCRIPT_DIR / 'jupyter_django_kernel.py'
NOTEBOOKS_DIR = PROJECT_ROOT / 'notebooks'


def _jupyter_data_dir() -> Path:
    explicit = (os.environ.get('JUPYTER_DATA_DIR') or '').strip()
    if explicit:
        return Path(explicit)
    return jupyter_dir(PROJECT_ROOT)


def _notebooks_dir() -> Path:
    explicit = (os.environ.get('JUPYTER_NOTEBOOKS_DIR') or '').strip()
    if explicit:
        return Path(explicit)
    return NOTEBOOKS_DIR


def _install_django_kernel():
    """Создаёт Django-aware IPython kernel spec в JUPYTER_DATA_DIR/kernels."""
    python_exe = sys.executable.replace('\\', '/')
    launcher_path = str(KERNEL_LAUNCHER).replace('\\', '/')

    kernel_spec = {
        'argv': [python_exe, launcher_path, '-f', '{connection_file}'],
        'display_name': KERNEL_DISPLAY_NAME,
        'language': 'python',
        'env': {
            'ERGO_PROJECT_ROOT': str(PROJECT_ROOT).replace('\\', '/'),
        },
    }

    dest = ensure_dir(_jupyter_data_dir() / 'kernels' / KERNEL_NAME)
    kernel_json_path = dest / 'kernel.json'
    kernel_json_path.write_text(
        json.dumps(kernel_spec, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    print(format_console('ok', f'Ядро Django установлено: {dest}'))


def _ensure_venv_commonjs():
    """
    Создаёт package.json с type=commonjs в virtual_env/, чтобы Node.js
    не наследовал "type": "module" из package.json корня или virtual_env/npm.
    Без этого JupyterLab's node-version-check.js падает с ReferenceError
    т.к. require() недоступен в ESM-контексте.

    Если задан JUPYTER_DATA_DIR (изолированный прогон), проектный virtual_env не трогаем.
    """
    if (os.environ.get('JUPYTER_DATA_DIR') or '').strip():
        return
    venv_pkg = PROJECT_ROOT / 'virtual_env' / 'package.json'
    if not venv_pkg.exists():
        venv_pkg.parent.mkdir(parents=True, exist_ok=True)
        venv_pkg.write_text('{"type": "commonjs"}\n', encoding='utf-8')


def _print_startup_info():
    from src.config.ergo_runtime import current_ergo_jupyter, jupyter_mode_enabled
    from src.config.jupyter_runtime import (
        effective_jupyter_access_mode,
        effective_jupyter_bind_host,
        effective_jupyter_bind_port,
        effective_jupyter_security,
        jupyter_public_lab_url,
    )

    if not jupyter_mode_enabled():
        print(format_console(
            'warning',
            f'ERGO_JUPYTER={current_ergo_jupyter()} — Jupyter выключен в режимах; '
            'запуск по явной команде. Детали: env/jupyter.env',
        ))

    mode = effective_jupyter_access_mode()
    security = effective_jupyter_security()
    bind_host = effective_jupyter_bind_host()
    bind_port = effective_jupyter_bind_port()
    lab_url = jupyter_public_lab_url()

    print(format_console('info', f'Режим доступа Jupyter: {mode}'))
    print(format_console('info', f'Bind: {bind_host}:{bind_port}'))
    print(format_console('info', f'Публичный адрес: {lab_url}'))

    if security['require_token']:
        print(format_console(
            'info',
            'Токен задан в API_JUPYTER_TOKEN — откройте lab с параметром ?token=...',
        ))
    if security['require_admin_gate']:
        print(format_console(
            'info',
            'За nginx доступ только для глобального администратора (сессия SPA + токен Jupyter)',
        ))


def _start_jupyterlab():
    """Запускает JupyterLab с effective-настройками."""
    from src.config.jupyter_runtime import build_jupyter_server_argv, validate_jupyter_startup

    try:
        import jupyterlab  # noqa: F401
    except ImportError:
        print(
            format_console(
                'error',
                'JupyterLab не установлен. Выполните: ergoms python-install --with jupyter',
            ),
            file=sys.stderr,
        )
        return 1

    startup_error = validate_jupyter_startup()
    if startup_error:
        print(format_console('error', startup_error), file=sys.stderr)
        return 1

    notebooks = _notebooks_dir()
    notebooks.mkdir(parents=True, exist_ok=True)
    _ensure_venv_commonjs()

    data_dir = ensure_dir(_jupyter_data_dir())
    cmd = [sys.executable, '-m', 'jupyterlab', *build_jupyter_server_argv(str(notebooks))]

    print(format_console('info', 'Запуск JupyterLab...'))
    print(format_console('info', f'Каталог блокнотов: {notebooks}'))
    _print_startup_info()

    env = os.environ.copy()
    existing_pythonpath = env.get('PYTHONPATH', '')
    parts = [item for item in existing_pythonpath.split(os.pathsep) if item]
    root_s = str(PROJECT_ROOT)
    if root_s not in parts:
        parts.append(root_s)
    env['PYTHONPATH'] = os.pathsep.join(parts)
    env['JUPYTER_DATA_DIR'] = str(data_dir)
    env['JUPYTER_PATH'] = str(data_dir)

    from log_env import log_file_path

    log_path = log_file_path('JUPYTER', PROJECT_ROOT)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, 'a', encoding='utf-8', buffering=1)
    log_handle.write(
        f'\n--- jupyter start {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n'
    )
    log_handle.flush()
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
        if proc.stdout:
            for line in proc.stdout:
                log_handle.write(line)
                log_handle.flush()
                print(line, end='')
        proc.wait()
        return proc.returncode or 0
    except KeyboardInterrupt:
        if proc is not None:
            proc.terminate()
            proc.wait()
        return 0
    finally:
        try:
            log_handle.close()
        except OSError:
            pass


def main():
    from lifecycle.host_process_guard import refuse_unwanted_core_service
    from lifecycle.host_profile import SERVICE_API

    refused = refuse_unwanted_core_service(
        SERVICE_API,
        message_key='host_refuses_jupyter',
        project_root=PROJECT_ROOT,
    )
    if refused:
        return refused
    _install_django_kernel()
    return _start_jupyterlab()


if __name__ == '__main__':
    sys.exit(main())
