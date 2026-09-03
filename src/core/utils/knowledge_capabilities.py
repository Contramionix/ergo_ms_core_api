"""Меню и каталог модулей для справки: всегда с процесса ядра."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.core.cms.adp.services.permission_catalog import get_modules_catalog
from src.core.cms.adp.services.permissions import PermissionService
from src.core.utils.module_registry import get_microservice_modules


def _resolve_user(*, user_public_id=None):
    if not user_public_id:
        return None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(public_id=user_public_id).first()
    if user is None or not user.is_active:
        return None
    return user


def flatten_menu_lines(nodes: list, *, depth: int = 0, limit: int = 200) -> list[str]:
    lines: list[str] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        name = (node.get('name') or '').strip()
        if name:
            lines.append(f'{"  " * depth}- {name}')
        children = node.get('children') or []
        if children:
            lines.extend(flatten_menu_lines(children, depth=depth + 1, limit=limit))
        if len(lines) >= limit:
            return lines[:limit]
    return lines


def _all_active_menu_tree() -> list[dict[str, Any]]:
    from src.core.cms.adp.menu.models import MenuItem

    items = list(
        MenuItem.objects.filter(is_active=True).order_by('order', 'name').only(
            'id',
            'parent_id',
            'name',
            'order',
        )
    )
    children: dict[int | None, list] = defaultdict(list)
    for item in items:
        children[item.parent_id].append(item)

    def walk(parent_id) -> list[dict[str, Any]]:
        nodes = []
        for item in children.get(parent_id, []):
            nodes.append({
                'name': item.name,
                'children': walk(item.id),
            })
        return nodes

    return walk(None)


def _slug_label(module_name: str) -> str:
    return module_name.replace('-', ' ').replace('_', ' ').strip().title()


def collect_module_entries(*, user=None, is_admin: bool, full: bool) -> list[dict[str, str]]:
    catalog = list(get_modules_catalog(include_disabled=False) or [])
    seen = {
        str(item.get('module_name') or '').strip()
        for item in catalog
        if item.get('module_name')
    }
    for name in sorted(get_microservice_modules()):
        if name in seen:
            continue
        catalog.append({
            'module_name': name,
            'module_label': _slug_label(name),
            'user_description': '',
            'disabled': False,
        })
        seen.add(name)

    allowed_names: set[str] | None = None
    if not full and not is_admin and user is not None:
        payload = PermissionService.get_user_permissions(user)
        allowed_names = set()
        for perm in payload.get('module_permissions') or []:
            name = getattr(perm, 'module_name', None)
            if name is None and isinstance(perm, dict):
                name = perm.get('module_name')
            if name:
                allowed_names.add(str(name))

    entries: list[dict[str, str]] = []
    for item in catalog:
        if item.get('disabled'):
            continue
        name = str(item.get('module_name') or '').strip()
        label = (item.get('module_label') or name or '').strip()
        if not name or not label:
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        description = item.get('user_description') or ''
        entries.append({
            'name': name,
            'label': label,
            'user_description': description.strip() if isinstance(description, str) else '',
        })
    return entries


def build_user_capabilities(
    *,
    user=None,
    full: bool = False,
    session_claims=None,
) -> dict[str, Any]:
    is_admin = bool(user is not None and PermissionService.is_admin(user))
    if full:
        menu_tree = _all_active_menu_tree()
        modules = collect_module_entries(user=user, is_admin=True, full=True)
        is_admin = True
    else:
        from src.core.cms.adp.menu.user_menu_builder import build_user_menu_items

        menu_tree = build_user_menu_items(user, session_claims=session_claims) if user else []
        modules = collect_module_entries(user=user, is_admin=is_admin, full=False)

    return {
        'is_admin': is_admin,
        'menu_lines': flatten_menu_lines(menu_tree),
        'modules': modules,
    }


def user_capabilities_op(*, user_public_id=None, full: bool = False, session_claims=None, **_):
    user = _resolve_user(user_public_id=user_public_id)
    if not full and user is None:
        return None
    claims = session_claims if isinstance(session_claims, dict) else None
    return build_user_capabilities(user=user, full=bool(full), session_claims=claims)


def site_overview_documents() -> list[dict[str, Any]]:
    """Документы полного меню и каталога для пакета ядра."""
    try:
        payload = build_user_capabilities(full=True)
    except Exception:
        return []

    documents: list[dict[str, Any]] = []
    menu_lines = payload.get('menu_lines') or []
    if menu_lines:
        documents.append({
            'id': 'site_menu',
            'title': 'Разделы системы (меню)',
            'text': (
                '# Разделы системы (боковое меню)\n\n'
                'Карта разделов ERGO MS, которые пользователь видит в боковом меню.\n\n'
                + '\n'.join(menu_lines)
            ),
            'audience': 'user',
            'permission_key': '',
            'language': 'ru',
        })
    modules = payload.get('modules') or []
    if modules:
        lines = [
            '# Возможности и модули системы',
            '',
            'Установленные модули ERGO MS и их назначение для пользователя.',
            '',
        ]
        for item in modules:
            lines.append(f'## {item["label"]}')
            lines.append('')
            description = item.get('user_description') or ''
            lines.append(description or 'Модуль установлен.')
            lines.append('')
        documents.append({
            'id': 'installed_modules',
            'title': 'Модули и возможности системы',
            'text': '\n'.join(lines),
            'audience': 'user',
            'permission_key': '',
            'language': 'ru',
        })
    return documents
