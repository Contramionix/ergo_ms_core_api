"""Разбор литералов JS (export default, объекты, импорты) без выполнения кода."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_IDENT_RE = re.compile(r'[A-Za-z_$][\w$]*')
_IMPORT_DEFAULT_RE = re.compile(
    r"""import\s+([A-Za-z_$][\w$]*)\s+from\s+['"]([^'"]+)['"]""",
)


class JsLiteralError(ValueError):
    """Не удалось разобрать фрагмент JS."""


def _skip_ws_and_comments(text: str, index: int) -> int:
    length = len(text)
    while index < length:
        char = text[index]
        if char in ' \t\n\r':
            index += 1
            continue
        if char == '/' and index + 1 < length:
            nxt = text[index + 1]
            if nxt == '/':
                newline = text.find('\n', index + 2)
                index = length if newline < 0 else newline + 1
                continue
            if nxt == '*':
                end = text.find('*/', index + 2)
                index = length if end < 0 else end + 2
                continue
        break
    return index


def _parse_string(text: str, index: int) -> tuple[str, int]:
    quote = text[index]
    if quote not in "'\"`":
        raise JsLiteralError('ожидалась строка')
    index += 1
    chars: list[str] = []
    length = len(text)
    while index < length:
        char = text[index]
        if char == '\\' and index + 1 < length:
            escaped = text[index + 1]
            mapping = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', "'": "'", '"': '"', '`': '`'}
            chars.append(mapping.get(escaped, escaped))
            index += 2
            continue
        if char == quote:
            return ''.join(chars), index + 1
        chars.append(char)
        index += 1
    raise JsLiteralError('незакрытая строка')


def _parse_number(text: str, index: int) -> tuple[float | int, int]:
    match = re.match(r'-?\d+(?:\.\d+)?', text[index:])
    if not match:
        raise JsLiteralError('ожидалось число')
    raw = match.group(0)
    index += len(raw)
    if '.' in raw:
        return float(raw), index
    return int(raw), index


def _skip_balanced(text: str, index: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    length = len(text)
    while index < length:
        index = _skip_ws_and_comments(text, index)
        if index >= length:
            break
        char = text[index]
        if char in "'\"`":
            _, index = _parse_string(text, index)
            continue
        if char == open_ch:
            depth += 1
            index += 1
            continue
        if char == close_ch:
            depth -= 1
            index += 1
            if depth <= 0:
                return index
            continue
        index += 1
    raise JsLiteralError('незакрытая скобка')


def _parse_member_or_ident(text: str, index: int) -> tuple[Any, int]:
    match = _IDENT_RE.match(text, index)
    if not match:
        raise JsLiteralError('ожидался идентификатор')
    name = match.group(0)
    index = match.end()
    parts = [name]
    while True:
        index = _skip_ws_and_comments(text, index)
        if index < len(text) and text[index] == '.':
            index = _skip_ws_and_comments(text, index + 1)
            nxt = _IDENT_RE.match(text, index)
            if not nxt:
                break
            parts.append(nxt.group(0))
            index = nxt.end()
            continue
        break
    if name in ('true', 'false', 'null') and len(parts) == 1:
        mapping = {'true': True, 'false': False, 'null': None}
        return mapping[name], index
    if name in ('undefined',) and len(parts) == 1:
        return None, index
    return {'__ref__': '.'.join(parts)}, index


def parse_js_value(text: str, index: int = 0) -> tuple[Any, int]:
    """Читает одно значение начиная с index."""
    index = _skip_ws_and_comments(text, index)
    if index >= len(text):
        raise JsLiteralError('пустой фрагмент')
    char = text[index]
    if char in "'\"`":
        return _parse_string(text, index)
    if char.isdigit() or (char == '-' and index + 1 < len(text) and text[index + 1].isdigit()):
        return _parse_number(text, index)
    if char == '[':
        return _parse_array(text, index)
    if char == '{':
        return _parse_object(text, index)
    if char == '(' and index + 1 < len(text) and text[index + 1] == '{':
        # ({ name: 'X' })
        value, after = _parse_object(text, index + 1)
        after = _skip_ws_and_comments(text, after)
        if after < len(text) and text[after] == ')':
            after += 1
        return value, after
    if text.startswith('function', index) or text[index:index + 2] == '=>':
        raise JsLiteralError('функции пропускаются')
    if text.startswith('async', index) or text.startswith('class', index):
        raise JsLiteralError('функции пропускаются')
    return _parse_member_or_ident(text, index)


def _parse_array(text: str, index: int) -> tuple[list[Any], int]:
    index += 1
    items: list[Any] = []
    while True:
        index = _skip_ws_and_comments(text, index)
        if index >= len(text):
            raise JsLiteralError('незакрытый массив')
        if text[index] == ']':
            return items, index + 1
        if text[index] == ',':
            index += 1
            continue
        if text[index] == '.':
            index = _skip_ws_and_comments(text, index + 3) if text.startswith('...', index) else index + 1
            index = _skip_value_or_ident(text, index)
            continue
        try:
            value, index = parse_js_value(text, index)
            items.append(value)
        except JsLiteralError:
            index = _skip_value_or_ident(text, index)
        index = _skip_ws_and_comments(text, index)
        if index < len(text) and text[index] == ',':
            index += 1


def _skip_value_or_ident(text: str, index: int) -> int:
    index = _skip_ws_and_comments(text, index)
    if index >= len(text):
        return index
    char = text[index]
    if char in "'\"`":
        _, index = _parse_string(text, index)
        return index
    if char == '{':
        return _skip_balanced(text, index, '{', '}')
    if char == '[':
        return _skip_balanced(text, index, '[', ']')
    if char == '(':
        return _skip_balanced(text, index, '(', ')')
    match = _IDENT_RE.match(text, index)
    if match:
        return match.end()
    return index + 1


def _parse_object(text: str, index: int) -> tuple[dict[str, Any], int]:
    index += 1
    result: dict[str, Any] = {}
    while True:
        index = _skip_ws_and_comments(text, index)
        if index >= len(text):
            raise JsLiteralError('незакрытый объект')
        if text[index] == '}':
            return result, index + 1
        if text[index] == ',':
            index += 1
            continue
        if text.startswith('...', index):
            index = _skip_value_or_ident(text, index + 3)
            continue
        if text[index] in "'\"`":
            key, index = _parse_string(text, index)
        elif text[index] == '[':
            index = _skip_balanced(text, index, '[', ']')
            index = _skip_ws_and_comments(text, index)
            if index < len(text) and text[index] == ':':
                index = _skip_value_or_ident(text, index + 1)
            continue
        else:
            match = _IDENT_RE.match(text, index)
            if not match:
                index += 1
                continue
            key = match.group(0)
            index = match.end()
        index = _skip_ws_and_comments(text, index)
        if index < len(text) and text[index] == ':':
            index = _skip_ws_and_comments(text, index + 1)
            try:
                if text.startswith(('function', 'async', 'class'), index) or text[index:index + 1] == '(':
                    if text[index:index + 2] != '({':
                        index = _skip_function_like(text, index)
                        continue
                value, index = parse_js_value(text, index)
                result[key] = value
            except JsLiteralError:
                index = _skip_value_or_ident(text, index)
        else:
            result[key] = {'__ref__': key}


def _skip_function_like(text: str, index: int) -> int:
    index = _skip_ws_and_comments(text, index)
    if text.startswith('async', index):
        index = _skip_ws_and_comments(text, index + 5)
    if text.startswith('function', index):
        index = _skip_ws_and_comments(text, index + 8)
        match = _IDENT_RE.match(text, index)
        if match:
            index = match.end()
    index = _skip_ws_and_comments(text, index)
    if index < len(text) and text[index] == '(':
        index = _skip_balanced(text, index, '(', ')')
    index = _skip_ws_and_comments(text, index)
    if index + 1 < len(text) and text[index:index + 2] == '=>':
        index = _skip_ws_and_comments(text, index + 2)
    if index < len(text) and text[index] == '{':
        return _skip_balanced(text, index, '{', '}')
    return _skip_value_or_ident(text, index)


def parse_imports(text: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in _IMPORT_DEFAULT_RE.finditer(text)}


def extract_export_default(text: str) -> Any:
    marker = 'export default'
    pos = text.find(marker)
    if pos < 0:
        raise JsLiteralError('нет export default')
    value, _ = parse_js_value(text, pos + len(marker))
    return value


def _lookup_ref(ref: str, namespace: dict[str, Any]) -> Any:
    parts = ref.split('.')
    current: Any = namespace
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_refs(value: Any, namespace: dict[str, Any]) -> Any:
    if isinstance(value, dict) and set(value.keys()) == {'__ref__'}:
        raw = str(value.get('__ref__') or '')
        found = _lookup_ref(raw, namespace)
        return resolve_refs(found, namespace) if found is not None else None
    if isinstance(value, dict):
        return {key: resolve_refs(item, namespace) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_refs(item, namespace) for item in value]
    return value


def load_js_export(path: Path, *, _stack: frozenset[str] | None = None) -> Any:
    """Читает export default файла и подставляет default-импорты."""
    resolved = path.resolve()
    key = str(resolved)
    stack = _stack or frozenset()
    if key in stack:
        return None
    try:
        text = resolved.read_text(encoding='utf-8')
    except OSError:
        return None
    try:
        raw = extract_export_default(text)
    except JsLiteralError:
        return None
    imports = parse_imports(text)
    namespace: dict[str, Any] = {}
    next_stack = stack | {key}
    for name, spec in imports.items():
        if spec.endswith(('.scss', '.css', '.json')):
            continue
        target = (resolved.parent / spec).resolve()
        if not target.suffix:
            candidate = target.with_suffix('.js')
            target = candidate if candidate.is_file() else target
        if not target.is_file():
            continue
        namespace[name] = load_js_export(target, _stack=next_stack)
    return resolve_refs(raw, namespace)


def flatten_locale_tree(tree: Any, *, prefix: str = '') -> dict[str, str]:
    """Вложенный объект локали → плоские ключи a.b.c."""
    flat: dict[str, str] = {}
    if not isinstance(tree, dict):
        return flat
    for key, value in tree.items():
        if not isinstance(key, str) or key.startswith('__'):
            continue
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(value, str):
            text = value.strip()
            if text:
                flat[path] = text
        elif isinstance(value, dict):
            flat.update(flatten_locale_tree(value, prefix=path))
    return flat
