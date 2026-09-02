"""Публикация outbox и идемпотентный inbox."""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from src.core.integrations import bridge
from src.core.integrations.models import InboxEvent, OutboxEvent

logger = logging.getLogger('integrations.outbox')


def enqueue_outbox(event: str, payload: dict[str, Any], *, idempotency_key: str = '') -> OutboxEvent:
    """Записать событие в той же транзакции, что и доменные правки."""
    data = dict(payload or {})
    if idempotency_key:
        data.setdefault('idempotency_key', idempotency_key)
    return OutboxEvent.objects.create(event=event, payload=data)


def accept_inbox(event: str, payload: dict[str, Any]) -> bool:
    """
    True, если событие принято впервые.
    False — дубль (уже обработано).
    """
    key = str((payload or {}).get('idempotency_key') or '').strip()
    if not key:
        key = str((payload or {}).get('public_id') or '')
    if not key:
        return True
    _obj, created = InboxEvent.objects.get_or_create(
        event=event,
        idempotency_key=key,
        defaults={'payload': payload or {}},
    )
    return created


def publish_pending_outbox(*, limit: int = 100) -> int:
    """Опубликовать пачку unpublished через ModuleBridge.emit."""
    pending = list(
        OutboxEvent.objects.filter(published_at__isnull=True).order_by('created_at')[:limit]
    )
    published = 0
    for row in pending:
        try:
            with transaction.atomic():
                bridge.emit(row.event, **(row.payload or {}))
                row.published_at = timezone.now()
                row.attempts = (row.attempts or 0) + 1
                row.save(update_fields=['published_at', 'attempts'])
            published += 1
        except Exception:
            row.attempts = (row.attempts or 0) + 1
            row.save(update_fields=['attempts'])
            logger.exception('outbox publish failed id=%s event=%s', row.pk, row.event)
    return published
