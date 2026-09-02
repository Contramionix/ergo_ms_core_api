"""Синхронизация документов с Meilisearch."""

from __future__ import annotations

import logging
import threading
from typing import Any

from .client import get_meili_client, is_meili_available
from .core_indexes import register_core_indexes
from .discovery import load_module_search_indexes
from .registry import SearchIndexDefinition, all_indexes, get_index

logger = logging.getLogger('search')

_INDEX_BOOTSTRAPPED = False
_INDEX_BOOTSTRAP_LOCK = threading.Lock()


def ensure_registry_loaded() -> None:
  global _INDEX_BOOTSTRAPPED
  if _INDEX_BOOTSTRAPPED:
    return
  with _INDEX_BOOTSTRAP_LOCK:
    if _INDEX_BOOTSTRAPPED:
      return
    register_core_indexes()
    load_module_search_indexes()
    _INDEX_BOOTSTRAPPED = True


def _layout_synonyms() -> dict[str, list[str]]:
  """Пары EN↔RU для частых букв (раскладка)."""
  from src.core.utils.keyboard_layout import swap_keyboard_layout

  pairs: dict[str, set[str]] = {}
  alphabet = 'abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
  for char in alphabet:
    if not char.strip():
      continue
    swapped = swap_keyboard_layout(char)
    if swapped == char:
      continue
    pairs.setdefault(char, set()).add(swapped)
    pairs.setdefault(swapped, set()).add(char)
  return {key: sorted(values) for key, values in pairs.items() if values}


def ensure_index(defn: SearchIndexDefinition) -> None:
  client = get_meili_client()
  if client is None:
    return
  try:
    client.get_index(defn.uid)
  except Exception:
    task = client.create_index(defn.uid, {'primaryKey': defn.primary_key})
    client.wait_for_task(task.task_uid)

  index = client.index(defn.uid)
  settings: dict[str, Any] = {
    'searchableAttributes': list(defn.searchable_attributes),
    'rankingRules': list(defn.ranking_rules),
  }
  if defn.filterable_attributes:
    settings['filterableAttributes'] = list(defn.filterable_attributes)
  if defn.sortable_attributes:
    settings['sortableAttributes'] = list(defn.sortable_attributes)
  synonyms = _layout_synonyms()
  if synonyms:
    settings['synonyms'] = synonyms
  index.update_settings(settings)


def index_documents(defn: SearchIndexDefinition, documents: list[dict]) -> None:
  if not documents or not is_meili_available():
    return
  client = get_meili_client()
  if client is None:
    return
  ensure_index(defn)
  client.index(defn.uid).add_documents(documents, defn.primary_key)


def delete_documents(index_uid: str, document_ids: list[str]) -> None:
  if not document_ids or not is_meili_available():
    return
  client = get_meili_client()
  if client is None:
    return
  client.index(index_uid).delete_documents(document_ids)


def reindex_index(index_uid: str | None = None, *, batch_size: int = 500) -> dict[str, int]:
  ensure_registry_loaded()
  targets = [index_uid] if index_uid else list(all_indexes().keys())
  stats: dict[str, int] = {}
  for uid in targets:
    defn = get_index(uid)
    if not defn or not defn.get_queryset or not defn.build_document:
      continue
    if not is_meili_available(force_check=True):
      logger.warning('Meilisearch недоступен — пропуск reindex %s', uid)
      stats[uid] = 0
      continue
    ensure_index(defn)
    qs = defn.get_queryset()
    total = 0
    batch: list[dict] = []
    for obj in qs.iterator(chunk_size=batch_size):
      batch.append(defn.build_document(obj))
      if len(batch) >= batch_size:
        index_documents(defn, batch)
        total += len(batch)
        batch = []
    if batch:
      index_documents(defn, batch)
      total += len(batch)
    stats[uid] = total
    logger.info('Reindex %s: %d документов', uid, total)
  return stats
