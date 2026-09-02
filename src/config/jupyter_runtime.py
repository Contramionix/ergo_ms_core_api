"""
Сценарии доступа к JupyterLab: local / lan / nginx.

Effective bind/public URL и аргументы ServerApp — единый источник для start_jupyter.py
и settings/jupyter.py. Пустой API_JUPYTER_TOKEN для lan/nginx заполняет ensure_secret.
"""

from __future__ import annotations

from typing import Any

from src.config.env import env
from src.config.nginx_runtime import (
    detect_lan_ip,
    nginx_enabled,
    nginx_public_base_url,
    nginx_public_origin,
)

ACCESS_LOCAL = 'local'
ACCESS_LAN = 'lan'
ACCESS_NGINX = 'nginx'
ACCESS_AUTO = 'auto'

_VALID_ACCESS_MODES = frozenset({ACCESS_LOCAL, ACCESS_LAN, ACCESS_NGINX, ACCESS_AUTO})


def jupyter_behind_nginx() -> bool:
    from src.config.ergo_runtime import jupyter_behind_nginx_mode

    return jupyter_behind_nginx_mode()


def jupyter_allow_remote() -> bool:
    return env.bool('API_JUPYTER_ALLOW_REMOTE', default=False)


def effective_jupyter_access_mode() -> str:
    explicit = env.str('API_JUPYTER_ACCESS_MODE', default='').strip().lower()
    if explicit in (ACCESS_LOCAL, ACCESS_LAN, ACCESS_NGINX):
        return explicit

    from src.config.ergo_runtime import jupyter_access_mode_from_ergo

    from_ergo = jupyter_access_mode_from_ergo()
    if from_ergo in (ACCESS_LOCAL, ACCESS_LAN, ACCESS_NGINX):
        return from_ergo

    # ERGO_JUPYTER=auto|none или API_JUPYTER_ACCESS_MODE=auto — эвристика
    if jupyter_behind_nginx():
        return ACCESS_NGINX
    if jupyter_allow_remote():
        return ACCESS_LAN
    return ACCESS_LOCAL


def get_jupyter_token() -> str:
    return env.str('API_JUPYTER_TOKEN', default='').strip()


def effective_jupyter_bind_host(default: str = 'localhost') -> str:
    explicit = env.str('API_JUPYTER_BIND_HOST', default='').strip()
    if explicit:
        return explicit

    mode = effective_jupyter_access_mode()
    if mode == ACCESS_NGINX:
        return '127.0.0.1'
    if mode == ACCESS_LAN:
        return '0.0.0.0'
    return default


def effective_jupyter_bind_port(default: str = '8002') -> str:
    explicit = env.str('API_JUPYTER_BIND_PORT', default='').strip()
    return explicit or default


def effective_jupyter_base_path() -> str:
    path = env.str('API_JUPYTER_BASE_PATH', default='/jupyter/').strip() or '/jupyter/'
    if not path.startswith('/'):
        path = f'/{path}'
    if not path.endswith('/'):
        path = f'{path}/'
    return path


def jupyter_public_base_url() -> str:
    explicit = env.str('API_JUPYTER_URL', default='').strip()
    if explicit:
        return explicit.rstrip('/')

    mode = effective_jupyter_access_mode()
    port = effective_jupyter_bind_port()

    if mode == ACCESS_NGINX:
        base = nginx_public_base_url().rstrip('/')
        path = effective_jupyter_base_path().rstrip('/')
        return f'{base}{path}'

    if mode == ACCESS_LAN:
        host = detect_lan_ip() or 'localhost'
        return f'http://{host}:{port}'

    return f'http://localhost:{port}'


def jupyter_public_lab_url() -> str:
    base = jupyter_public_base_url().rstrip('/')
    mode = effective_jupyter_access_mode()
    if mode == ACCESS_NGINX:
        return f'{base}/lab'
    return f'{base}/lab'


def effective_jupyter_security() -> dict[str, Any]:
    mode = effective_jupyter_access_mode()
    require_token = mode in (ACCESS_LAN, ACCESS_NGINX)
    require_admin_gate = mode == ACCESS_NGINX
    allow_remote = mode == ACCESS_LAN
    trust_xheaders = mode == ACCESS_NGINX and env.bool('API_JUPYTER_TRUST_XHEADERS', default=True)

    if mode == ACCESS_NGINX:
        allow_origin = nginx_public_origin()
    elif mode == ACCESS_LAN:
        allow_origin = '*'
    else:
        port = effective_jupyter_bind_port()
        allow_origin = f'http://localhost:{port}'

    return {
        'mode': mode,
        'require_token': require_token,
        'require_admin_gate': require_admin_gate,
        'allow_remote': allow_remote,
        'allow_origin': allow_origin,
        'trust_xheaders': trust_xheaders,
    }


def validate_jupyter_startup() -> str | None:
    """Возвращает текст ошибки или None, если запуск допустим."""
    mode = effective_jupyter_access_mode()

    if mode == ACCESS_NGINX and not nginx_enabled():
        return (
            'Режим nginx требует ERGO_PROXY=nginx (или явный NGINX_ENABLED=true). '
            'Проверьте корневой .env и перезапустите nginx (ergoms install-nginx).'
        )

    if mode in (ACCESS_LAN, ACCESS_NGINX) and not get_jupyter_token():
        return (
            'Для режима доступа «{mode}» задайте API_JUPYTER_TOKEN в env/jupyter.env '
            '(ergoms generate-secret) или перезапустите start-jupyter — ключ запишется сам.'.format(mode=mode)
        )

    return None


def build_jupyter_server_argv(notebooks_dir: str) -> list[str]:
    """CLI-аргументы jupyterlab из effective-настроек."""
    security = effective_jupyter_security()
    mode = security['mode']
    host = effective_jupyter_bind_host()
    port = effective_jupyter_bind_port()
    token = get_jupyter_token()

    argv: list[str] = [
        '--ip', host,
        '--port', port,
        '--notebook-dir', notebooks_dir,
        '--ServerApp.open_browser', 'False',
        '--ServerApp.websocket_ping_timeout', '30000',
        '--ServerApp.allow_remote_access', 'True' if security['allow_remote'] else 'False',
    ]

    if token:
        argv.extend(['--IdentityProvider.token', token, '--ServerApp.password', ''])
    else:
        argv.extend(['--IdentityProvider.token', '', '--ServerApp.password', ''])

    allow_origin = security.get('allow_origin', '')
    if allow_origin:
        argv.extend(['--ServerApp.allow_origin', allow_origin])

    if security.get('trust_xheaders'):
        argv.extend(['--ServerApp.trust_xheaders', 'True'])

    if mode == ACCESS_NGINX:
        argv.extend(['--ServerApp.base_url', effective_jupyter_base_path()])

    if env.bool('DOCKER_ENABLED', default=False):
        argv.append('--allow-root')

    return argv
