"""Подписи и списки модулей так, как их видит пользователь в интерфейсе."""
from __future__ import annotations

import re
from typing import Iterable

_API_PAREN_RE = re.compile(r'\s*[\(\[]\s*API\s*[\)\]]', re.IGNORECASE)
_VIA_API_RE = re.compile(
    r'(?:,)?\s*(?:через|via|through(?:\s+the)?)\s+API\b',
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$')
_LIST_RE = re.compile(r'^(\s*[-*]\s+)(.+)$')
_TABLE_SEP_RE = re.compile(r'^\|\s*[-:| ]+\|\s*$')
_TABLE_ROW_RE = re.compile(r'^\|\s*(.+?)\s*\|')
_MENU_PREFIX_RE = re.compile(r'^\s*-+\s*')


def sanitize_user_facing_label(label: str) -> str:
    """Убирает технический хвост вроде «(API)» из названия раздела."""
    return sanitize_user_facing_text(label)


def sanitize_user_facing_text(text: str) -> str:
    """Убирает пометки реализации: «(API)», «через API»."""
    value = text or ''
    if not value:
        return value
    value = _API_PAREN_RE.sub('', value)
    value = _VIA_API_RE.sub('', value)
    value = re.sub(r'[ \t]{2,}', ' ', value)
    value = re.sub(r' +([.,;:])', r'\1', value)
    return value.strip()


def looks_implementation_only(label: str, description: str = '') -> bool:
    """True если подпись или описание помечены как деталь реализации."""
    raw = f'{label or ""} {description or ""}'
    return bool(_API_PAREN_RE.search(raw) or _VIA_API_RE.search(raw))


def menu_item_names(menu_lines: Iterable[str] | None) -> set[str]:
    """Имена пунктов меню без маркера списка, уже без технического хвоста."""
    names: set[str] = set()
    for line in menu_lines or []:
        text = _MENU_PREFIX_RE.sub('', str(line or '')).strip()
        text = sanitize_user_facing_label(text)
        if text:
            names.add(text.casefold())
    return names


def _label_in_menu(label: str, menu: set[str]) -> bool:
    key = sanitize_user_facing_label(label).casefold()
    return bool(key) and key in menu


def _module_has_ui(module_name: str) -> bool:
    name = (module_name or '').strip()
    if not name:
        return False
    try:
        from src.core.utils.ui_catalog import module_has_routes

        return bool(module_has_routes(name))
    except Exception:
        return False


def select_user_facing_modules(
    modules: list | None,
    *,
    menu_lines: Iterable[str] | None = None,
) -> list[dict]:
    """Оставляет модули, которые пользователь видит в меню или на экранах клиента."""
    menu = menu_item_names(menu_lines)
    selected: list[dict] = []
    for raw in modules or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        name = str(item.get('name') or item.get('module_name') or '').strip()
        raw_label = str(item.get('label') or item.get('module_label') or '').strip()
        raw_desc = item.get('user_description') or ''
        if not isinstance(raw_desc, str):
            raw_desc = ''
        label = sanitize_user_facing_label(raw_label)
        description = sanitize_user_facing_text(raw_desc)
        if not label:
            continue
        in_menu = _label_in_menu(label, menu)
        has_ui = _module_has_ui(name)
        impl = looks_implementation_only(raw_label, raw_desc)
        if in_menu or has_ui:
            keep = True
        elif impl:
            keep = False
        elif menu:
            keep = False
        else:
            keep = True
        if not keep:
            continue
        item['label'] = label
        if name:
            item['name'] = name
        item['user_description'] = description
        selected.append(item)
    return selected


def drop_implementation_only_blocks(text: str, menu: set[str]) -> str:
    """Выбрасывает заголовки и строки, помеченные как API и отсутствующие в меню."""
    if not text or not menu:
        return text
    out: list[str] = []
    skipping = False
    skip_level = 0
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if skipping and level > skip_level:
                continue
            skipping = False
            skip_level = 0
            if looks_implementation_only(title, '') and not _label_in_menu(title, menu):
                skipping = True
                skip_level = level
                continue
            out.append(f'{heading.group(1)} {sanitize_user_facing_label(title)}')
            continue
        if skipping:
            continue
        listed = _LIST_RE.match(line)
        if listed:
            body = listed.group(2)
            label, _sep, rest = body.partition(':')
            if looks_implementation_only(label, rest) and not _label_in_menu(label, menu):
                continue
        if _TABLE_SEP_RE.match(line):
            out.append(line)
            continue
        table = _TABLE_ROW_RE.match(line)
        if table:
            cell = table.group(1)
            if looks_implementation_only(cell, line) and not _label_in_menu(cell, menu):
                continue
        out.append(line)
    return '\n'.join(out)


def prepare_user_facing_text(text: str, *, menu_lines: Iterable[str] | None = None) -> str:
    """Очищает справку и ответ: без (API), без разделов, которых нет в меню."""
    value = text or ''
    if not value:
        return value
    menu = menu_item_names(menu_lines) if menu_lines is not None else set()
    if menu:
        value = drop_implementation_only_blocks(value, menu)
    try:
        from src.core.cms.adp.services.permission_catalog import rewrite_slug_module_labels

        return rewrite_slug_module_labels(value)
    except Exception:
        return sanitize_user_facing_text(value)
