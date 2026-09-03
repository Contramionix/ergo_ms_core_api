"""Markdown экрана для пакета knowledge/."""
from __future__ import annotations

from .models import UiField, UiScreen

_REQUIRED_LABEL = {
    'required': 'обязательно',
    'optional': 'необязательно',
    'unspecified': '',
}


def _owner_display_label(owner: str) -> str:
    name = (owner or '').strip()
    if not name or name == 'core':
        return ''
    try:
        from src.core.cms.adp.services.permission_catalog import (
            _is_slug_like_module_label,
            _resolve_module_label,
        )

        label = _resolve_module_label(name)
    except Exception:
        return ''
    if not label or _is_slug_like_module_label(name, label):
        return ''
    return label


def _field_line(field: UiField) -> str:
    bits = [field.label]
    extra: list[str] = []
    req = _REQUIRED_LABEL.get(field.required) or ''
    if req:
        extra.append(req)
    if field.hint:
        extra.append(field.hint)
    elif field.placeholder:
        extra.append(field.placeholder)
    if extra:
        bits.append(' — ')
        bits.append('. '.join(extra))
    return f'- {"".join(bits)}'


def render_screen_markdown(screen: UiScreen, *, owner: str = '') -> str:
    lines = [f'# {screen.title}', '']
    location: list[str] = []
    if screen.section and screen.section != screen.title:
        location.append(f'Раздел: {screen.section}')
    if screen.path:
        location.append(f'Путь: {screen.path}')
    if owner:
        label = _owner_display_label(owner)
        if label:
            location.append(f'Модуль: {label}')
    if location:
        lines.append('. '.join(location) + '.')
        lines.append('')
    if screen.fields:
        lines.append('Поля:')
        for field in screen.fields:
            lines.append(_field_line(field))
        lines.append('')
    if screen.buttons:
        labels = ', '.join(item.label for item in screen.buttons)
        lines.append(f'Кнопки: {labels}')
        lines.append('')
    if not screen.fields and not screen.buttons:
        lines.append('Отдельных полей формы на этом экране в разметке нет.')
        lines.append('')
    return '\n'.join(lines).strip() + '\n'
