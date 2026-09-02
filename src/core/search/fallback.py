"""Fallback-поиск при недоступности Meilisearch."""

from __future__ import annotations

from django.db import connection
from django.db.models import Case, IntegerField, Q, QuerySet, When

from src.core.cms.adp.services.user_search import apply_user_search

from .core_indexes import (
  INDEX_AUDIT,
  INDEX_CLIENT_MONITOR,
  INDEX_INVITATIONS,
  INDEX_MODULE_PERMISSIONS,
  INDEX_PROFILE_CHANGE,
  INDEX_ROLE_GROUPS,
  INDEX_ROLES,
  INDEX_USERS,
)
from .query import normalize_query


def _order_by_ids(queryset: QuerySet, ids: list[int | str]) -> QuerySet:
  if not ids:
    return queryset.none()
  whens = [When(pk=pk, then=pos) for pos, pk in enumerate(ids)]
  return queryset.filter(pk__in=ids).order_by(Case(*whens, output_field=IntegerField()))


def _icontains_q(fields: tuple[str, ...], token: str) -> Q:
  clause = Q()
  for field in fields:
    clause |= Q(**{f'{field}__icontains': token})
  return clause


def fallback_search(
  index_uid: str,
  query: str,
  queryset: QuerySet,
  *,
  page: int = 1,
  page_size: int = 20,
) -> tuple[list[int], int]:
  q = normalize_query(query)
  if not q:
    total = queryset.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(queryset.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_USERS:
    from src.core.cms.adp.services.user_search import build_user_token_q, tokenize_search

    filtered = queryset
    for token in tokenize_search(q):
      filtered = filtered.filter(build_user_token_q(token))
    total = filtered.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_AUDIT:
    if connection.vendor == 'postgresql':
      from django.contrib.postgres.search import TrigramSimilarity

      ranked = (
        queryset.annotate(
          search_rank=(
            TrigramSimilarity('actor_label', q)
            + TrigramSimilarity('entity_label', q)
            + TrigramSimilarity('actor__username', q)
          )
        )
        .filter(search_rank__gt=0.15)
        .order_by('-search_rank', '-created_at', '-id')
      )
    else:
      ranked = queryset.filter(
        Q(actor_label__icontains=q)
        | Q(entity_label__icontains=q)
        | Q(actor__username__icontains=q)
      ).order_by('-created_at', '-id')
    total = ranked.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(ranked.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_INVITATIONS:
    filtered = queryset.filter(
      Q(email__icontains=q)
      | Q(note__icontains=q)
      | Q(invited_by__username__icontains=q)
    ).order_by('-created_at')
    total = filtered.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_PROFILE_CHANGE:
    filtered = queryset.filter(
      Q(user__username__icontains=q)
      | Q(user__email__icontains=q)
      | Q(user__first_name__icontains=q)
      | Q(user__last_name__icontains=q)
      | Q(user__middle_name__icontains=q)
      | Q(email__icontains=q)
      | Q(first_name__icontains=q)
      | Q(last_name__icontains=q)
      | Q(middle_name__icontains=q)
      | Q(phone__icontains=q)
      | Q(comment__icontains=q)
    ).order_by('-created_at')
    total = filtered.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_CLIENT_MONITOR:
    filtered = queryset.filter(
      Q(user_label__icontains=q)
      | Q(user_public_id__icontains=q)
      | Q(user_agent__icontains=q)
      | Q(client_version__icontains=q)
    ).order_by('-last_event_at')
    total = filtered.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_ROLES:
    filtered = queryset.filter(
      Q(name__icontains=q) | Q(description__icontains=q) | Q(role_type__icontains=q)
    ).order_by('name')
    total = filtered.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_ROLE_GROUPS:
    filtered = queryset.filter(
      Q(name__icontains=q)
      | Q(description__icontains=q)
      | Q(parent_role__name__icontains=q)
    ).order_by('name')
    total = filtered.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  if index_uid == INDEX_MODULE_PERMISSIONS:
    filtered = queryset.filter(
      Q(name__icontains=q)
      | Q(resource_path__icontains=q)
      | Q(module_name__icontains=q)
      | Q(permission_key__icontains=q)
      | Q(role_group__name__icontains=q)
    ).order_by('module_name', 'permission_key')
    total = filtered.count()
    offset = max(0, (page - 1) * page_size)
    ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
    return ids, total

  filtered = queryset.filter(_icontains_q(('id',), q))
  total = filtered.count()
  offset = max(0, (page - 1) * page_size)
  ids = list(filtered.values_list('pk', flat=True)[offset:offset + page_size])
  return ids, total


def apply_ordered_ids(queryset: QuerySet, ids: list) -> QuerySet:
  return _order_by_ids(queryset, ids)
