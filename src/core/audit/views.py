import csv
import uuid

from django.db import connection
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from src.core.cms.adp.services.permissions import PermissionService
from src.core.utils.mixins import SwaggerSafeMixin

from . import catalog
from .dimensions import (
    get_dimensions_for_ui,
    get_read_guard_dimensions,
    get_scope_dimensions,
)
from .models import AuditEvent
from .pagination import AuditPagination
from .permissions import CanReadAuditLog
from .serializers import (
    AuditEventDetailSerializer,
    AuditEventListSerializer,
    RecordUndoSerializer,
)
from .service import AuditService


class AuditEventViewSet(SwaggerSafeMixin, viewsets.ReadOnlyModelViewSet):
    """Единый журнал действий пользователей (только чтение).

    Доступен глобальному администратору и тем, кому разрешает провайдер
    ``audit.can_read`` в пределах read_guard-измерений scope.
    Фильтры: модуль, действие / actions / exclude_actions, инициатор, важность,
    период, измерения scope, поиск по объекту/инициатору.
    """

    permission_classes = [permissions.IsAuthenticated, CanReadAuditLog]
    pagination_class = AuditPagination

    def get_permissions(self):
        # Запись отмены — любой авторизованный (toast Undo), не только читатели журнала.
        if getattr(self, 'action', None) == 'record_undo':
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return AuditEventDetailSerializer
        return AuditEventListSerializer

    def _is_global_admin(self) -> bool:
        user = getattr(self.request, 'user', None)
        return bool(user and user.is_authenticated and PermissionService.is_admin(user))

    def _read_scope_values(self) -> dict | None:
        """Значения всех read_guard-измерений из запроса для не-админа.

        Возвращает {key: value}. Если хотя бы у одного read_guard-измерения нет
        значения — возвращает None (выборка должна быть пустой).
        """
        values: dict = {}
        for dim in get_read_guard_dimensions():
            resolve = dim.get('resolve')
            value = resolve(self.request) if callable(resolve) else None
            if value in (None, ''):
                return None
            values[dim['key']] = value
        return values

    def _apply_read_scope(self, qs):
        """Обязательное ограничение выборки для не-админа по read_guard-измерениям.

        Глобальный админ видит весь журнал (опциональные фильтры — отдельно).
        Не-админ — только события своего scope; нет read_guard-измерений или их
        значений -> пустая выборка.
        """
        if self._is_global_admin():
            return qs

        read_guard = get_read_guard_dimensions()
        if not read_guard:
            return qs.none()

        scope = self._read_scope_values()
        if scope is None:
            return qs.none()

        for key, value in scope.items():
            qs = qs.filter(scope__contains={key: value})
        return qs

    def _apply_search_filter(self, qs, search: str):
        if connection.vendor == 'postgresql':
            from django.contrib.postgres.search import TrigramSimilarity

            return (
                qs.annotate(
                    search_rank=(
                        TrigramSimilarity('actor_label', search)
                        + TrigramSimilarity('entity_label', search)
                        + TrigramSimilarity('actor__username', search)
                    )
                )
                .filter(search_rank__gt=0.15)
                .order_by('-search_rank', '-created_at', '-id')
            )

        from django.db.models import Q

        return qs.filter(
            Q(actor_label__icontains=search)
            | Q(entity_label__icontains=search)
            | Q(actor__username__icontains=search)
        ).order_by('-created_at', '-id')

    @staticmethod
    def _coerce_dimension_value(raw):
        text = str(raw).strip()
        if text.lstrip('-').isdigit():
            try:
                return int(text)
            except (TypeError, ValueError):
                return text
        return text

    def _apply_dimension_filters(self, qs):
        """Опциональные фильтры по измерениям аудита из query (для всех измерений).

        Значения измерений хранятся в JSON-поле scope; фильтр использует
        containment (@>) по scope, что задействует GIN-индекс. Для не-админа
        обязательное ограничение уже наложено через _apply_read_scope; здесь —
        только сужение по переданным параметрам.
        """
        params = self.request.query_params
        for dim in get_scope_dimensions():
            raw = params.get(dim['filter_param'])
            if raw in (None, ''):
                continue
            qs = qs.filter(scope__contains={dim['key']: self._coerce_dimension_value(raw)})
        return qs

    @staticmethod
    def _parse_action_list(raw) -> list[str]:
        if not raw:
            return []
        return [part.strip() for part in str(raw).split(',') if part.strip()]

    def _apply_filters(self, qs):
        params = self.request.query_params

        source_module = params.get('source_module')
        if source_module:
            qs = qs.filter(source_module=source_module)

        include_actions = self._parse_action_list(params.get('actions'))
        exclude_actions = self._parse_action_list(params.get('exclude_actions'))
        action_value = (params.get('action') or '').strip()
        if include_actions:
            qs = qs.filter(action__in=include_actions)
        elif action_value:
            qs = qs.filter(action=action_value)
        if exclude_actions:
            qs = qs.exclude(action__in=exclude_actions)

        severity = params.get('severity')
        if severity:
            qs = qs.filter(severity=severity)

        actor_id = params.get('actor_id')
        if actor_id:
            try:
                qs = qs.filter(actor_id=int(actor_id))
            except (TypeError, ValueError):
                pass

        actor_ref = params.get('actor_ref')
        if actor_ref:
            try:
                uuid.UUID(str(actor_ref))
                qs = qs.filter(actor__public_id=actor_ref)
            except (ValueError, TypeError):
                pass

        actor_label = (params.get('actor_label') or '').strip()
        if actor_label:
            qs = qs.filter(actor_id__isnull=True, actor_label=actor_label)

        qs = self._apply_dimension_filters(qs)

        date_from = params.get('date_from')
        if date_from:
            parsed = parse_datetime(date_from) or parse_datetime(f'{date_from}T00:00:00')
            if parsed is not None:
                qs = qs.filter(created_at__gte=parsed)

        date_to = params.get('date_to')
        if date_to:
            parsed = parse_datetime(date_to) or parse_datetime(f'{date_to}T23:59:59')
            if parsed is not None:
                qs = qs.filter(created_at__lte=parsed)

        search = (params.get('q') or params.get('search') or '').strip()
        if search:
            from src.core.search.core_indexes import INDEX_AUDIT
            from src.core.search.fallback import apply_ordered_ids
            from src.core.search.service import search_index

            result = search_index(
                INDEX_AUDIT,
                search,
                qs,
                page=1,
                page_size=10000,
            )
            qs = apply_ordered_ids(qs, result.ids) if result.ids else qs.none()
        else:
            qs = qs.order_by('-created_at', '-id')

        return qs

    def get_queryset(self):
        if self.is_swagger_fake_view():
            return AuditEvent.objects.none()
        qs = AuditEvent.objects.select_related('actor').all()
        if self.action == 'list':
            # Поля для JSON-списка не нужны; source_module/action оставляем для каталога.
            qs = qs.defer(
                'changes',
                'meta',
                'user_agent',
                'request_id',
                'scope',
                'entity_type',
                'entity_ref',
            )
        qs = self._apply_read_scope(qs)
        return self._apply_filters(qs)

    def _resolve_actor_scope(self) -> dict:
        """scope для списка инициаторов: query-фильтры (админ) или read-scope."""
        if self._is_global_admin():
            scope: dict = {}
            params = self.request.query_params
            for dim in get_scope_dimensions():
                raw = params.get(dim['filter_param'])
                if raw not in (None, ''):
                    scope[dim['key']] = self._coerce_dimension_value(raw)
            return scope

        return self._read_scope_values() or {}

    @action(detail=False, methods=['get'], url_path='catalog')
    def catalog(self, request):
        """Лёгкий справочник для UI: модули, действия, важность (без инициаторов).

        Список инициаторов — отдельно в ``actors``: на 3G он тяжёлый (до 500
        записей) и не нужен для первого экрана таблицы.
        """
        return Response({
            'modules': catalog.get_modules(),
            'actions': catalog.get_flat_actions(),
            'severities': [
                {'value': value, 'label': label}
                for value, label in AuditEvent.SEVERITY_CHOICES
            ],
        })

    @action(detail=False, methods=['get'], url_path='actors')
    def actors(self, request):
        """Уникальные инициаторы для фильтра журнала (можно грузить после таблицы)."""
        return Response({
            'actors': catalog.get_distinct_actors(scope=self._resolve_actor_scope()),
        })

    @action(detail=False, methods=['get'], url_path='dimensions')
    def dimensions(self, request):
        """Расширяемые измерения аудита (scope) для фильтров UI."""
        return Response({'dimensions': get_dimensions_for_ui()})

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        """Экспорт отфильтрованного журнала в CSV."""
        qs = self.get_queryset()[:10000]
        catalog_data = catalog.get_catalog()

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="audit_log.csv"'
        response.write('\ufeff')

        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Время', 'Модуль', 'Действие', 'Важность', 'Инициатор', 'Объект', 'IP'])

        for event in qs:
            section = catalog_data.get(event.source_module or '')
            module_label = section['module_label'] if section else event.source_module
            spec = catalog.get_action_spec(event.source_module or '', event.action or '')
            action_label = spec['label'] if spec else event.action
            writer.writerow([
                event.created_at.strftime('%d.%m.%Y %H:%M:%S'),
                module_label,
                action_label,
                event.get_severity_display(),
                event.actor_label,
                event.entity_label,
                event.ip_address or '',
            ])

        return response

    @action(detail=False, methods=['post'], url_path='record-undo')
    def record_undo(self, request):
        """Записать отмену действия из toast Undo (клиентский reportUndo)."""
        serializer = RecordUndoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        kind = data['kind']
        label = data['label']
        source_module = data.get('source_module') or 'core.cms.adp'
        entity_label = data.get('entity_label') or label
        entity_type = data.get('entity_type') or 'action'
        entity_ref = data.get('entity_ref') or ''
        meta = dict(data.get('meta') or {})
        meta.setdefault('kind', kind)
        meta.setdefault('label', label)

        AuditService.record(
            action='undo.performed',
            source_module=source_module,
            actor=request.user,
            request=request,
            entity={
                'type': entity_type,
                'ref': entity_ref,
                'label': entity_label,
            },
            meta=meta,
            severity=AuditEvent.SEVERITY_INFO,
        )
        return Response({'ok': True}, status=status.HTTP_201_CREATED)
