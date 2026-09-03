"""Каталоги i18n клиента: ключ t() → подпись."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .js_literal import flatten_locale_tree, load_js_export

_T_CALL_RE = re.compile(
    r"""\bt\(\s*['"]([A-Za-z][\w.-]*)['"]""",
)
_SUPPORTED = ('ru', 'en', 'fr')


class LocaleCatalog:
    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def resolve(self, key: str) -> str:
        text = (key or '').strip()
        if not text:
            return ''
        return self._values.get(text) or ''

    def resolve_expression(self, expression: str) -> str:
        """Склеивает все t('key') из выражения Vue/JS."""
        keys = _T_CALL_RE.findall(expression or '')
        parts = [self.resolve(key) for key in keys]
        parts = [item for item in parts if item]
        if parts:
            return ' '.join(parts)
        return ''

    def __contains__(self, key: str) -> bool:
        return key in self._values

    def __len__(self) -> int:
        return len(self._values)


def _language_slice(tree: object, language: str) -> object:
    if not isinstance(tree, dict):
        return tree
    if language in tree and isinstance(tree[language], dict):
        return tree[language]
    return tree


def load_locale_catalog(
    files: Iterable[Path],
    *,
    language: str = 'ru',
) -> LocaleCatalog:
    merged: dict[str, str] = {}
    lang = (language or 'ru').strip().lower() or 'ru'
    for path in files:
        if not path.is_file():
            continue
        tree = load_js_export(path)
        sliced = _language_slice(tree, lang)
        merged.update(flatten_locale_tree(sliced))
    return LocaleCatalog(merged)


def iter_module_locale_files(client_dir: Path, language: str = 'ru') -> list[Path]:
    lang = (language or 'ru').strip().lower() or 'ru'
    files: list[Path] = []
    hook = client_dir / 'js' / 'locales.js'
    if hook.is_file():
        files.append(hook)
    lang_file = client_dir / 'js' / 'locales' / f'{lang}.js'
    if lang_file.is_file():
        files.append(lang_file)
    lang_dir = client_dir / 'js' / 'locales' / lang
    if lang_dir.is_dir():
        files.extend(sorted(lang_dir.glob('*.js')))
    for path in sorted(client_dir.glob('*/js/locales.js')):
        files.append(path)
    return _unique_files(files)


def iter_core_locale_files(core_client_src: Path, language: str = 'ru') -> list[Path]:
    lang = (language or 'ru').strip().lower() or 'ru'
    index = core_client_src / 'i18n' / 'locales' / lang / 'index.js'
    if index.is_file():
        return [index]
    folder = core_client_src / 'i18n' / 'locales' / lang
    if folder.is_dir():
        return [path for path in sorted(folder.glob('*.js')) if path.name != 'index.js']
    return []


def _unique_files(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def supported_languages() -> tuple[str, ...]:
    return _SUPPORTED
