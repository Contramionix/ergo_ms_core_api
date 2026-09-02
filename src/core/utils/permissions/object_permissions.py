"""Object-level permission hooks (security audit С8).

Точки расширения для ViewSet/APIView и permission-классов DRF.
Дефолт — запрет. Новое представление обязано переопределить
``check_object_permission`` и/или сузить queryset.
"""

from __future__ import annotations

from typing import Any


class ObjectPermissionMixin:
    """
    Миксин / база для object-scoped проверок.

    Подклассы обязаны переопределить ``check_object_permission`` и/или
    фильтровать queryset через ``filter_queryset_for_user``.
    Без переопределения доступ к объекту запрещён.
    """

    def check_object_permission(self, request: Any, obj: Any) -> bool:
        """Override for object scope. Default denies access."""
        return False

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        """
        DRF BasePermission-compatible entry.

        Delegates to ``check_object_permission(request, obj)``.
        When used as a ViewSet mixin, ``view`` is typically ``self``.
        """
        checker = getattr(view, 'check_object_permission', None)
        if view is not None and view is not self and callable(checker):
            return checker(request, obj)
        return self.check_object_permission(request, obj)

    def check_object_permissions(self, request: Any, obj: Any) -> None:
        """ViewSet hook: DRF permissions, затем object-scope миксина."""
        super().check_object_permissions(request, obj)
        if not self.check_object_permission(request, obj):
            self.permission_denied(request, message='Нет доступа к объекту.')


def filter_queryset_for_user(queryset: Any, user: Any) -> Any:
    """
    Extension point: restrict queryset to objects the user may see.

    Default returns an empty queryset when ``none()`` exists.
    Call from ``get_queryset`` after adopting ObjectPermissionMixin.
    """
    none = getattr(queryset, 'none', None)
    if callable(none):
        return none()
    return queryset
