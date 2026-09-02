"""Парсинг query-параметров списков с поиском."""

from __future__ import annotations

from .query import parse_list_query_param


def parse_search_pagination(request, *, default_page_size: int = 12, max_page_size: int = 200):
  params = getattr(request, 'query_params', None) or getattr(request, 'GET', {})
  try:
    page = int(params.get('page') or 1)
  except (TypeError, ValueError):
    page = 1
  try:
    page_size = int(params.get('page_size') or default_page_size)
  except (TypeError, ValueError):
    page_size = default_page_size
  page = max(1, page)
  page_size = max(1, min(page_size, max_page_size))
  q = parse_list_query_param(request)
  return page, page_size, q
