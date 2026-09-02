"""
HTTP-опрос GET /api/system/ready/ после старта API.

Используется оркестрацией (start-all, службы ОС, healthcheck Docker)
и скриптами запуска клиента/nginx — клиент ждёт ready перед Vite/nginx.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from _common import format_console


def _configure_stdio_utf8() -> None:
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    os.environ.setdefault('PYTHONUTF8', '1')
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)


def _log(level: str, message: str) -> None:
    print(format_console(level, message), flush=True)


def _default_ready_url() -> str:
    host = os.environ.get('API_HOST', '127.0.0.1').strip() or '127.0.0.1'
    # Bind-адрес 0.0.0.0 / :: нельзя использовать как URL для опроса с хоста.
    if host in ('0.0.0.0', '::', '[::]'):
        host = '127.0.0.1'
    port = os.environ.get('API_PORT', '8000').strip() or '8000'
    return f'http://{host}:{port}/api/system/ready/'


def _endpoint_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or '127.0.0.1'
    if host in ('0.0.0.0', '::', '[::]'):
        host = '127.0.0.1'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    return host, int(port)


def _tcp_open(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_ready(url: str) -> tuple[str, dict | None]:
    """
    Возвращает (status, payload):
    - ready — ready=true
    - maintenance — API отвечает 503 с code=maintenance (процесс жив)
    - not_ready — HTTP 503 / ready=false
    - http_error — другой HTTP-код
    - unreachable — сеть / таймаут / не JSON
    """
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            raw = response.read().decode('utf-8')
            payload = json.loads(raw)
            if payload.get('ready') is True:
                return 'ready', payload
            return 'not_ready', payload
    except urllib.error.HTTPError as exc:
        body = ''
        try:
            body = exc.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        payload = None
        if body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
        if payload and payload.get('code') == 'maintenance':
            return 'maintenance', payload
        if exc.code == 503:
            return 'not_ready', payload
        return 'http_error', payload
    except Exception:
        return 'unreachable', None


def wait_until_api_ready(
    *,
    url: str | None = None,
    timeout_sec: float | None = None,
    interval_sec: float | None = None,
) -> int:
    """Ждёт ready=true или ответ обслуживания. 0 — успех, 1 — таймаут."""
    _configure_stdio_utf8()

    if os.environ.get('ERGO_SKIP_API_READY_WAIT', '').strip().lower() in ('1', 'true', 'yes'):
        _log('skip', 'Ожидание API ready пропущено (ERGO_SKIP_API_READY_WAIT)')
        return 0

    ready_url = (url or os.environ.get('ERGO_READY_URL', _default_ready_url())).strip()
    timeout = float(
        timeout_sec
        if timeout_sec is not None
        else os.environ.get('ERGO_READY_TIMEOUT', '180')
    )
    interval = float(
        interval_sec
        if interval_sec is not None
        else os.environ.get('ERGO_READY_INTERVAL', '0.5')
    )
    deadline = time.monotonic() + timeout
    host, port = _endpoint_host_port(ready_url)
    last_status = ''
    last_progress = 0.0
    saw_tcp = False
    reported_checks = False

    _log('info', f'Ожидание готовности API: {ready_url} (таймаут {timeout:.0f} с)')

    while time.monotonic() < deadline:
        tcp_up = _tcp_open(host, port)
        if tcp_up and not saw_tcp:
            saw_tcp = True
            _log('info', f'Порт {host}:{port} открыт, ждём ready=true…')

        if not tcp_up:
            status, payload = 'unreachable', None
        else:
            status, payload = _probe_ready(ready_url)

        if status == 'ready':
            _log('ok', f'API готов: {ready_url}')
            return 0

        if status == 'maintenance':
            _log(
                'ok',
                f'API отвечает, включён режим технических работ: {ready_url}',
            )
            return 0

        if status == 'not_ready' and payload and not reported_checks:
            checks = payload.get('checks')
            if checks:
                _log('info', f'API отвечает, но ещё не ready: {checks}')
                reported_checks = True

        now = time.monotonic()
        if status != last_status or (now - last_progress) >= 5.0:
            remaining = max(0.0, deadline - now)
            if status == 'unreachable' and not tcp_up:
                _log('info', f'API ещё не слушает {host}:{port}… осталось {remaining:.0f} с')
            elif status == 'unreachable':
                _log('info', f'Порт открыт, но /ready/ недоступен… осталось {remaining:.0f} с')
            elif status == 'not_ready':
                _log('info', f'API загружается (ready=false)… осталось {remaining:.0f} с')
            else:
                _log('info', f'Ответ /ready/ неожиданный… осталось {remaining:.0f} с')
            last_status = status
            last_progress = now

        time.sleep(interval)

    if saw_tcp:
        _log(
            'warning',
            f'API на {host}:{port} отвечает, но ready=true не получен за {timeout:.0f} с — продолжаем',
        )
    else:
        _log(
            'warning',
            f'API не ответил на {ready_url} за {timeout:.0f} с — продолжаем без ожидания',
        )
    return 1


def main() -> int:
    return wait_until_api_ready()


if __name__ == '__main__':
    sys.exit(main())
