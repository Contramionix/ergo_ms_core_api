"""Сбор документов каталога UI для пакета knowledge/."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.config.paths import MODULES_DIR, SYSTEM_DIR

from .locales import LocaleCatalog, load_locale_catalog, iter_core_locale_files, iter_module_locale_files
from .paths import (
    core_client_src,
    iter_core_routes_files,
    iter_module_routes_files,
    module_client_dir,
)
from .render import render_screen_markdown
from .routes import parse_routes_file
from .vue_forms import fill_screen_from_vue

logger = logging.getLogger('utils.ui_catalog')

_DOC_ID_SAFE = re.compile(r'[^a-zA-Z0-9_.-]+')


def _document(*, doc_id: str, title: str, text: str, audience: str, language: str) -> dict[str, Any]:
    return {
        'id': doc_id,
        'title': title,
        'text': text,
        'audience': audience or 'user',
        'permission_key': '',
        'language': language,
    }


def _safe_doc_id(screen_id: str) -> str:
    slug = _DOC_ID_SAFE.sub('_', (screen_id or 'screen').strip()) or 'screen'
    return f'ui_catalog:{slug}'


def collect_ui_documents(
    *,
    routes_files: list[Path],
    locale_files: list[Path],
    owner: str,
    language: str = 'ru',
    system_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Собирает по одному документу на экран из переданных hook-файлов."""
    locales = load_locale_catalog(locale_files, language=language)
    if not locales:
        locales = LocaleCatalog()
    screens = []
    seen_ids: set[str] = set()
    root = Path(system_dir or SYSTEM_DIR)
    for routes_file in routes_files:
        if not routes_file.is_file():
            continue
        for screen in parse_routes_file(
            routes_file,
            locales=locales,
            owner=owner,
            system_dir=root,
        ):
            key = screen.screen_id or screen.path
            if key in seen_ids:
                continue
            seen_ids.add(key)
            fill_screen_from_vue(screen, owner=owner, locales=locales)
            if not screen.has_content():
                continue
            screens.append(screen)
    documents: list[dict[str, Any]] = []
    for screen in screens:
        documents.append(_document(
            doc_id=_safe_doc_id(screen.screen_id),
            title=screen.title,
            text=render_screen_markdown(screen, owner=owner),
            audience=screen.audience,
            language=language,
        ))
    return documents


def collect_module_ui_documents(
    module_name: str,
    *,
    language: str = 'ru',
    system_dir: Path | None = None,
) -> list[dict[str, Any]]:
    owner = (module_name or '').strip()
    if not owner:
        return []
    root = Path(system_dir or SYSTEM_DIR)
    client_dir = (root / 'modules' / owner / 'client') if system_dir else module_client_dir(owner)
    if not client_dir.is_dir():
        return []
    routes_files = iter_module_routes_files(client_dir)
    if not routes_files:
        return []
    locale_files = iter_module_locale_files(client_dir, language)
    try:
        return collect_ui_documents(
            routes_files=routes_files,
            locale_files=locale_files,
            owner=owner,
            language=language,
            system_dir=root,
        )
    except Exception:
        logger.warning('Не удалось собрать каталог UI модуля %s', owner, exc_info=True)
        return []


def collect_core_ui_documents(
    *,
    language: str = 'ru',
    system_dir: Path | None = None,
) -> list[dict[str, Any]]:
    root = Path(system_dir or SYSTEM_DIR)
    src = core_client_src(root)
    routes_files = iter_core_routes_files(src)
    if not routes_files:
        return []
    locale_files = iter_core_locale_files(src, language)
    try:
        return collect_ui_documents(
            routes_files=routes_files,
            locale_files=locale_files,
            owner='core',
            language=language,
            system_dir=root,
        )
    except Exception:
        logger.warning('Не удалось собрать каталог UI ядра', exc_info=True)
        return []


def module_has_routes(module_name: str, *, system_dir: Path | None = None) -> bool:
    owner = (module_name or '').strip()
    if not owner:
        return False
    root = Path(system_dir or SYSTEM_DIR)
    client_dir = (root / 'modules' / owner / 'client') if system_dir else MODULES_DIR / owner / 'client'
    return bool(iter_module_routes_files(client_dir))
