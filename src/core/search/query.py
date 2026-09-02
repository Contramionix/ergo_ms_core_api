"""Нормализация и варианты поискового запроса."""

from __future__ import annotations

from src.core.utils.keyboard_layout import search_layout_variants


def normalize_query(raw: str | None) -> str:
  """Trim и схлопывание пробелов."""
  return ' '.join((raw or '').split())


def expand_query_variants(raw: str | None) -> list[str]:
  """Варианты запроса: исходный + раскладка EN↔RU."""
  return search_layout_variants(normalize_query(raw))


def parse_list_query_param(request, *, primary: str = 'q', legacy: str = 'search') -> str:
  """Канонический параметр q с alias legacy (search)."""
  params = getattr(request, 'query_params', None) or getattr(request, 'GET', {})
  value = (params.get(primary) or params.get(legacy) or '').strip()
  return normalize_query(value)
