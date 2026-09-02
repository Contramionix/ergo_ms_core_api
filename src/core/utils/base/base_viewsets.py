"""Базовые ViewSet: JWT обязателен, Swagger-safe, lookup по public_id."""

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from src.core.settings.permissions import IsGlobalAdmin
from src.core.utils.base.base_views import AuthenticatedAPIMixin

# UUID в URL (public_id). При lookup_field не UUID — переопредели вместе с lookup_url_kwarg.
PUBLIC_ID_LOOKUP_REGEX = (
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)


class _PublicIdLookupMixin:
    lookup_field = 'public_id'
    lookup_url_kwarg = 'public_id'
    lookup_value_regex = PUBLIC_ID_LOOKUP_REGEX
    # Имя FK/поля владельца (например 'owner'). None — фильтр по владельцу не ставится.
    owner_field = None

    def get_queryset(self):
        qs = self.restrict_queryset(super().get_queryset())
        owner_field = getattr(self, 'owner_field', None)
        if not owner_field:
            return qs
        user = self.get_safe_user()
        if user is None:
            return qs
        pk = getattr(user, 'pk', None)
        if pk is None:
            return qs.none()
        lookup = owner_field if str(owner_field).endswith('_id') else f'{owner_field}_id'
        return qs.filter(**{lookup: pk})


class BaseViewSet(AuthenticatedAPIMixin, viewsets.ViewSet):
    """ViewSet с @action: токен обязателен, без lookup модели."""


class BaseGenericViewSet(AuthenticatedAPIMixin, viewsets.GenericViewSet):
    """GenericViewSet: токен обязателен. lookup — pk, пока не задан public_id."""

    def get_queryset(self):
        return self.restrict_queryset(super().get_queryset())


class BaseModelViewSet(_PublicIdLookupMixin, AuthenticatedAPIMixin, viewsets.ModelViewSet):
    """CRUD: JWT, Swagger-safe, URL по public_id. Права модуля добавляй в permission_classes."""


class BaseReadOnlyModelViewSet(
    _PublicIdLookupMixin,
    AuthenticatedAPIMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """Только чтение: JWT, Swagger-safe, URL по public_id."""


class BaseViewSetGlobalAdmin(BaseViewSet):
    """ViewSet только для глобального администратора."""

    permission_classes = [IsAuthenticated, IsGlobalAdmin]


class BaseModelViewSetGlobalAdmin(BaseModelViewSet):
    """CRUD только для глобального администратора."""

    permission_classes = [IsAuthenticated, IsGlobalAdmin]


class BaseReadOnlyModelViewSetGlobalAdmin(BaseReadOnlyModelViewSet):
    """Чтение только для глобального администратора."""

    permission_classes = [IsAuthenticated, IsGlobalAdmin]
