"""
Сценарий NGINX_ENABLED: единая точка входа, внутренние сервисы на 127.0.0.1.

Публичный адрес задаётся NGINX_PUBLIC_HOST (IP или hostname).
NGINX_SERVER_NAME — fallback и server_name в конфиге nginx.
"""

from __future__ import annotations

from src.config.env import env
from src.config.ergo_runtime import nginx_mode_enabled


def detect_lan_ip() -> str:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return ip
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith('127.'):
            return ip
    except OSError:
        pass

    return ''


def nginx_enabled() -> bool:
    return nginx_mode_enabled()


def nginx_public_host() -> str:
    explicit = env.str('NGINX_PUBLIC_HOST', default='').strip()
    if explicit:
        return explicit

    server_name = env.str('NGINX_SERVER_NAME', default='localhost').strip()
    if nginx_enabled() and server_name in ('', 'localhost', '127.0.0.1'):
        detected = detect_lan_ip()
        if detected:
            return detected

    return server_name or 'localhost'


def nginx_listen_host() -> str:
    explicit = env.str('NGINX_LISTEN_HOST', default='').strip()
    if explicit:
        return explicit
    if nginx_use_https():
        return '127.0.0.1'
    return '0.0.0.0'


def nginx_listen_port() -> str:
    return env.str('NGINX_LISTEN_PORT', default='80').strip() or '80'


def nginx_use_https() -> bool:
    if env.bool('NGINX_USE_HTTPS', default=False):
        return True
    return nginx_listen_port() == '443'


def nginx_host_policy() -> str:
    """allow | redirect | deny — см. NGINX_HOST_POLICY в .env."""
    value = env.str('NGINX_HOST_POLICY', default='allow').strip().lower()
    if value in ('allow', 'redirect', 'deny'):
        return value
    return 'allow'


def nginx_public_base_url() -> str:
    override = env.str('FRONTEND_BASE_URL', default='').strip()
    if override:
        return override.rstrip('/')

    scheme = 'https' if nginx_use_https() else 'http'
    host = nginx_public_host()
    # NGINX_LISTEN_PORT — HTTP-listener (часто :80 с редиректом); для публичных HTTPS-ссылок — TLS-порт.
    port = (
        env.str('NGINX_TLS_PORT', default='443').strip() or '443'
        if scheme == 'https'
        else nginx_listen_port()
    )
    if (scheme == 'http' and port == '80') or (scheme == 'https' and port == '443'):
        return f'{scheme}://{host}'
    return f'{scheme}://{host}:{port}'


def nginx_public_origin() -> str:
    return nginx_public_base_url()


def merge_allowed_hosts(hosts: list[str] | tuple[str, ...]) -> list[str]:
    merged = list(hosts)
    if not nginx_enabled():
        return merged
    for item in (nginx_public_host(), 'localhost', '127.0.0.1'):
        if item and item not in merged:
            merged.append(item)
    return merged


def effective_api_bind_host(default: str = 'localhost') -> str:
    if nginx_enabled():
        return env.str('API_HOST', default='127.0.0.1')
    return env.str('API_HOST', default=default)


def effective_media_public_host(default: str = 'localhost') -> str:
    if nginx_enabled():
        return env.str('MEDIA_API_HOST', default=nginx_public_host())
    return env.str('MEDIA_API_HOST', default=default)


def effective_media_public_port(default: str = '8003') -> str:
    if nginx_enabled():
        return env.str('MEDIA_API_PORT', default=nginx_listen_port())
    return env.str('MEDIA_API_BIND_PORT', default=default)


def _csv_names(raw: str) -> set[str]:
    return {part.strip() for part in (raw or '').split(',') if part.strip()}


def microservice_upload_path_prefix(target_dir: str = '') -> str:
    """Первый сегмент ``target_dir``, если это вынесенный модуль.

    Тогда браузер бьёт в ``/upload/<module>/``, и nginx ядра шлёт только
    эти файлы на хост с ``HOST_MEDIA=on``. Аватары остаются на ``/upload/``.
    """
    runtime = env.str('MODULE_RUNTIME', default='monolith').strip().lower()
    if runtime not in ('microservice', 'split'):
        return ''
    prefix = str(target_dir or '').replace('\\', '/').strip().strip('/').split('/')[0]
    if not prefix:
        return ''
    override = _csv_names(env.str('NGINX_MEDIA_UPSTREAM_MODULES', default=''))
    names = override or _csv_names(env.str('MICROSERVICE_MODULES', default=''))
    return prefix if prefix in names else ''


def media_api_public_upload_url(target_dir: str = '') -> str:
    """URL для fetch загрузки из SPA.

    За nginx страница и ``/upload/`` — один origin. Относительный путь не ломает
    CSP ``connect-src 'self'``, если сайт открыли не тем хостом, что в
    ``NGINX_PUBLIC_HOST``. Явный ``MEDIA_API_URL`` (CDN) — полный URL.
    Для ``target_dir`` модуля — ``/upload/<module>/``, не общий ``/upload/``.
    """
    prefix = microservice_upload_path_prefix(target_dir)
    suffix = f'/upload/{prefix}/' if prefix else '/upload/'
    explicit = env.str('MEDIA_API_URL', default='').strip()
    if explicit:
        return f'{explicit.rstrip("/")}{suffix}'
    if nginx_enabled():
        return suffix
    return f'{media_api_public_base_url()}{suffix}'


def media_api_public_base_url() -> str:
    """
    Публичный base URL для подписанных ссылок (/serve/, /upload/).

    Приоритет: MEDIA_API_URL (CDN) → за nginx пустая строка (тот же origin, что SPA)
    → иначе MEDIA_API_BIND_PORT на localhost.

    Не подставляем NGINX_PUBLIC_HOST: на хосте модулей это IP пира, и браузер
    уходит с публичного сайта на ``http://<ip>/``.
    """
    explicit = env.str('MEDIA_API_URL', default='').strip()
    if explicit:
        return explicit.rstrip('/')

    if nginx_enabled():
        return ''

    host = effective_media_public_host('localhost')
    port = effective_media_public_port('8003')
    protocol = env.str('MEDIA_API_PROTOCOL', default='http').strip() or 'http'
    if (protocol == 'http' and str(port) == '80') or (protocol == 'https' and str(port) == '443'):
        return f'{protocol}://{host}'
    return f'{protocol}://{host}:{port}'


def media_api_internal_base_url() -> str:
    """
    Служебный base URL core/api → media_api (MEDIA_ACCESS_MODE=remote).

    Приоритет: MEDIA_API_INTERNAL_URL → http://MEDIA_API_BIND_HOST:MEDIA_API_BIND_PORT.
    """
    explicit = env.str('MEDIA_API_INTERNAL_URL', default='').strip()
    if explicit:
        return explicit.rstrip('/')

    bind_host = env.str('MEDIA_API_BIND_HOST', default='127.0.0.1').strip() or '127.0.0.1'
    bind_port = env.str('MEDIA_API_BIND_PORT', default='8003').strip() or '8003'
    return f'http://{bind_host}:{bind_port}'


def inferred_public_origins() -> list[str]:
    """
    Публичный origin SPA из runtime, без записи в .env.

    При nginx — nginx_public_origin() (там же FRONTEND_BASE_URL, если задан).
    Без nginx — только непустой FRONTEND_BASE_URL.
    """
    if nginx_enabled():
        origin = nginx_public_origin()
        return [origin] if origin else []
    frontend = env.str('FRONTEND_BASE_URL', default='').strip().rstrip('/')
    return [frontend] if frontend else []


def effective_cors_origins(default_origins: list[str]) -> list[str]:
    origins = list(default_origins)
    for origin in inferred_public_origins():
        if origin not in origins:
            origins.append(origin)
    return origins
