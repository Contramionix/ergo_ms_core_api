"""Celery-задачи ядра: сброс исходящей очереди событий."""

from celery import shared_task

from src.core.integrations.outbox import publish_pending_outbox


@shared_task(name='core.integrations.flush_outbox')
def flush_outbox(limit: int = 100) -> int:
    return publish_pending_outbox(limit=limit)
