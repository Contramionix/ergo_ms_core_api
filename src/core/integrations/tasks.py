"""Celery-задачи ядра: сброс исходящей очереди событий и пакеты справки."""

from celery import shared_task

from src.core.integrations.outbox import publish_pending_outbox


@shared_task(name='core.integrations.flush_outbox')
def flush_outbox(limit: int = 100) -> int:
    return publish_pending_outbox(limit=limit)


@shared_task(name='core.knowledge.publish_packs')
def publish_knowledge_packs() -> list[dict]:
    from src.core.utils.knowledge_pack import publish_local_knowledge_packs

    return publish_local_knowledge_packs()
