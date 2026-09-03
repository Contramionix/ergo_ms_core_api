"""Поля и кнопки из Vue SFC рядом с маршрутом."""
from __future__ import annotations

import re
from pathlib import Path

from .locales import LocaleCatalog
from .models import Requirement, UiButton, UiField, UiScreen
from .paths import is_same_owner_vue, resolve_component_path

_TEMPLATE_RE = re.compile(r'<template\b[^>]*>(.*)</template>', re.I | re.S)
_SCRIPT_RE = re.compile(r'<script\b[^>]*>(.*)</script>', re.I | re.S)
_FORM_FIELD_RE = re.compile(
    r'<(FormField|SettingsCardRow)\b([^>]*)>(.*?)</\1>',
    re.I | re.S,
)
_ATTR_RE = re.compile(
    r'''(?:^|\s)(:?[A-Za-z][\w-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''',
)
_T_CALL_RE = re.compile(r"""\bt\(\s*['"]([A-Za-z][\w.-]*)['"]""")
_VUE_IMPORT_RE = re.compile(
    r"""import\s+\w+\s+from\s+['"]([^'"]+\.vue)['"]""",
)
_BUTTON_RE = re.compile(
    r'<(button|router-link|a)\b([^>]*)>(.*?)</\1>',
    re.I | re.S,
)
_LABEL_PROP_RE = re.compile(
    r"""\b(label|placeholder|hint)\s*:\s*t\(\s*['"]([A-Za-z][\w.-]*)['"]""",
)
_I18N_LITERAL_RE = re.compile(
    r"""['"]((?:settings|routes|common|admin|[a-z_]+)\.[A-Za-z][\w.-]+)['"]""",
)
_INPUT_REQUIRED_RE = re.compile(r'<(input|textarea|select)\b[^>]*\brequired\b', re.I)
_MAX_DEPTH = 3
_SKIP_BUTTON = frozenset({
    '',
    '...',
    '×',
    'x',
})
_LABEL_KEY_HINT = re.compile(
    r'(label|title|placeholder|hint|firstName|lastName|middleName|email|phone|'
    r'bio|submit|cancel|create|save|nameLabel)$',
    re.I,
)


def _attrs(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw or ''):
        name = match.group(1)
        value = match.group(2) if match.group(2) is not None else (
            match.group(3) if match.group(3) is not None else (match.group(4) or '')
        )
        result[name] = value
    return result


def _clean_text(value: str) -> str:
    text = re.sub(r'\{\{.*?\}\}', ' ', value or '', flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _resolve_attr(attrs: dict[str, str], name: str, locales: LocaleCatalog) -> str:
    bound = attrs.get(f':{name}') or attrs.get(f'v-bind:{name}') or ''
    if bound:
        from_t = locales.resolve_expression(bound)
        if from_t:
            return from_t
        stripped = bound.strip()
        if (stripped.startswith("'") and stripped.endswith("'")) or (
            stripped.startswith('"') and stripped.endswith('"')
        ):
            return stripped[1:-1].strip()
    literal = attrs.get(name) or ''
    if literal:
        from_t = locales.resolve_expression(literal)
        return from_t or literal.strip()
    return ''


def _requirement(attrs: dict[str, str], inner: str) -> Requirement:
    if 'optional' in attrs and attrs.get('optional') != 'false':
        return 'optional'
    if 'required' in attrs and attrs.get(':required') != 'false':
        bound = attrs.get(':required') or attrs.get('v-bind:required') or ''
        if bound.strip() in ('false', '0'):
            return 'optional'
        return 'required'
    if _INPUT_REQUIRED_RE.search(inner or ''):
        return 'required'
    return 'unspecified'


def _fields_from_template(template: str, locales: LocaleCatalog) -> list[UiField]:
    fields: list[UiField] = []
    seen: set[str] = set()
    for match in _FORM_FIELD_RE.finditer(template or ''):
        attrs = _attrs(match.group(2))
        inner = match.group(3) or ''
        label = _resolve_attr(attrs, 'label', locales)
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        fields.append(UiField(
            label=label,
            required=_requirement(attrs, inner),
            hint=_resolve_attr(attrs, 'hint', locales),
            placeholder=_first_placeholder(inner, locales),
        ))
    return fields


def _first_placeholder(inner: str, locales: LocaleCatalog) -> str:
    match = re.search(
        r''':?placeholder\s*=\s*(?:"([^"]*)"|'([^']*)')''',
        inner or '',
        re.I,
    )
    if not match:
        return ''
    raw = match.group(1) if match.group(1) is not None else (match.group(2) or '')
    return locales.resolve_expression(raw) or raw.strip()


def _buttons_from_template(template: str, locales: LocaleCatalog) -> list[UiButton]:
    buttons: list[UiButton] = []
    seen: set[str] = set()
    for match in _BUTTON_RE.finditer(template or ''):
        attrs = _attrs(match.group(2))
        classes = f"{attrs.get('class') or ''} {attrs.get(':class') or ''}"
        tag = (match.group(1) or '').lower()
        is_button = tag == 'button' or 'btn' in classes or 'ui-btn' in classes
        if not is_button:
            continue
        inner = match.group(3) or ''
        label = locales.resolve_expression(inner)
        if not label:
            label = _clean_text(inner)
        if not label:
            label = _resolve_attr(attrs, 'aria-label', locales) or _resolve_attr(
                attrs, 'title', locales,
            )
        label = re.sub(r'\s+', ' ', label).strip()
        if not label or label.casefold() in _SKIP_BUTTON or len(label) > 80:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        buttons.append(UiButton(label=label))
    return buttons


def _fields_from_script(script: str, locales: LocaleCatalog) -> list[UiField]:
    fields: list[UiField] = []
    seen: set[str] = set()
    for match in _LABEL_PROP_RE.finditer(script or ''):
        key = match.group(2)
        label = locales.resolve(key)
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        if match.group(1) != 'label':
            continue
        fields.append(UiField(label=label))
    for match in _I18N_LITERAL_RE.finditer(script or ''):
        key = match.group(1)
        if not _LABEL_KEY_HINT.search(key.split('.')[-1]):
            continue
        label = locales.resolve(key)
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        fields.append(UiField(label=label))
    return fields


def _vue_imports(script: str, from_file: Path, *, owner: str) -> list[Path]:
    found: list[Path] = []
    for match in _VUE_IMPORT_RE.finditer(script or ''):
        spec = match.group(1)
        if spec.startswith('@/components/'):
            continue
        target = resolve_component_path(spec, from_file=from_file, owner=owner)
        if target is None or not target.is_file():
            continue
        if not is_same_owner_vue(target, owner=owner):
            continue
        found.append(target)
    return found


def extract_from_vue(
    path: Path,
    locales: LocaleCatalog,
    *,
    owner: str = '',
    depth: int = 0,
    seen: set[str] | None = None,
) -> tuple[list[UiField], list[UiButton]]:
    visited = seen if seen is not None else set()
    key = str(path.resolve())
    if key in visited or depth > _MAX_DEPTH:
        return [], []
    visited.add(key)
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return [], []
    template_match = _TEMPLATE_RE.search(raw)
    script_match = _SCRIPT_RE.search(raw)
    template = template_match.group(1) if template_match else ''
    script = script_match.group(1) if script_match else ''
    fields = _fields_from_template(template, locales)
    fields.extend(_fields_from_script(script, locales))
    buttons = _buttons_from_template(template, locales)
    for child in _vue_imports(script, path, owner=owner):
        child_fields, child_buttons = extract_from_vue(
            child,
            locales,
            owner=owner,
            depth=depth + 1,
            seen=visited,
        )
        fields.extend(child_fields)
        buttons.extend(child_buttons)
    return _dedupe_fields(fields), _dedupe_buttons(buttons)


def _dedupe_fields(fields: list[UiField]) -> list[UiField]:
    result: list[UiField] = []
    seen: set[str] = set()
    for item in fields:
        key = item.label.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_buttons(buttons: list[UiButton]) -> list[UiButton]:
    result: list[UiButton] = []
    seen: set[str] = set()
    for item in buttons:
        key = item.label.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def fill_screen_from_vue(screen: UiScreen, *, owner: str, locales: LocaleCatalog) -> None:
    if screen.component_path is None or not screen.component_path.is_file():
        return
    fields, buttons = extract_from_vue(screen.component_path, locales, owner=owner)
    screen.fields = fields
    screen.buttons = buttons
