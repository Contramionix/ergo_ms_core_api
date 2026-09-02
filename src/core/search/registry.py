"""Реестр поисковых индексов ядра и модулей."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from django.db.models import QuerySet

DocumentBuilder = Callable[[Any], dict[str, Any]]
QuerysetProvider = Callable[[], QuerySet]


@dataclass(frozen=True)
class SearchIndexDefinition:
  uid: str
  primary_key: str = 'id'
  searchable_attributes: tuple[str, ...] = ()
  filterable_attributes: tuple[str, ...] = ()
  sortable_attributes: tuple[str, ...] = ()
  build_document: DocumentBuilder | None = None
  get_queryset: QuerysetProvider | None = None
  ranking_rules: tuple[str, ...] = (
    'words',
    'typo',
    'proximity',
    'attribute',
    'sort',
    'exactness',
  )


_REGISTRY: dict[str, SearchIndexDefinition] = {}


def register_index(defn: SearchIndexDefinition) -> None:
  _REGISTRY[defn.uid] = defn


def get_index(uid: str) -> SearchIndexDefinition | None:
  return _REGISTRY.get(uid)


def all_indexes() -> dict[str, SearchIndexDefinition]:
  return dict(_REGISTRY)


def clear_registry_for_tests() -> None:
  _REGISTRY.clear()
