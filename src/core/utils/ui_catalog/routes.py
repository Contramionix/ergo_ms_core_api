"""Экраны из hook-файлов routes.js."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .js_literal import load_js_export
from .locales import LocaleCatalog
from .models import UiScreen
from .paths import resolve_component_path


def _join_paths(parent: str, child: str) -> str:
    left = (parent or '').rstrip('/')
    right = (child or '').strip()
    if not right or right == '':
        return left or '/'
    if right.startswith('/'):
        return right
    if not left:
        return f'/{right}'
    return f'{left}/{right}'


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _route_title(meta: dict[str, Any], locales: LocaleCatalog) -> str:
    title_key = str(meta.get('titleKey') or '').strip()
    if title_key:
        resolved = locales.resolve(title_key)
        if resolved:
            return resolved
    title = str(meta.get('title') or '').strip()
    return title


def _audience(meta: dict[str, Any]) -> str:
    if meta.get('requiresGlobalAdmin') or meta.get('requiresAdmin'):
        return 'admin'
    return 'user'


def _walk_routes(
    node: Any,
    *,
    parent_path: str,
    parent_title: str,
    locales: LocaleCatalog,
    from_file: Path,
    owner: str,
    screens: list[UiScreen],
    system_dir: Path | None,
) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_routes(
                item,
                parent_path=parent_path,
                parent_title=parent_title,
                locales=locales,
                from_file=from_file,
                owner=owner,
                screens=screens,
                system_dir=system_dir,
            )
        return
    data = _as_dict(node)
    if not data:
        return
    # Манифест вида { RouteName: { path, ... } }
    looks_like_map = all(
        isinstance(value, dict) and ('path' in value or 'children' in value or 'component' in value)
        for value in data.values()
    ) and 'path' not in data
    if looks_like_map:
        for name, child in data.items():
            entry = dict(child) if isinstance(child, dict) else {}
            entry.setdefault('name', name)
            _walk_routes(
                entry,
                parent_path=parent_path,
                parent_title=parent_title,
                locales=locales,
                from_file=from_file,
                owner=owner,
                screens=screens,
                system_dir=system_dir,
            )
        return

    path = _join_paths(parent_path, str(data.get('path') or ''))
    meta = _as_dict(data.get('meta'))
    title = _route_title(meta, locales) or parent_title
    name = str(data.get('name') or '').strip()
    component = str(data.get('component') or '').strip()
    if component and not data.get('redirect'):
        component_path = resolve_component_path(
            component,
            from_file=from_file,
            system_dir=system_dir,
            owner=owner,
        )
        screen_id = name or path.strip('/').replace('/', '_') or 'screen'
        screens.append(UiScreen(
            screen_id=screen_id,
            title=title or screen_id,
            path=path or '/',
            section=parent_title,
            audience=_audience(meta),
            component_path=component_path,
        ))
    children = data.get('children')
    if children:
        _walk_routes(
            children,
            parent_path=path,
            parent_title=title or parent_title,
            locales=locales,
            from_file=from_file,
            owner=owner,
            screens=screens,
            system_dir=system_dir,
        )


def parse_routes_file(
    path: Path,
    *,
    locales: LocaleCatalog,
    owner: str = '',
    system_dir: Path | None = None,
) -> list[UiScreen]:
    tree = load_js_export(path)
    screens: list[UiScreen] = []
    _walk_routes(
        tree,
        parent_path='',
        parent_title='',
        locales=locales,
        from_file=path,
        owner=owner,
        screens=screens,
        system_dir=system_dir,
    )
    return screens
