"""
Утилиты токенизированного поиска пользователей.

Каждое слово из строки поиска должно совпасть хотя бы с одним из полей (AND между словами, OR между полями).
Для списков с Meilisearch используйте src.core.search.service.search_queryset.
"""

from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()

DEFAULT_USER_SEARCH_FIELDS = (
    'username',
    'email',
    'first_name',
    'last_name',
    'middle_name',
)


def tokenize_search(search: str) -> list[str]:
    normalized = (search or '').strip()
    if not normalized:
        return []
    return [token for token in normalized.split() if token]


def _unicode_icontains_variants(token: str) -> tuple[str, ...]:
    """Варианты регистра для ORM icontains.

    PostgreSQL с collation C сворачивает в ILIKE только ASCII; Python
    корректно обрабатывает кириллицу.
    """
    if not token:
        return ()
    variants = {
        token,
        token.lower(),
        token.upper(),
        token.casefold(),
        token.capitalize(),
        token.title(),
    }
    return tuple(variants)


def build_user_token_q(
    token: str,
    *,
    prefix: str = '',
    fields: tuple[str, ...] = DEFAULT_USER_SEARCH_FIELDS,
    extra_fields: tuple[str, ...] = (),
) -> Q:
    token_filter = Q()
    variants = _unicode_icontains_variants(token)
    for field in fields:
        for variant in variants:
            token_filter |= Q(**{f'{prefix}{field}__icontains': variant})
    for field in extra_fields:
        for variant in variants:
            token_filter |= Q(**{f'{field}__icontains': variant})
    return token_filter


def apply_user_search(
    queryset,
    search: str,
    *,
    prefix: str = '',
    fields: tuple[str, ...] = DEFAULT_USER_SEARCH_FIELDS,
    extra_fields: tuple[str, ...] = (),
):
    tokens = tokenize_search(search)
    if not tokens:
        return queryset

    if prefix or extra_fields or fields != DEFAULT_USER_SEARCH_FIELDS:
        for token in tokens:
            queryset = queryset.filter(
                build_user_token_q(
                    token,
                    prefix=prefix,
                    fields=fields,
                    extra_fields=extra_fields,
                )
            )
        return queryset

    from src.core.search.core_indexes import INDEX_USERS
    from src.core.search.fallback import apply_ordered_ids
    from src.core.search.service import search_index

    result = search_index(INDEX_USERS, search, queryset, page=1, page_size=100000)
    if not result.ids:
        return queryset.none()
    return apply_ordered_ids(queryset, result.ids)


def apply_last_name_letter_filter(queryset, letter: str, *, prefix: str = ''):
    """Фильтр по первой букве фамилии (AlphabetFilter).

    Варианты регистра нужны для кириллицы на PostgreSQL с collation C.
    """
    normalized = (letter or '').strip()
    if not normalized:
        return queryset

    # Одна буква (или короткий префикс); берём первый символ после trim.
    char = normalized[0]
    field = f'{prefix}last_name'
    letter_filter = Q()
    for variant in _unicode_icontains_variants(char):
        letter_filter |= Q(**{f'{field}__istartswith': variant})
        letter_filter |= Q(**{f'{field}__startswith': variant})
    return queryset.filter(letter_filter)


def build_user_search_q(
    search: str,
    *,
    prefix: str = '',
    fields: tuple[str, ...] = DEFAULT_USER_SEARCH_FIELDS,
    extra_fields: tuple[str, ...] = (),
) -> Q:
    tokens = tokenize_search(search)
    if not tokens:
        return Q()

    combined = Q()
    for token in tokens:
        combined &= build_user_token_q(
            token,
            prefix=prefix,
            fields=fields,
            extra_fields=extra_fields,
        )
    return combined


def resolve_user_by_search(search: str, *, queryset=None):
    normalized = (search or '').strip()
    if not normalized:
        return None

    base_qs = queryset if queryset is not None else User.objects.all()

    exact = base_qs.filter(username=normalized).first()
    if exact:
        return exact

    return apply_user_search(base_qs, normalized).first()
