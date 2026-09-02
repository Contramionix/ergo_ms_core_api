"""Discovery search_indexes.py из модулей."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from .registry import SearchIndexDefinition, register_index

logger = logging.getLogger('search')


def _coerce_definition(raw: dict[str, Any]) -> SearchIndexDefinition | None:
  uid = (raw.get('uid') or '').strip()
  if not uid:
    return None
  build_document = raw.get('build_document')
  get_queryset = raw.get('get_queryset')
  if not callable(build_document) or not callable(get_queryset):
    return None
  return SearchIndexDefinition(
    uid=uid,
    primary_key=str(raw.get('primary_key') or 'id'),
    searchable_attributes=tuple(raw.get('searchable_attributes') or ()),
    filterable_attributes=tuple(raw.get('filterable_attributes') or ()),
    sortable_attributes=tuple(raw.get('sortable_attributes') or ()),
    build_document=build_document,
    get_queryset=get_queryset,
    ranking_rules=tuple(raw.get('ranking_rules') or SearchIndexDefinition.ranking_rules),
  )


def _module_search_index_paths() -> list[str]:
  from src.config.settings.base import DJANGO_CORE_DIR, MODULES_DIR
  from src.core.utils.module_registry import (
    is_module_loadable_in_process,
    is_valid_module_dir_name,
  )

  candidates: list[str] = []
  modules_root = Path(MODULES_DIR)
  if modules_root.is_dir():
    for module_dir in modules_root.iterdir():
      if not module_dir.is_dir() or not is_valid_module_dir_name(module_dir.name):
        continue
      if not is_module_loadable_in_process(module_dir.name):
        continue
      api_dir = module_dir / 'api'
      if not api_dir.is_dir():
        continue
      for search_file in api_dir.rglob('search_indexes.py'):
        rel = search_file.relative_to(api_dir)
        parts = rel.with_suffix('').parts
        if not parts or parts[-1] != 'search_indexes':
          continue
        nested = '.'.join(parts[:-1])
        suffix = f'.{nested}' if nested else ''
        candidates.append(f'modules.{module_dir.name}.api{suffix}.search_indexes')

  core_root = Path(DJANGO_CORE_DIR)
  if core_root.is_dir():
    for search_file in core_root.rglob('search_indexes.py'):
      rel = search_file.relative_to(core_root)
      parts = rel.with_suffix('').parts
      if not parts or parts[-1] != 'search_indexes':
        continue
      nested = '.'.join(parts[:-1])
      suffix = f'.{nested}' if nested else ''
      candidates.append(f'src.core{suffix}.search_indexes')
  return candidates


def load_module_search_indexes() -> None:
  for module_path in _module_search_index_paths():
    try:
      mod = importlib.import_module(module_path)
    except ImportError:
      continue
    except Exception:
      logger.warning('Ошибка загрузки search_indexes из %s', module_path, exc_info=True)
      continue

    entries = getattr(mod, 'SEARCH_INDEXES', None)
    if not entries:
      continue
    if not isinstance(entries, (list, tuple)):
      continue
    for raw in entries:
      if not isinstance(raw, dict):
        continue
      defn = _coerce_definition(raw)
      if defn:
        register_index(defn)
