"""Сервис поиска: Meilisearch BM25 + fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet

from .client import get_meili_client, is_meili_available
from .fallback import apply_ordered_ids, fallback_search
from .query import expand_query_variants, normalize_query
from .registry import get_index
from .sync import ensure_registry_loaded

logger = logging.getLogger('search')


@dataclass
class SearchResult:
  ids: list
  total: int
  page: int
  page_size: int
  used_meili: bool = False


def _build_meili_filter(filters: dict[str, Any] | None) -> str | None:
  if not filters:
    return None
  parts = []
  for key, value in filters.items():
    if value in (None, ''):
      continue
    if isinstance(value, bool):
      parts.append(f'{key} = {str(value).lower()}')
    elif isinstance(value, (int, float)):
      parts.append(f'{key} = {value}')
    else:
      escaped = str(value).replace('"', '\\"')
      parts.append(f'{key} = "{escaped}"')
  return ' AND '.join(parts) if parts else None


def search_index(
  index_uid: str,
  query: str,
  queryset: QuerySet,
  *,
  page: int = 1,
  page_size: int = 20,
  filters: dict[str, Any] | None = None,
) -> SearchResult:
  page = max(1, int(page or 1))
  page_size = max(1, min(int(page_size or 20), 200))
  q = normalize_query(query)

  if not q:
    total = queryset.count()
    offset = (page - 1) * page_size
    ids = list(queryset.values_list('pk', flat=True)[offset:offset + page_size])
    return SearchResult(ids=ids, total=total, page=page, page_size=page_size, used_meili=False)

  ensure_registry_loaded()

  if is_meili_available() and get_index(index_uid):
    client = get_meili_client()
    if client is not None:
      try:
        index = client.index(index_uid)
        search_params: dict[str, Any] = {
          'limit': page_size,
          'offset': (page - 1) * page_size,
        }
        meili_filter = _build_meili_filter(filters)
        if meili_filter:
          search_params['filter'] = meili_filter

        variants = expand_query_variants(q)
        search_query = variants[0] if variants else q
        result = index.search(search_query, search_params)
        hits = result.get('hits') or []
        ids = []
        for hit in hits:
          raw_id = hit.get('id')
          if raw_id is not None:
            ids.append(int(raw_id) if str(raw_id).isdigit() else raw_id)
        total = int(
          result.get('estimatedTotalHits')
          or result.get('totalHits')
          or len(ids)
        )
        # Пустой индекс / устаревшие документы — не маскируем ORM-fallback.
        if total > 0 or ids:
          return SearchResult(
            ids=ids,
            total=total,
            page=page,
            page_size=page_size,
            used_meili=True,
          )
      except Exception:
        logger.warning(
          'Meilisearch search failed for %s — fallback',
          index_uid,
          exc_info=True,
        )

  ids, total = fallback_search(
    index_uid,
    q,
    queryset,
    page=page,
    page_size=page_size,
  )
  return SearchResult(ids=ids, total=total, page=page, page_size=page_size, used_meili=False)


def search_queryset(
  index_uid: str,
  query: str,
  queryset: QuerySet,
  **kwargs,
) -> tuple[QuerySet, SearchResult]:
  result = search_index(index_uid, query, queryset, **kwargs)
  if not result.ids:
    return queryset.none(), result
  return apply_ordered_ids(queryset, result.ids), result
