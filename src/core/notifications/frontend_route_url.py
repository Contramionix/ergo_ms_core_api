"""Сборка абсолютного URL клиента из Vue-route уведомления (без pk в query)."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

logger = logging.getLogger('core.notifications')

_PARAM_RE = re.compile(r':([A-Za-z_][\w]*)(\?)?')
INBOX_PATH = '/user/notifications'


def _normalize_base(base_url: str) -> str:
    return (base_url or '').rstrip('/')


def join_frontend_path(base_url: str, path: str) -> str:
    """Склеить FRONTEND_BASE_URL и путь клиента (/crm/tasks/...)."""
    base = _normalize_base(base_url)
    if not path:
        return base
    if not path.startswith('/'):
        path = f'/{path}'
    if not base:
        return path
    return urljoin(f'{base}/', path.lstrip('/'))


def fill_vue_path(template: str, params: dict | None) -> str | None:
    """Подставить params в шаблон Vue Router (/crm/tasks/:taskId?)."""
    if not template:
        return None
    values = params if isinstance(params, dict) else {}

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        optional = match.group(2) == '?'
        raw = values.get(name)
        if raw is None or raw == '':
            if optional:
                return ''
            raise KeyError(name)
        return str(raw).strip('/')

    try:
        filled = _PARAM_RE.sub(_replace, template)
    except KeyError:
        return None

    while '//' in filled:
        filled = filled.replace('//', '/')
    filled = filled.rstrip('/') or '/'
    if not filled.startswith('/'):
        filled = f'/{filled}'
    return filled


def build_frontend_url_from_route(route, *, base_url: str, path_index: dict | None = None) -> str | None:
    """route {name, params} → абсолютный URL или None, если имя неизвестно."""
    if not isinstance(route, dict):
        return None
    name = (route.get('name') or '').strip()
    if not name:
        return None

    if path_index is None:
        try:
            from src.core.cms.client_routes_cache import get_client_route_name_index

            path_index = get_client_route_name_index()
        except Exception:
            logger.exception('Не удалось загрузить индекс клиентских маршрутов')
            return None

    template = (path_index or {}).get(name)
    if not template:
        return None
    filled = fill_vue_path(template, route.get('params'))
    if not filled:
        return None
    return join_frontend_path(base_url, filled)


def inbox_url(base_url: str) -> str:
    return join_frontend_path(base_url, INBOX_PATH)
