"""API мониторинга клиентов: ingest (auth) и чтение (global admin)."""

from __future__ import annotations

from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from src.core.settings.permissions import IsGlobalAdmin
from src.core.utils.base.base_views import BaseAPIViewAuthMixin
from src.core.utils.mixins import SwaggerSafeMixin

from .models import ClientMonitorSession
from .pagination import ClientMonitorPagination
from .serializers import (
    ClientMonitorEventSerializer,
    ClientMonitorSessionDetailSerializer,
    ClientMonitorSessionListSerializer,
)
from .service import (
    build_debug_pack,
    ingest_events,
    select_events_for_pack,
    split_intervals,
)


class ClientMonitorIngestView(BaseAPIViewAuthMixin):
    """POST batch событий от SPA-клиента."""

    def post(self, request: Request):
        result = ingest_events(
            user=request.user,
            session_id=request.data.get('session_id'),
            session_meta=request.data.get('session_meta'),
            events=request.data.get('events'),
        )
        if result.get('disabled'):
            return Response(status=status.HTTP_204_NO_CONTENT)
        if result.get('error'):
            return Response({'detail': result['error']}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_200_OK)


class ClientMonitorSessionViewSet(SwaggerSafeMixin, viewsets.ReadOnlyModelViewSet):
    """Список/детали сессий мониторинга — только глобальный админ."""

    permission_classes = [permissions.IsAuthenticated, IsGlobalAdmin]
    pagination_class = ClientMonitorPagination
    lookup_field = 'public_id'
    lookup_value_regex = r'[0-9a-fA-F-]{36}'

    def get_queryset(self):
        return ClientMonitorSession.objects.all().order_by('-last_event_at', '-id')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ClientMonitorSessionDetailSerializer
        return ClientMonitorSessionListSerializer

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        has_errors = request.query_params.get('has_errors')
        if has_errors in ('1', 'true', 'True'):
            qs = qs.filter(has_errors=True)
        elif has_errors in ('0', 'false', 'False'):
            qs = qs.filter(has_errors=False)

        user_ref = (request.query_params.get('user') or '').strip()
        if user_ref:
            qs = qs.filter(user_public_id=user_ref)

        date_from = parse_datetime(request.query_params.get('date_from') or '')
        date_to = parse_datetime(request.query_params.get('date_to') or '')
        if date_from:
            qs = qs.filter(last_event_at__gte=date_from)
        if date_to:
            qs = qs.filter(last_event_at__lte=date_to)

        search = (request.query_params.get('q') or request.query_params.get('search') or '').strip()
        if search:
            from src.core.search.core_indexes import INDEX_CLIENT_MONITOR
            from src.core.search.fallback import apply_ordered_ids
            from src.core.search.service import search_index

            result = search_index(
                INDEX_CLIENT_MONITOR,
                search,
                qs,
                page=1,
                page_size=10000,
            )
            qs = apply_ordered_ids(qs, result.ids) if result.ids else qs.none()

        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['get'], url_path='events')
    def events(self, request, public_id=None):
        session = self.get_object()
        qs = session.events.all().order_by('seq')
        kind = (request.query_params.get('kind') or '').strip()
        if kind:
            qs = qs.filter(kind=kind)
        page = self.paginate_queryset(qs)
        serializer = ClientMonitorEventSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['get'], url_path='intervals')
    def intervals(self, request, public_id=None):
        session = self.get_object()
        events = list(session.events.all().order_by('seq'))
        return Response({'results': split_intervals(events)})

    @action(detail=True, methods=['get'], url_path='debug-pack')
    def debug_pack(self, request, public_id=None):
        session = self.get_object()
        around_raw = request.query_params.get('around_error_id')
        around_error_id = None
        if around_raw not in (None, ''):
            try:
                around_error_id = int(around_raw)
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'around_error_id must be integer'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        interval_raw = request.query_params.get('interval_index')
        interval_index = None
        if interval_raw not in (None, ''):
            try:
                interval_index = int(interval_raw)
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'interval_index must be integer'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        mode = 'session'
        if around_error_id is not None:
            mode = 'around_error'
        elif interval_index is not None:
            mode = 'interval'
        elif request.query_params.get('from') or request.query_params.get('to'):
            mode = 'range'

        events = select_events_for_pack(
            session,
            date_from=request.query_params.get('from'),
            date_to=request.query_params.get('to'),
            around_error_id=around_error_id,
            interval_index=interval_index,
        )
        text = build_debug_pack(session, events, mode=mode)
        return Response({
            'markdown': text,
            'event_count': len(events),
            'mode': mode,
            'session_id': str(session.public_id),
        })
