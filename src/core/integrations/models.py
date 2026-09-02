"""Исходящая очередь и приём без дублей для событий между сервисами."""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class OutboxEvent(models.Model):
    """Исходящее событие владельца данных (транзакционный outbox)."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event = models.CharField(max_length=255, db_index=True)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'core_outbox_event'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['published_at', 'created_at'], name='core_outbox_pub_created_idx'),
        ]

    def mark_published(self) -> None:
        self.published_at = timezone.now()
        self.save(update_fields=['published_at'])


class InboxEvent(models.Model):
    """Идемпотентный приём события потребителем."""

    event = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'core_inbox_event'
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'idempotency_key'],
                name='core_inbox_event_idem_uniq',
            ),
        ]
