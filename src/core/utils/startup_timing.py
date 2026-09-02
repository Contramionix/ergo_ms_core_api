"""
Учёт wall-clock времени запуска сервисов (API, Media API, Celery).

Старт передаётся через env (time.time()), чтобы autoreload parent→child не сбрасывал
таймер. В child env очищается после чтения — повторный reload меряет только себя.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from typing import Callable, Mapping, Match, Optional, Sequence

# Cursor линкует http://localhost:8000/ в терминале и иногда открывает Browser Tab.
_LOCAL_HTTP_URL = re.compile(
    r'https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/[^\s]*)?',
    re.IGNORECASE,
)

ENV_API_START_WALL = 'ERGO_API_START_WALL'
ENV_MEDIA_START_WALL = 'ERGO_MEDIA_API_START_WALL'
# Обратная совместимость
ENV_START_WALL = ENV_API_START_WALL

_start_wall: float | None = None
_active_env_key: str = ENV_START_WALL
_ready_printed = False


def mark_start(*, env_key: str = ENV_START_WALL) -> float:
    """Отмечает начало запуска; сохраняет более раннее значение из env/памяти. Возвращает wall-start."""
    global _start_wall, _active_env_key
    _active_env_key = env_key
    now = time.time()
    env_raw = os.environ.get(env_key)
    candidates: list[float] = [now]
    if env_raw is not None:
        try:
            candidates.append(float(env_raw))
        except ValueError:
            pass
    if _start_wall is not None:
        candidates.append(_start_wall)

    _start_wall = min(candidates)
    # Parent / launcher публикует старт для дочерних процессов.
    # Child (RUN_MAIN) забирает значение и убирает env, чтобы reload стартовал с нуля.
    if os.environ.get('RUN_MAIN') == 'true':
        os.environ.pop(env_key, None)
    else:
        os.environ[env_key] = str(_start_wall)
    return _start_wall


def set_start_time_if_earlier(t: float, *, env_key: str | None = None) -> None:
    """t — wall-clock (time.time()), не perf_counter."""
    global _start_wall, _active_env_key
    key = env_key or _active_env_key
    _active_env_key = key
    if _start_wall is None or t < _start_wall:
        _start_wall = t
        if os.environ.get('RUN_MAIN') != 'true':
            os.environ[key] = str(_start_wall)


def get_elapsed() -> float:
    """Секунды с момента mark_start() (wall-clock)."""
    if _start_wall is None:
        return 0.0
    return max(0.0, time.time() - _start_wall)


def format_elapsed(elapsed: float | None = None) -> str:
    """Строка вида '4.15s' или '500ms'."""
    if elapsed is None:
        elapsed = get_elapsed()
    if elapsed < 1:
        return f'{elapsed * 1000:.0f}ms'
    return f'{elapsed:.2f}s'


def get_elapsed_str() -> str:
    """Строка вида 'in 12.34s' (ASCII для совместимости с консолью)."""
    return f'in {format_elapsed()}'


def try_print_service_ready(
    service_name: str = 'API',
    stream=None,
    *,
    elapsed: float | None = None,
) -> bool:
    """Печатает одну итоговую строку готовности. True — если напечатали."""
    global _ready_printed
    if _ready_printed:
        return False
    _ready_printed = True
    fmt = format_elapsed(elapsed)
    msg = f'>>> {service_name} готов (полное время запуска): {fmt}'
    # print — надёжнее stdout-обёрток Django и cp1252 на Windows.
    try:
        print(f'\n{msg}', flush=True)
    except Exception:
        try:
            sys.stdout.buffer.write(f'\n{msg}\n'.encode('utf-8', errors='replace'))
            sys.stdout.buffer.flush()
        except Exception:
            logging.getLogger('startup_timing').info('%s', msg)
    return True


def try_print_api_ready(stream=None, *, reason: str = '') -> bool:
    """Совместимость: готовность API."""
    return try_print_service_ready('API', stream)


def hide_local_http_urls(text: str) -> str:
    """Пишет localhost-адрес без схемы, чтобы IDE не открывала встроенный браузер."""

    def _replace(match: Match[str]) -> str:
        rest = match.group(0).split('://', 1)[1].rstrip('/')
        if '/' in rest:
            authority, path = rest.split('/', 1)
            path = '/' + path
        else:
            authority, path = rest, ''
        if authority.startswith('[') and ']:' in authority:
            host, port = authority.rsplit(']:', 1)
            host = f'{host}]'
        elif ':' in authority:
            host, port = authority.rsplit(':', 1)
        else:
            return f'{authority}{path}'
        return f'{host}, port {port}{path}'

    return _LOCAL_HTTP_URL.sub(_replace, text)


def is_listen_ready_message(text: str) -> bool:
    """Daphne: Listening on TCP…"""
    return 'Listening on' in text


def is_dev_server_start_message(text: str) -> bool:
    """Starting … development server (WSGI Django или Daphne ASGI) — идёт в stdout команды."""
    lower = text.lower()
    return 'starting' in lower and 'development server' in lower


def is_wsgi_start_ready_message(text: str) -> bool:
    """Классический Django runserver (без Daphne/ASGI)."""
    if not is_dev_server_start_message(text):
        return False
    lower = text.lower()
    return 'asgi' not in lower and 'daphne' not in lower


def is_server_ready_message(text: str) -> bool:
    """Media/API: Listening (Daphne) или Starting development server."""
    return is_listen_ready_message(text) or is_dev_server_start_message(text)


class StreamReadyWrapper:
    """Обёртка stdout: готовность по Starting development server (WSGI и Daphne)."""

    def __init__(self, stream, service_name: str = 'API'):
        self._stream = stream
        self._service_name = service_name

    def write(self, data: str = '', style_func=None, ending=None, *args, **kwargs):
        if isinstance(data, str):
            data = hide_local_http_urls(data)
        inner = self._stream.write
        try:
            result = inner(data, style_func=style_func, ending=ending, *args, **kwargs)
        except TypeError:
            result = inner(data)
        if is_dev_server_start_message(data):
            try_print_service_ready(self._service_name, self._stream)
        return result

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class ListeningReadyHandler(logging.Handler):
    """Запасной путь: Listening из logger daphne (если Starting не прошёл через stdout)."""

    def __init__(self, service_name: str = 'API', stream=None) -> None:
        super().__init__(level=logging.INFO)
        self._service_name = service_name
        self._stream = stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        if is_listen_ready_message(msg):
            try_print_service_ready(self._service_name, self._stream)


_DAPHNE_READY_LOGGERS = ('daphne.server', 'daphne')


def install_listening_ready_handler(
    service_name: str = 'API',
    stream=None,
) -> ListeningReadyHandler:
    handler = ListeningReadyHandler(service_name, stream=stream)
    for name in _DAPHNE_READY_LOGGERS:
        logging.getLogger(name).addHandler(handler)
    return handler


def remove_listening_ready_handler(handler: ListeningReadyHandler | None) -> None:
    if handler is None:
        return
    for name in _DAPHNE_READY_LOGGERS:
        logging.getLogger(name).removeHandler(handler)


def run_process_with_ready_timing(
    cmd: Sequence[str],
    *,
    cwd: str,
    env: Optional[Mapping[str, str]] = None,
    service_name: str,
    is_ready_line: Callable[[str], bool] = is_server_ready_message,
    start_wall: float | None = None,
) -> int:
    """Popen + проброс stdout; одна строка готовности при совпадении is_ready_line."""
    if start_wall is None:
        start_wall = _start_wall if _start_wall is not None else time.time()
    run_env = dict(env) if env is not None else os.environ.copy()
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=run_env,
    )
    try:
        if proc.stdout:
            for line in proc.stdout:
                print(line, end='')
                if is_ready_line(line):
                    try_print_service_ready(
                        service_name,
                        elapsed=max(0.0, time.time() - start_wall),
                    )
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    return proc.returncode or 0
