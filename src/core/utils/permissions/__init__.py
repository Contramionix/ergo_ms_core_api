"""Права на уровне объекта — точки расширения (С8)."""

from .object_permissions import ObjectPermissionMixin, filter_queryset_for_user

__all__ = (
    'ObjectPermissionMixin',
    'filter_queryset_for_user',
)
